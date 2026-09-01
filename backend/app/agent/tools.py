"""Domain tools exposed to the deep agent (bound to a per-job AgentContext)."""

import json
import re
from dataclasses import dataclass, field

import httpx
from langchain_core.tools import tool

from ..config import settings
from ..db import SessionLocal
from ..models.lead import Lead
from ..services import dedupe, nominatim, overpass, scoring, searxng, verify
from ..services import website as website_svc
from ..worker.queue import update_progress


@dataclass
class AgentContext:
    discovery_id: int
    user_id: int
    job_id: int
    category: str
    location: str
    industry: str | None
    keywords: list[str]
    exclude_keywords: list[str]
    num_leads: int
    country_code: str | None = None  # e.g. "IN"; inferred from the location

    # Telemetry the worker uses to detect a premature STOP: whether the agent
    # actually saw real candidates and how much conversion work it attempted.
    searched_categories: set[str] = field(default_factory=set)
    saw_candidates: bool = False
    save_attempts: int = 0
    # Whether web search (SearXNG) has produced any candidate this run; used by
    # `progress` to warn when an endpoint is configured but silently failing
    # (public instances commonly block/rate-limit the JSON API).
    web_results_seen: bool = False
    # Countries the run has been geo-scoped to, recorded from successful
    # `search_businesses` geocodes. The save gate rejects a candidate whose
    # country contradicts these, so a run scoped to New Zealand can't silently
    # accumulate Mumbai businesses from an un-scoped web query.
    target_countries: set[str] = field(default_factory=set)

    # Loop guards. These live on the context object (not in the agent's
    # conversation), so they survive conversation compaction: compaction wipes
    # the agent's working memory every few turns, which is exactly what let the
    # old runs re-search the same category and re-fetch the same website in a
    # loop. Recording seen work here makes those repeats visible to the tools,
    # which can then refuse to redo them.
    last_search: dict[str, int] = field(default_factory=dict)  # category -> last limit
    fetched_urls: set[str] = field(default_factory=set)  # canonical URLs already fetched
    fetch_cache: dict[str, dict] = field(default_factory=dict)  # url -> last result
    research_cache: dict[str, dict] = field(default_factory=dict)  # name+loc -> lookup result


def _json(obj) -> str:
    return json.dumps(obj, ensure_ascii=False, default=str)


def _canonical_url(url: str) -> str:
    """Default the scheme so http://x and https://x hit the same cache slot."""
    if not url.startswith(("http://", "https://")):
        return "https://" + url
    return url


# SME-friendly supported categories the agent should sweep to reach the "at
# least 5 categories" target (used by ``progress`` to suggest next steps).
_SWEEP_CATEGORIES = [
    "restaurant",
    "cafe",
    "hotel",
    "salon",
    "gym",
    "spa",
    "travel",
    "real estate",
    "clinic",
    "dental",
    "beauty",
    "bakery",
    "school",
    "bar",
]


# Placeholder/fabricated data patterns the agent must never save as a real lead.
_PLACEHOLDER_DOMAINS = {
    "example.com", "example.org", "example.net", "example.edu", "example.io",
    "yourdomain.com", "mydomain.com", "test.com", "placeholder.com",
    "domain.com", "localhost",
}
_PLACEHOLDER_NAME_WORDS = ("example", "test", "dummy", "placeholder", "sample", "foo", "bar")


def _digits(value: str | None) -> str:
    return "".join(ch for ch in (value or "") if ch.isdigit())


# Common country name/ISO aliases, normalized to one canonical key. Used by the
# save-time location gate so "NZ" matches "New Zealand", "USA" matches
# "United States", and so on. Unknown strings fall back to their lowercased,
# stripped form (so "India" matches "india").
_COUNTRY_CANONICAL = {
    "us": "united states", "usa": "united states", "u.s.a": "united states",
    "u.s.a.": "united states", "u s": "united states", "america": "united states",
    "united states of america": "united states",
    "uk": "united kingdom", "u.k.": "united kingdom", "u.k": "united kingdom", "u k": "united kingdom",
    "britain": "united kingdom", "great britain": "united kingdom", "england": "united kingdom",
    "nz": "new zealand", "aotearoa": "new zealand",
    "uae": "united arab emirates", "united arab emirates": "uae",
    "gb": "united kingdom", "in": "india", "ca": "canada", "au": "australia",
    "sg": "singapore", "de": "germany", "fr": "france", "es": "spain",
}


def _canonical_country(country: str | None) -> str | None:
    """Normalize a country name/ISO code to a stable key for comparisons."""
    if not country:
        return None
    key = " ".join(str(country).lower().split()).rstrip(".")
    return _COUNTRY_CANONICAL.get(key, key)


def _country_aliases(value: str | None) -> set[str]:
    """All canonical countries a string names.

    Nominatim sometimes returns a combined country like "New Zealand / Aotearoa"
    or "United States (US)"; each slash-separated name is an equivalent, so the
    matcher treats them as one country rather than rejecting a "New Zealand"
    candidate against a "New Zealand / Aotearoa" target.
    """
    if not value:
        return set()
    parts = re.split(r"[/|]", str(value))
    return {canon for canon in (_canonical_country(p) for p in parts) if canon}


def _country_matches(country: str | None, target_countries: set[str]) -> bool:
    """True when a candidate's country is one of the run's target countries."""
    mine = _country_aliases(country)
    if not mine:
        return False
    return any(mine & _country_aliases(t) for t in target_countries)


def _sequential_run(digits: str) -> bool:
    for i in range(len(digits) - 5):
        window = [int(ch) for ch in digits[i:i + 6]]
        if all(window[j + 1] == window[j] + 1 for j in range(5)):
            return True
        if all(window[j + 1] == window[j] - 1 for j in range(5)):
            return True
    return False


def _looks_fabricated(name, website, email, phone) -> str | None:
    """Return a reason string if a lead looks like placeholder/fabricated data."""
    if name:
        norm = dedupe.normalize_name(name)
        for word in _PLACEHOLDER_NAME_WORDS:
            if word in norm:
                return f"name '{name}' looks like placeholder data"
    if website:
        domain = dedupe.domain_from_url(website)
        if domain and domain in _PLACEHOLDER_DOMAINS:
            return f"website '{website}' is a placeholder domain"
    if email and "@" in email:
        email_domain = email.rsplit("@", 1)[1].lower()
        if email_domain in _PLACEHOLDER_DOMAINS or email_domain.startswith("example."):
            return f"email '{email}' is a placeholder address"
    phone_digits = _digits(phone)
    if phone_digits:
        if len(set(phone_digits)) == 1:
            return f"phone '{phone}' is a repetitive placeholder"
        if _sequential_run(phone_digits):
            return f"phone '{phone}' is a sequential placeholder"
    return None


def _within_bbox(candidate: dict, bbox: tuple) -> bool:
    """True when a candidate's coords fall inside an Overpass (s,w,n,e) bbox.

    Handles a bbox that wraps the antimeridian (west > east): a point is inside
    when its longitude is west of the split OR east of it. Candidates without
    coordinates (e.g. web results) are kept — they carry no geometry to check.
    """
    lat = candidate.get("lat")
    lon = candidate.get("lon")
    if lat is None or lon is None:
        return True
    south, west, north, east = bbox
    if not (south <= lat <= north):
        return False
    if west <= east:
        return west <= lon <= east
    return lon >= west or lon <= east


def _merge_candidates(osm_results: list[dict], web_results: list[dict], limit: int) -> list[dict]:
    """Merge Overpass/Nominatim and SearXNG web candidates, de-duplicated.

    Sources are interleaved 50/50 (round-robin, OSM first) so web results are
    never starved out when Overpass already saturates ``limit`` — each source
    gets equal representation up to the limit when both have candidates.
    Duplication is judged by website domain, falling back to normalized name;
    a web result whose domain matches an OSM candidate is dropped. If one
    source runs out, the remaining slots go to the other.
    """
    seen_domains: set[str] = set()
    seen_names: set[str] = set()

    def _unique(candidates: list[dict]) -> list[dict]:
        out: list[dict] = []
        for candidate in candidates:
            domain = dedupe.domain_from_url(candidate.get("website"))
            norm = dedupe.normalize_name(candidate.get("name") or "")
            if domain:
                if domain in seen_domains:
                    continue
                seen_domains.add(domain)
            elif norm:
                if norm in seen_names:
                    continue
                seen_names.add(norm)
            out.append(candidate)
        return out

    osm_unique = _unique(osm_results)
    web_unique = _unique(web_results)

    merged: list[dict] = []
    oi = wi = 0
    while oi < len(osm_unique) or wi < len(web_unique):
        if oi < len(osm_unique):
            merged.append(osm_unique[oi])
            oi += 1
            if len(merged) >= limit:
                break
        if wi < len(web_unique):
            merged.append(web_unique[wi])
            wi += 1
            if len(merged) >= limit:
                break
    return merged


def build_tools(ctx: AgentContext) -> list:
    @tool
    def search_businesses(
        category: str | None = None, location: str | None = None, limit: int = 12
    ) -> str:
        """Discover candidate businesses for a category and location.

        Args:
            category: the business category to search (e.g. "Dental Clinics").
                Defaults to the discovery's lead type. When it maps to an
                Overpass/OSM tag, OSM results are returned (merged with web
                results if SearXNG is configured); otherwise the tool returns
                the supported list — unless web search is enabled, in which
                case it searches the web for the category instead.
            location: the place to search in (e.g. "Bengaluru, India").
                Defaults to the discovery's target location. Required when the
                discovery was created from a natural-language brief — derive
                it from the brief for each call.
            limit: maximum number of candidates to return (default 12). Kept
                small: each result lands in the agent's context, which is
                budgeted against the model's per-minute token cap. The agent
                can search a category again if it needs more candidates.

        Returns:
            A JSON list of candidate businesses with name, category, website,
            phone, email, address, city, country, lat, lon, source, source_id.
            OSM results are merged with SearXNG web-search results when
            ``SEARXNG_ENDPOINT`` is configured; web candidates have a website
            (and possibly snippet-extracted contacts) but no coordinates yet.
        """
        cat = category or ctx.category
        loc = location or ctx.location
        if not loc:
            return (
                "No location given. Derive the target location from the "
                "discovery brief and pass it in the `location` argument."
            )
        tags = overpass.category_to_tags(cat)
        # Single-category gate: for a run whose ICP is one concrete vertical
        # (e.g. "Dental Clinics"), searching any OTHER mapped category only
        # returns candidates that the save gate would discard. Refuse up front
        # and point the agent back at the target category instead of burning
        # calls on categories that cannot produce a shortlist.
        target_tags = overpass.category_to_tags(ctx.category)
        if target_tags is not None and tags is not None and tags != target_tags:
            ctx.searched_categories.add(cat)
            ctx.last_search[cat] = limit
            target_name = overpass.canonical_category(ctx.category)
            return (
                f"Category '{cat}' does not match this run's single-category ICP "
                f"('{ctx.category}'); only '{target_name or ctx.category}' businesses "
                f"qualify. Re-search the target category with a larger `limit` or a "
                f"different `location` to find more candidates — off-target categories "
                f"are discarded at save time."
            )
        web_enabled = bool(settings.searxng_endpoint)
        # Loop guard: searching the same category with the same limit returns the
        # same top-N, so re-running it just burns turns (and TPM). Nudge the
        # agent to a different category, or a larger `limit` for more results.
        if ctx.last_search.get(cat) == limit:
            return (
                f"Already searched '{cat}' (limit={limit}); the results would be identical. "
                f"Search a different category, or pass a larger `limit` if you want more "
                f"candidates for '{cat}'."
            )
        ctx.last_search[cat] = limit

        # Upstream (Overpass/Nominatim/SearXNG) is flaky. Catch HTTP errors at
        # each step instead of raising: langgraph's default tool-error handler
        # re-raises and kills the whole agent run. Each source falls through on
        # failure and the web path is skipped entirely when it's not configured.
        # When ``enable_osm`` is false the OSM/Nominatim path is skipped
        # altogether and every search is web-only (SearXNG).
        osm_results: list[dict] = []
        search_error: httpx.HTTPError | None = None
        if tags is not None and settings.enable_osm:
            geo = None
            try:
                geo = nominatim.geocode(loc)
            except httpx.HTTPError as exc:
                search_error = exc
            if geo:
                # Pin the run's geo-scope: the country this location resolves to
                # becomes the target the save gate validates candidates against.
                if geo.get("country"):
                    ctx.target_countries.add(geo["country"])
                elif geo.get("country_code"):
                    ctx.target_countries.add(geo["country_code"])
                # Prefer the location's OSM polygon (area) when the geocoder
                # returned an administrative relation. A country such as New
                # Zealand has a bounding box that wraps the antimeridian
                # (lon −179..+179, i.e. "nearly the whole world"), which makes a
                # raw bbox query return candidates from the wrong countries. The
                # area filter scopes to the real polygon. When the area query
                # itself returns nothing we do NOT retry with that world-spanning
                # bbox; the nominatim text fallback below is properly geo-scoped.
                # The bbox path is only used when no area exists, and its results
                # are filtered back into the geocoded box afterwards.
                area_id = None
                if geo.get("osm_type") == "relation" and geo.get("osm_id"):
                    area_id = 3600000000 + int(geo["osm_id"])
                bbox = geo.get("boundingbox")
                try:
                    if area_id:
                        osm_results = overpass.search_businesses(
                            cat, bbox=bbox, area_id=area_id, limit=limit
                        )
                    elif bbox:
                        osm_results = overpass.search_businesses(cat, bbox=bbox, limit=limit)
                        osm_results = [c for c in osm_results if _within_bbox(c, bbox)]
                except httpx.HTTPError as exc:
                    search_error = exc
            if not osm_results:
                try:
                    osm_results = nominatim.search_places(f"{cat} {loc}", limit=limit)
                except httpx.HTTPError as exc:
                    search_error = exc
            # Telemetry: remember this search so the worker can tell a premature
            # STOP (agent bailed after seeing real candidates) from a genuinely
            # empty region.
            ctx.searched_categories.add(cat)
            if osm_results:
                ctx.saw_candidates = True
        elif not web_enabled:
            if tags is None:
                return (
                    f"Unsupported category '{cat}'. "
                    f"Supported categories: {overpass.supported_categories()}"
                )
            return (
                f"No data source is enabled for '{cat}': OSM/Nominatim is disabled "
                "(ENABLE_OSM=false) and web search is unavailable (no SEARXNG_ENDPOINT). "
                "Enable SEARXNG_ENDPOINT or ENABLE_OSM."
            )

        # Location-guard fallback for web-only mode: geocode is skipped when OSM
        # is disabled, so pin the run's target country from the location string
        # instead. Keeps the save-time location gate (rejecting off-country
        # candidates) active even when every search is web-only.
        if not settings.enable_osm:
            cc = verify.infer_country_code(loc)
            if cc:
                ctx.target_countries.add(cc)

        web_results: list[dict] = []
        if web_enabled:
            web_results = searxng.search_web(f"{cat} {loc}", category=cat, limit=limit)
            ctx.searched_categories.add(cat)
            if web_results:
                ctx.saw_candidates = True
                ctx.web_results_seen = True

        results = _merge_candidates(osm_results, web_results, limit)
        if not results:
            if search_error is not None and not web_results:
                return (
                    f"No candidates found; search failed "
                    f"({type(search_error).__name__}: {search_error}). "
                    f"Try again, search a smaller area, or move on."
                )
            return "No candidates found for this category/location."
        return _json(results)

    @tool
    def fetch_website(url: str) -> str:
        """Fetch a business website and extract contact points.

        Args:
            url: the business website URL (e.g. https://example.com).

        Returns:
            JSON with url, title, emails (list), phones (list), and socials (map).

        Note:
            Results are cached per URL: re-fetching a website you already fetched
            returns the cached result instead of crawling it again.
        """
        key = _canonical_url(url)
        if key in ctx.fetch_cache:
            return _json(
                {
                    **ctx.fetch_cache[key],
                    "cached": True,
                    "note": "Already fetched this website earlier in the run. Use the "
                    "emails/phones below instead of re-fetching.",
                }
            )
        data = website_svc.fetch_website(url)
        ctx.fetched_urls.add(key)
        ctx.fetch_cache[key] = data
        return _json(data)


    def _persist_lead(
        name: str,
        website: str | None = None,
        email: str | None = None,
        phone: str | None = None,
        city: str | None = None,
        country: str | None = None,
        category: str | None = None,
        contact_name: str | None = None,
        contact_role: str | None = None,
        address: str | None = None,
        lat: float | None = None,
        lon: float | None = None,
        source: str = "agent",
        source_id: str | None = None,
        score: float | None = None,
        reason: str | None = None,
        what_to_sell: str | None = None,
        fit_score: float | None = None,
        fit_reason: str | None = None,
    ) -> str:
        """Verify, score, deduplicate and persist a single lead.

        Shared by the ``save_lead`` tool and ``verify_contact`` (which calls this
        directly once a contact verifies, so a verified candidate is saved in one
        tool call instead of depending on a second, separate ``save_lead`` call).

        ``score`` (0-100) and ``reason`` are the LLM's sole fit judgment;
        ``what_to_sell`` is the pitch angle for this lead. ``fit_score``/
        ``fit_reason`` remain as back-compat aliases for older callers.
        """
        ctx.save_attempts += 1
        reason = reason or fit_reason
        if score is None:
            score = fit_score

        # Single-category gate: when the discovery names one concrete vertical
        # (e.g. "Dental Clinics" → amenity=denist), every saved lead must belong
        # to exactly that tag-set. Off-target or unclassifiable categories are
        # discarded so a narrow run can never collect e.g. bakeries or GP clinics.
        target_tags = overpass.category_to_tags(ctx.category)
        lead_cat = category or ctx.category
        if target_tags is not None and overpass.category_to_tags(lead_cat) != target_tags:
            return (
                f"Discarded: category '{lead_cat}' does not match this run's "
                f"single-category ICP ('{ctx.category}'). Only save {ctx.category} "
                "businesses; search the target category (raise `limit` or change "
                "`location`) for more candidates instead."
            )
        if target_tags is not None:
            category = overpass.canonical_category(ctx.category) or ctx.category

        # Location gate: a lead whose country contradicts where the run has been
        # geo-scoped is off-target. ``ctx.target_countries`` is populated by
        # ``search_businesses`` from the geocoded search location(s), so it
        # reflects the actual scope of this run (not the agent's prose). A
        # candidate that carries no country can't be checked — OSM candidates
        # are already inside the geocoded area, and bare web candidates still
        # need enrichment before they can be saved with a country at all.
        if country and ctx.target_countries and not _country_matches(country, ctx.target_countries):
            return (
                f"Discarded: country '{country}' does not match the run's target "
                f"location(s) ({', '.join(sorted(ctx.target_countries))}). Only save "
                "businesses in the target location; search there instead."
            )

        fab = _looks_fabricated(name, website, email, phone)
        if fab:
            return f"Discarded: looks fabricated ({fab})."

        email_verified = bool(email and verify.verify_email(email)["verified"])
        phone_check = verify.validate_phone(phone, ctx.country_code)
        phone_norm = phone_check["normalized"]
        phone_valid = phone_check["valid"]

        lead = {
            "name": name,
            "website": website,
            "email": email,
            "phone": phone,
            "category": category or ctx.category,
            "contact_name": contact_name,
            "verified": email_verified,
        }

        if scoring.is_excluded(lead, ctx.exclude_keywords):
            return "Discarded: matches an exclude keyword."

        # A lead is saved when the email is verified OR the phone is valid. A
        # website is NOT required. Say exactly which contact point failed so the
        # agent can fix it instead of re-trying blindly.
        if not email_verified and not phone_valid:
            reasons = []
            if not email:
                reasons.append("no email")
            elif not email_verified:
                reasons.append(f"email '{email}' failed format/MX verification")
            if not phone:
                reasons.append("no phone")
            elif not phone_valid:
                reasons.append(
                    f"phone '{phone}' did not normalize to a valid number "
                    f"({phone_check['reason']})"
                )
            return (
                "Discarded: no verified contact point (" + " and ".join(reasons) + "). "
                "A lead is saved when the email is verified OR the phone is valid. "
                "Pass the contact data via `verify_contact` to see exactly what verifies."
            )

        if score is not None:
            score = max(0.0, min(100.0, round(float(score), 2)))
        score, breakdown = scoring.score_lead(lead, ctx.keywords, fit_score=score)
        key = dedupe.make_dedupe_key(name, website)

        db = SessionLocal()
        try:
            if key:
                existing = (
                    db.query(Lead)
                    .filter(Lead.discovery_id == ctx.discovery_id, Lead.dedupe_key == key)
                    .first()
                )
                if existing:
                    return f"Duplicate (already saved): {name}"

            row = Lead(
                discovery_id=ctx.discovery_id,
                user_id=ctx.user_id,
                name=name,
                category=category or ctx.category,
                industry=ctx.industry,
                address=address,
                city=city,
                country=country,
                lat=lat,
                lon=lon,
                website=website,
                email=email,
                phone=phone_norm or phone,
                contact_name=contact_name,
                contact_role=contact_role,
                source=source,
                source_id=source_id,
                score=score,
                score_breakdown=breakdown,
                fit_score=score,
                fit_reason=reason,
                what_to_sell=what_to_sell,
                verified=email_verified,
                verified_method="mx" if email_verified else ("phone_format" if phone_valid else None),
                status="shortlisted",
                dedupe_key=key,
                raw={"phone_input": phone} if phone else {},
            )
            db.add(row)
            db.commit()
            total = (
                db.query(Lead)
                .filter(Lead.discovery_id == ctx.discovery_id, Lead.status == "shortlisted")
                .count()
            )
            update_progress(ctx.job_id, {"stage": "discovery", "lead_count": total})
            return (
                f"Saved ({total}/{ctx.num_leads}): {name} "
                f"score={score} verified={'email' if email_verified else 'phone'}"
            )
        finally:
            db.close()

    @tool
    def save_lead(
        name: str,
        website: str | None = None,
        email: str | None = None,
        phone: str | None = None,
        city: str | None = None,
        country: str | None = None,
        category: str | None = None,
        contact_name: str | None = None,
        contact_role: str | None = None,
        address: str | None = None,
        lat: float | None = None,
        lon: float | None = None,
        source: str = "agent",
        source_id: str | None = None,
        score: float | None = None,
        reason: str | None = None,
        what_to_sell: str | None = None,
        fit_score: float | None = None,
        fit_reason: str | None = None,
    ) -> str:
        """Verify, score, deduplicate and persist a single lead.

        Args:
            name: the business name (required).
            website: business website URL.
            email: contact email (verified via format + MX lookup).
            phone: contact phone (format-validated; OSM multi-value strings like
                "+91-93343-18675;+91-93347-42008" and "00" country codes are handled).
            city: business city.
            country: business country.
            category: business category.
            contact_name: name of the contact person.
            contact_role: role of the contact person.
            address: full street address.
            lat: latitude.
            lon: longitude.
            source: data source label (default "agent").
            source_id: source record id.
            score: 0-100 LLM judgment of how well this business fits the ICP
                (higher = a stronger lead). This IS the final lead score.
            reason: one-line plain-English explanation of why this lead is a
                good fit (e.g. the local SME category and why it would buy).
            what_to_sell: what to sell this lead — the specific offering,
                service, or outreach angle for this business.
            fit_score, fit_reason: back-compat aliases for ``score`` and
                ``reason``.

        Returns:
            A status string: shortlisted (with score and progress) or discarded (with reason).
        """
        return _persist_lead(
            name,
            website=website,
            email=email,
            phone=phone,
            city=city,
            country=country,
            category=category,
            contact_name=contact_name,
            contact_role=contact_role,
            address=address,
            lat=lat,
            lon=lon,
            source=source,
            source_id=source_id,
            score=score,
            reason=reason,
            what_to_sell=what_to_sell,
            fit_score=fit_score,
            fit_reason=fit_reason,
        )

    @tool
    def progress() -> str:
        """Report discovery progress vs the target.

        Returns:
            Leads collected so far, categories already searched, and which
            supported categories remain — so you know what to sweep next.
        """
        db = SessionLocal()
        try:
            total = (
                db.query(Lead)
                .filter(Lead.discovery_id == ctx.discovery_id, Lead.status == "shortlisted")
                .count()
            )
        finally:
            db.close()
        searched = ", ".join(sorted(ctx.searched_categories)) or "none"
        msg = (
            f"{total}/{ctx.num_leads} leads collected. "
            f"Categories searched so far ({len(ctx.searched_categories)}): {searched}."
        )
        target_tags = overpass.category_to_tags(ctx.category)
        if target_tags is not None:
            target = overpass.canonical_category(ctx.category) or ctx.category
            msg += (
                f" This is a single-category run ({ctx.category}): keep searching "
                f"'{target}' (raise `limit`, change `location`) until the target is "
                "met — off-target categories are refused/discarded."
            )
        elif len(ctx.searched_categories) < 5:
            remaining = [c for c in _SWEEP_CATEGORIES if c not in ctx.searched_categories]
            msg += " Sweep at least 5 different categories; not yet searched: " + (
                ", ".join(remaining[:8]) if remaining else "none"
            )
        else:
            msg += (
                " You have swept 5+ categories. If leads are still short, keep going or conclude."
            )
        if settings.searxng_endpoint and not ctx.web_results_seen:
            msg += (
                " Note: web search (SearXNG) is configured but has produced no "
                "results — the instance may be rate-limited or blocking the JSON "
                "API; results so far are OSM-only."
            )
        return msg

    @tool
    def verify_contact(
        phone: str | None = None,
        email: str | None = None,
        website: str | None = None,
        name: str | None = None,
        city: str | None = None,
        country: str | None = None,
        category: str | None = None,
        contact_name: str | None = None,
        contact_role: str | None = None,
        address: str | None = None,
        lat: float | None = None,
        lon: float | None = None,
        source: str = "agent",
        source_id: str | None = None,
        score: float | None = None,
        reason: str | None = None,
        what_to_sell: str | None = None,
        fit_score: float | None = None,
        fit_reason: str | None = None,
    ) -> str:
        """Verify a candidate's contact points — and save it immediately if it verifies.

        Args:
            phone: phone number from the search result. Any format is accepted:
                OSM multi-value strings ("+91-93343-18675;+91-93347-42008") are
                split, and country codes written as "+", "00", or omitted are all
                handled. Pass the raw value — no need to pre-clean it.
            email: email address from the search result (checked via format + MX).
            website: business website URL (best-effort reachability check).
            name: the business name. Pass this to save the lead in the SAME call
                once its contact verifies — no separate `save_lead` call needed.
                Omit `name` to only check verification without saving.
            city, country, category, contact_name, contact_role, address, lat,
                lon, source, source_id: forwarded to the save when `name` is
                given and the contact verifies (same meaning as on `save_lead`).
            score, reason, what_to_sell: forwarded to the save when `name` is
                given and the contact verifies (same meaning as on `save_lead`).
            fit_score, fit_reason: back-compat aliases for ``score`` and ``reason``.

        Returns:
            JSON with email/phone/website verification results and a ``save_ok``
            flag (a lead can be saved when the email is verified OR the phone is
            valid — a website is NOT required). When `name` was given, also
            includes `save_result`: the Saved/Duplicate/Discarded outcome, exactly
            as `save_lead` would report it.
        """
        email_result = verify.verify_email(email or "")
        phone_check = verify.validate_phone(phone, ctx.country_code)
        phone_norm = phone_check["normalized"]
        site = website_svc.check_website(website) if website else None
        save_ok = bool(email_result.get("verified")) or phone_norm is not None
        result = {
            "phone": {
                "valid": phone_norm is not None,
                "normalized": phone_norm,
                "reason": phone_check["reason"],
                "input": phone,
            },
            "email": email_result,
            "website": site,
            "save_ok": save_ok,
        }
        if name:
            result["save_result"] = (
                _persist_lead(
                    name,
                    website=website,
                    email=email,
                    phone=phone,
                    city=city,
                    country=country,
                    category=category,
                    contact_name=contact_name,
                    contact_role=contact_role,
                    address=address,
                    lat=lat,
                    lon=lon,
                    source=source,
                    source_id=source_id,
                    score=score,
                    reason=reason,
                    what_to_sell=what_to_sell,
                    fit_score=fit_score,
                    fit_reason=fit_reason,
                )
                if save_ok
                else "Not saved: no verified contact point yet."
            )
        else:
            result["next_action"] = (
                "save_ok is True — call save_lead (or re-call verify_contact with `name` set) "
                "to persist this lead."
                if save_ok
                else "No verified contact point yet; adjust the data or move to another candidate."
            )
        return _json(result)

    @tool
    def research_business(name: str, city: str | None = None) -> str:
        """Look a specific business up on the web and return grounded contacts + socials.

        Searches the web for "<name> <city> <country>" to (a) confirm the
        business is real and matches the campaign (read the returned ``matches``
        snippets for relevance) and (b) auto-extract contact info and social
        links. Emails/phones are pulled from the search snippets AND by crawling
        the top non-directory result; social links come from the result hosts.

        Args:
            name: the exact business name from a `search_businesses` result.
            city: the business's city if known (optional but improves the find).

        Returns:
            JSON with:
            - found: whether any result was returned.
            - matches: top results with title, url, and snippet (for relevance).
            - emails, phones, socials, website: grounded data to pass to
              `save_lead`/`verify_contact`. NEVER invent values not listed here.
            - note: guidance when nothing was found (e.g. call `fetch_website`).

        Note:
            Results are cached per business name + city.
        """
        key = f"{name}|{city or ctx.location}"
        if key in ctx.research_cache:
            return _json({**ctx.research_cache[key], "cached": True})
        query = f'"{name}" {city or ""} {ctx.location}'.strip()
        raw = searxng.lookup(query)
        emails: set[str] = set()
        phones: set[str] = set()
        socials: dict[str, str] = {}
        website = None
        for item in raw:
            emails.update(website_svc.extract_emails(item.get("content") or item.get("title") or ""))
            phones.update(website_svc.extract_phones(item.get("content") or ""))
            for domain, url in website_svc.extract_socials_from_urls(item.get("url")).items():
                socials.setdefault(domain, url)

        matches = [
            {"title": item.get("title"), "url": item.get("url"), "snippet": (item.get("content") or "")[:300]}
            for item in raw
        ]
        result: dict = {
            "found": bool(raw),
            "matches": matches,
            "emails": sorted(emails),
            "phones": sorted(phones),
            "socials": socials,
            "website": website,
            "note": None,
        }

        # Enrich from the top non-directory result's own site (richer contact pages).
        for item in raw:
            candidate_site = _canonical_url(item.get("url"))
            if any(d in candidate_site for d in website_svc.SOCIAL_DOMAINS):
                continue
            if website is None:
                website = candidate_site
            crawled = website_svc.fetch_website(candidate_site)
            if crawled.get("emails") or crawled.get("phones") or crawled.get("socials"):
                website = crawled.get("url")
                emails.update(crawled.get("emails") or [])
                phones.update(crawled.get("phones") or [])
                for d, u in (crawled.get("socials") or {}).items():
                    socials.setdefault(d, u)
                result["website"] = website
                break

        if not raw:
            result["note"] = (
                "Web lookup returned nothing for this business. Use `fetch_website` "
                "with the known website (if any) before deciding whether to save."
            )

        result["emails"] = sorted(emails)
        result["phones"] = sorted(phones)
        result["socials"] = socials
        result["website"] = website
        ctx.research_cache[key] = result
        return _json(result)

    # NOTE: keep this order stable — tests index into it (search=0, fetch=1,
    # save_lead=2, progress=3, verify_contact=4, research=5), and agent.py
    # relies on ``tools[0]`` being search_businesses for the sub-agent.
    return [search_businesses, fetch_website, save_lead, progress, verify_contact, research_business]
