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
    score, breakdown = score_lead(lead, keywords=["dental"])
    assert score >= 90
    assert "verified_email" in breakdown
    assert "keywords" in breakdown


def test_bare_lead_scores_low():
    score, _ = score_lead({"name": "Unknown"}, keywords=[])
    assert score == 0


def test_keyword_bonus_capped():
    lead = {"name": "crm automation ai agency", "category": "saas"}
    score, breakdown = score_lead(lead, keywords=["crm", "automation", "ai", "agency", "saas", "tool"])
    assert breakdown["keywords"] == 15  # capped


def test_exclude_matches_name():
    lead = {"name": "Freelancer Agency"}
    assert is_excluded(lead, ["freelancer", "agency"]) is True


def test_exclude_no_match():
    lead = {"name": "Acme Dental"}
    assert is_excluded(lead, ["freelancer", "agency"]) is False
