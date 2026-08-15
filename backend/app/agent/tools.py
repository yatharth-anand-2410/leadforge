"""Domain tools exposed to the deep agent (bound to a per-job AgentContext)."""

import json
from dataclasses import dataclass, field

from langchain_core.tools import tool

from ..db import SessionLocal
from ..models.lead import Lead
from ..services import dedupe, nominatim, overpass, scoring, verify, website as website_svc
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
    collected: list[str] = field(default_factory=list)  # dedupe keys/names shortlisted this run


def _json(obj) -> str:
    return json.dumps(obj, ensure_ascii=False, default=str)


def build_tools(ctx: AgentContext) -> list:
    @tool
    def search_businesses(limit: int = 25) -> str:
        """Discover candidate businesses for the target category and location.

        Args:
            limit: maximum number of candidates to return (default 25).

        Returns:
            A JSON list of candidate businesses with name, category, website,
            phone, email, address, city, country, lat, lon, source, source_id.
        """
        results: list[dict] = []
        geo = nominatim.geocode(ctx.location)
        if geo and geo.get("boundingbox"):
            results = overpass.search_businesses(ctx.category, geo["boundingbox"], limit=limit)
        if not results:
            results = nominatim.search_places(f"{ctx.category} {ctx.location}", limit=limit)
        if not results:
            return "No candidates found for this category/location."
        return _json(results)

    @tool
    def fetch_website(url: str) -> str:
        """Fetch a business website and extract contact points.

        Args:
            url: the business website URL (e.g. https://example.com).

        Returns:
            JSON with url, title, emails (list), phones (list), and socials (map).
        """
        return _json(website_svc.fetch_website(url))

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
    ) -> str:
        """Verify, score, deduplicate and persist a single lead.

        Args:
            name: the business name (required).
            website: business website URL.
            email: contact email (verified via MX lookup).
            phone: contact phone (format-validated).
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

        Returns:
            A status string: shortlisted (with score and progress) or discarded (with reason).
        """
        email_verified = bool(email and verify.verify_email(email)["verified"])
        phone_valid = bool(phone and verify.is_valid_phone(phone))

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

        if not email_verified and not (phone_valid and website):
            return "Discarded: no verified contact point (needs verified email, or a valid phone + website)."

        score, breakdown = scoring.score_lead(lead, ctx.keywords)
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
                phone=phone,
                contact_name=contact_name,
                contact_role=contact_role,
                source=source,
                source_id=source_id,
                score=score,
                score_breakdown=breakdown,
                verified=email_verified,
                verified_method="mx" if email_verified else ("phone_format" if phone_valid else None),
                status="shortlisted",
                dedupe_key=key,
                raw={},
            )
            db.add(row)
            db.commit()
            ctx.collected.append(key or name)
            total = (
                db.query(Lead)
                .filter(Lead.discovery_id == ctx.discovery_id, Lead.status == "shortlisted")
                .count()
            )
            update_progress(ctx.job_id, {"stage": "discovery", "lead_count": total})
            return (
                f"Saved ({len(ctx.collected)}/{ctx.num_leads}): {name} "
                f"score={score} verified={'email' if email_verified else 'phone'}"
            )
        finally:
            db.close()

    @tool
    def progress() -> str:
        """Report how many shortlisted leads have been collected so far vs the target.

        Returns:
            A string like "3/10 leads collected".
        """
        return f"{len(ctx.collected)}/{ctx.num_leads} leads collected."

    return [search_businesses, fetch_website, save_lead, progress]
