"""Deterministic lead scoring and exclude-keyword filtering.

A lead's score (0–100) is split into two halves:

- **Contact quality (0–50)** — deterministic and data-driven: whether we have a
  reachable website, verified email, phone, and contact name.
- **ICP fit (0–50)** — how well the business matches the target: the agent's
  ``fit_score`` judgment when provided, otherwise a keyword-substring fallback.
"""


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


def score_lead(lead: dict, keywords: list[str] | None = None, fit_score: float | None = None) -> tuple[float, dict]:
    """Score a lead 0–100: contact quality (max 50) + ICP fit (max 50).

    ``fit_score`` is the agent's 0–50 judgment of how well the lead fits the ICP
    (higher = a stronger buyer). When omitted, a keyword-substring match stands
    in so scores don't silently collapse for leads saved without it.
    """
    score = 0.0
    breakdown: dict = {}

    if lead.get("website"):
        score += 12
        breakdown["website"] = 12

    email = lead.get("email")
    if email:
        if lead.get("verified"):
            score += 20
            breakdown["verified_email"] = 20
        else:
            score += 6
            breakdown["email"] = 6

    if lead.get("phone"):
        score += 10
        breakdown["phone"] = 10

    if lead.get("contact_name"):
        score += 8
        breakdown["contact_name"] = 8

    hay = _haystack(lead)
    kw_hits = 0
    for kw in keywords or []:
        if kw and kw.lower() in hay:
            kw_hits += 1

    if fit_score is not None:
        fit = max(0.0, min(50.0, round(float(fit_score), 2)))
    else:
        kw_points = min(15, kw_hits * 5)
        fit = round(kw_points * (50 / 15), 2) if kw_points else 0.0
        if kw_points:
            breakdown["keywords"] = kw_points
    breakdown["fit"] = fit
    score += fit

    return round(score, 2), breakdown