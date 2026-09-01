"""Deterministic lead filtering and LLM-judged scoring.

A lead's score (0–100) is the LLM's judgment of how well the business fits the
ICP — there is no additive contact-quality half. Contacts are still VERIFIED
(user must have a working email or phone to be saved) but verification is a
gate, not scoring. This keeps the score a single, interpretable number the
agent owns while preventing fabricated data from sneaking through the fit.
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
    """Score a lead 0–100 from the LLM's fit judgment.

    ``fit_score`` (0–100) is the agent's assessment of how well the lead fits
    the ICP. When omitted (defensive default for old callers), a keyword-
    substring match stands in so scores don't silently collapse.

    Returns ``(score, breakdown)`` where ``breakdown = {"llm": score}``.
    """
    if fit_score is not None:
        score = max(0.0, min(100.0, round(float(fit_score), 2)))
        breakdown = {"llm": score}
        return round(score, 2), breakdown

    hay = _haystack(lead)
    kw_hits = 0
    for kw in keywords or []:
        if kw and kw.lower() in hay:
            kw_hits += 1
    score = max(0.0, min(100.0, kw_hits * 10))
    breakdown = {"keywords": min(round(score, 2), 50.0), "llm": score}
    return round(score, 2), breakdown