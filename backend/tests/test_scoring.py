from app.services.scoring import is_excluded, score_lead


def test_full_lead_scores_high():
    lead = {
        "name": "Acme Dental",
        "category": "Dental Clinics",
        "website": "https://acme.com",
        "email": "info@acme.com",
        "verified": True,
        "phone": "+15551234567",
        "contact_name": "Jane Doe",
    }
    score, breakdown = score_lead(lead, keywords=["dental"], fit_score=48)
    assert score >= 90
    assert breakdown["verified_email"] == 20
    assert breakdown["fit"] == 48


def test_score_is_contact_plus_fit():
    lead = {
        "name": "Acme Dental",
        "website": "https://acme.com",
        "email": "info@acme.com",
        "verified": True,
        "phone": "+15551234567",
    }
    score, breakdown = score_lead(lead, fit_score=30)
    # 12 (website) + 20 (email) + 10 (phone) = 42 contact, 30 fit
    assert score == 72
    assert breakdown["fit"] == 30


def test_fit_score_capped_to_50():
    lead = {"name": "Acme Dental", "email": "a@b.com", "verified": True}
    score, breakdown = score_lead(lead, fit_score=99)
    assert breakdown["fit"] == 50
    assert score == 50 + 20


def test_bare_lead_scores_low():
    score, _ = score_lead({"name": "Unknown"}, keywords=[])
    assert score == 0


def test_keyword_fallback_scales_fit():
    lead = {"name": "crm automation ai agency", "category": "saas"}
    _, breakdown = score_lead(lead, keywords=["crm", "automation", "ai", "agency", "saas", "tool"])
    assert breakdown["keywords"] == 15  # capped
    assert breakdown["fit"] == 50  # kw points scale to the 50-point fit half


def test_fit_provided_suppresses_keyword_fallback():
    lead = {"name": "crm automation ai agency"}
    _, breakdown = score_lead(lead, keywords=["crm", "ai"], fit_score=42)
    assert breakdown["fit"] == 42
    assert "keywords" not in breakdown


def test_exclude_matches_name():
    lead = {"name": "Freelancer Agency"}
    assert is_excluded(lead, ["freelancer", "agency"]) is True


def test_exclude_no_match():
    lead = {"name": "Acme Dental"}
    assert is_excluded(lead, ["freelancer", "agency"]) is False