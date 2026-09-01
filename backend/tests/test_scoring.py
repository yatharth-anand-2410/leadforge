from app.services.scoring import is_excluded, score_lead


def test_llm_score_is_used_directly():
    lead = {
        "name": "Acme Dental",
        "category": "Dental Clinics",
        "website": "https://acme.com",
        "email": "info@acme.com",
        "verified": True,
        "phone": "+15551234567",
    }
    score, breakdown = score_lead(lead, keywords=["dental"], fit_score=92)
    assert score == 92
    assert breakdown == {"llm": 92}


def test_llm_score_capped_to_100():
    lead = {"name": "Acme Dental"}
    score, breakdown = score_lead(lead, fit_score=150)
    assert score == 100
    assert breakdown["llm"] == 100


def test_llm_score_floored_to_0():
    lead = {"name": "Acme Dental"}
    score, _ = score_lead(lead, fit_score=-5)
    assert score == 0


def test_bare_lead_scores_zero_without_fit():
    score, _ = score_lead({"name": "Unknown"}, keywords=[])
    assert score == 0


def test_keyword_fallback_when_no_llm_score():
    lead = {"name": "crm automation ai agency", "category": "saas"}
    _, breakdown = score_lead(lead, keywords=["crm", "automation", "ai", "agency", "saas", "tool"])
    assert breakdown["llm"] > 0


def test_contact_fields_no_longer_additive():
    """Score is purely the LLM judgment — contacts don't add points."""
    rich = score_lead(
        {"name": "Acme", "website": "https://acme.com", "email": "a@b.com", "verified": True, "phone": "+1"},
        fit_score=50,
    )
    bare = score_lead({"name": "Acme"}, fit_score=50)
    assert rich[0] == bare[0] == 50


def test_exclude_matches_name():
    lead = {"name": "Freelancer Agency"}
    assert is_excluded(lead, ["freelancer", "agency"]) is True


def test_exclude_no_match():
    lead = {"name": "Acme Dental"}
    assert is_excluded(lead, ["freelancer", "agency"]) is False