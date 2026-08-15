"""Domain tools exposed to the deep agent (bound to a per-job AgentContext)."""

import json
from dataclasses import dataclass, field

import httpx
from langchain_core.tools import tool

from ..db import SessionLocal
from ..models.lead import Lead
from ..services import dedupe, nominatim, overpass, scoring, verify
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

    # Loop guards. These live on the context object (not in the agent's
    # conversation), so they survive conversation compaction: compaction wipes
    # the agent's working memory every few turns, which is exactly what let the
    # old runs re-search the same category and re-fetch the same website in a
    # loop. Recording seen work here makes those repeats visible to the tools,
    # which can then refuse to redo them.
    last_search: dict[str, int] = field(default_factory=dict)  # category -> last limit
    fetched_urls: set[str] = field(default_factory=set)  # canonical URLs already fetched
    fetch_cache: dict[str, dict] = field(default_factory=dict)  # url -> last result


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


def build_tools(ctx: AgentContext) -> list:
    @tool
    def search_businesses(category: str | None = None, limit: int = 12) -> str:
        """Discover candidate businesses for a category and the target location.

        Args:
            category: the business category to search (e.g. "Dental Clinics").
                Defaults to the discovery's lead type. Must be one of the
                Overpass-supported categories; otherwise the tool returns the
                supported list so you can pick the closest match.
            limit: maximum number of candidates to return (default 12). Kept
                small: each result lands in the agent's context, which is
                budgeted against the model's per-minute token cap. The agent
                can search a category again if it needs more candidates.

        Returns:
            A JSON list of candidate businesses with name, category, website,
            phone, email, address, city, country, lat, lon, source, source_id.
        """
        cat = category or ctx.category
        if overpass.category_to_tags(cat) is None:
            return (
                f"Unsupported category '{cat}'. "
                f"Supported categories: {overpass.supported_categories()}"
            )
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
        # Upstream (Overpass/Nominatim) is flaky. Catch HTTP errors at each step
        # instead of raising: langgraph's default tool-error handler re-raises and
        # kills the whole agent run. On failure we still try the fallback chain
        # (Overpass -> Nominatim), and only if everything is exhausted do we
        # return a message so the agent can retry, narrow the area, or move on.
        results: list[dict] = []
        search_error: httpx.HTTPError | None = None
        try:
            geo = nominatim.geocode(ctx.location)
        except httpx.HTTPError as exc:
            search_error = exc
            geo = None
        if geo and geo.get("boundingbox"):
            try:
                results = overpass.search_businesses(cat, geo["boundingbox"], limit=limit)
            except httpx.HTTPError as exc:
                search_error = exc
        if not results:
            try:
                results = nominatim.search_places(f"{cat} {ctx.location}", limit=limit)
            except httpx.HTTPError as exc:
                search_error = exc
        # Telemetry: remember this search so the worker can tell a premature
        # STOP (agent bailed after seeing real candidates) from a genuinely
        # empty region.
        ctx.searched_categories.add(cat)
        if results:
            ctx.saw_candidates = True
        if not results:
            if search_error is not None:
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
        fit_score: float | None = None,
        fit_reason: str | None = None,
    ) -> str:
        """Verify, score, deduplicate and persist a single lead.

        Shared by the ``save_lead`` tool and ``verify_contact`` (which calls this
        directly once a contact verifies, so a verified candidate is saved in one
        tool call instead of depending on a second, separate ``save_lead`` call).
        """
        ctx.save_attempts += 1
        reason = _looks_fabricated(name, website, email, phone)
        if reason:
            return f"Discarded: looks fabricated ({reason})."

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

        if fit_score is not None:
            fit_score = max(0.0, min(50.0, round(float(fit_score), 2)))
        score, breakdown = scoring.score_lead(lead, ctx.keywords, fit_score=fit_score)
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
                fit_score=fit_score,
                fit_reason=fit_reason,
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
            fit_score: 0-50 estimate of how well this business fits the ICP
                (higher = a stronger buyer). Combined with contact quality for
                the final 0-100 score.
            fit_reason: one-line plain-English explanation of why this lead is
                a good fit (e.g. the local SME category and why it would buy).

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
        remaining = [c for c in _SWEEP_CATEGORIES if c not in ctx.searched_categories]
        msg = (
            f"{total}/{ctx.num_leads} leads collected. "
            f"Categories searched so far ({len(ctx.searched_categories)}): {searched}."
        )
        if len(ctx.searched_categories) < 5:
            msg += " Sweep at least 5 different categories; not yet searched: " + (
                ", ".join(remaining[:8]) if remaining else "none"
            )
        else:
            msg += " You have swept 5+ categories. If leads are still short, keep going or conclude."
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
            fit_score, fit_reason: forwarded to the save when `name` is given
                and the contact verifies (same meaning as on `save_lead`).

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

    # NOTE: keep this order stable — tests index into it (search=0, fetch=1,
    # save_lead=2, progress=3, verify_contact=4), and agent.py relies on
    # ``tools[0]`` being search_businesses for the sub-agent.
    return [search_businesses, fetch_website, save_lead, progress, verify_contact]
