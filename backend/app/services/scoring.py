"""Deterministic lead scoring and exclude-keyword filtering."""


def _haystack(lead: dict) -> str:
    return " ".join(
        filter(
            None,
            [
                lead.get("name", ""),
                lead.get("category", ""),
                lead.get("industry", ""),
                lead.get("description", ""),
            ],
        )
    ).lower()


def is_excluded(lead: dict, exclude_keywords: list[str]) -> bool:
    hay = _haystack(lead)
    for kw in exclude_keywords or []:
        if kw and kw.lower() in hay:
            return True
    return False


def score_lead(lead: dict, keywords: list[str] | None = None) -> tuple[float, dict]:
    """Score a lead 0–100. Higher = better fit."""
    score = 0.0
    breakdown: dict = {}

    if lead.get("website"):
        score += 20
        breakdown["website"] = 20

    email = lead.get("email")
    if email:
        if lead.get("verified"):
            score += 30
            breakdown["verified_email"] = 30
        else:
            score += 10
            breakdown["email"] = 10

    if lead.get("phone"):
        score += 15
        breakdown["phone"] = 15

    if lead.get("contact_name"):
        score += 10
        breakdown["contact_name"] = 10

    if lead.get("category"):
        score += 10
        breakdown["category"] = 10

    hay = _haystack(lead)
    kw_hits = 0
    for kw in keywords or []:
        if kw and kw.lower() in hay:
            kw_hits += 1
    if kw_hits:
        pts = min(15, kw_hits * 5)
        score += pts
        breakdown["keywords"] = pts

    return round(score, 2), breakdown
