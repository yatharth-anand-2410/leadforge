import json

import httpx
import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

import app.agent.tools as tools_mod
import app.worker.queue as queue_mod
from app.agent.tools import AgentContext, build_tools
from app.db import Base
from app.models.lead import Lead


@pytest.fixture()
def tool_session(tmp_path, monkeypatch):
    engine = create_engine(
        f"sqlite:///{tmp_path / 'agent.db'}",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(bind=engine)
    session_local = sessionmaker(bind=engine, autoflush=False, autocommit=False)
    monkeypatch.setattr(tools_mod, "SessionLocal", session_local)
    monkeypatch.setattr(queue_mod, "SessionLocal", session_local)
    return session_local


def _ctx(**overrides):
    base = dict(
        discovery_id=1,
        user_id=1,
        job_id=1,
        category="Dental Clinics",
        location="Bengaluru, India",
        industry=None,
        keywords=[],
        exclude_keywords=[],
        num_leads=3,
    )
    base.update(overrides)
    return AgentContext(**base)


def _tools(**ctx_overrides):
    return build_tools(_ctx(**ctx_overrides))


def test_save_lead_shortlists_with_verified_email(tool_session, monkeypatch):
    monkeypatch.setattr(tools_mod.verify, "verify_email", lambda email: {"verified": True})
    save_lead = _tools()[2]

    out = save_lead.invoke(
        {
            "name": "Smile Dental Clinic",
            "website": "https://smiledental.example.com",
            "email": "hello@smiledental.example.com",
            "city": "Bengaluru",
            "country": "India",
        }
    )
    assert out.startswith("Saved (1/3): Smile Dental Clinic")

    db = tool_session()
    rows = db.query(Lead).all()
    assert len(rows) == 1
    lead = rows[0]
    assert lead.status == "shortlisted"
    assert lead.verified is True
    assert lead.verified_method == "mx"
    assert lead.score > 0
    db.close()


def test_save_lead_discards_without_contact(tool_session, monkeypatch):
    monkeypatch.setattr(tools_mod.verify, "verify_email", lambda email: {"verified": False})
    save_lead = _tools()[2]

    out = save_lead.invoke({"name": "No Contact Biz", "website": "https://nocontact.example.com"})
    assert "Discarded" in out

    db = tool_session()
    assert db.query(Lead).count() == 0
    db.close()


def test_save_lead_deduplicates(tool_session, monkeypatch):
    monkeypatch.setattr(tools_mod.verify, "verify_email", lambda email: {"verified": True})
    save_lead = _tools()[2]
    args = {
        "name": "Smile Dental Clinic",
        "website": "https://smiledental.example.com",
        "email": "hello@smiledental.example.com",
    }

    first = save_lead.invoke(args)
    second = save_lead.invoke(args)
    assert first.startswith("Saved")
    assert second.startswith("Duplicate")

    db = tool_session()
    assert db.query(Lead).count() == 1
    db.close()


def test_save_lead_exclude_keyword(tool_session, monkeypatch):
    monkeypatch.setattr(tools_mod.verify, "verify_email", lambda email: {"verified": True})
    save_lead = _tools(exclude_keywords=["franchise"])[2]

    out = save_lead.invoke(
        {
            "name": "Smile Dental Franchise",
            "website": "https://smile.example.com",
            "email": "a@smile.example.com",
        }
    )
    assert "exclude" in out
    db = tool_session()
    assert db.query(Lead).count() == 0
    db.close()


def test_search_businesses_returns_candidates(monkeypatch):
    monkeypatch.setattr(
        tools_mod.nominatim,
        "geocode",
        lambda location: {"boundingbox": (0.0, 0.0, 1.0, 1.0)},
    )
    monkeypatch.setattr(
        tools_mod.overpass,
        "search_businesses",
        lambda category, bbox, limit=25: [
            {"name": "Clinic A", "category": category, "website": "https://a.example.com"}
        ],
    )
    search = _tools()[0]

    out = search.invoke({"limit": 5})
    data = json.loads(out)
    assert data[0]["name"] == "Clinic A"


def test_search_businesses_falls_back_to_nominatim(monkeypatch):
    monkeypatch.setattr(tools_mod.nominatim, "geocode", lambda location: None)
    monkeypatch.setattr(
        tools_mod.nominatim,
        "search_places",
        lambda query, limit=25: [{"name": "Clinic B", "source": "nominatim"}],
    )
    search = _tools()[0]

    out = search.invoke({"limit": 5})
    data = json.loads(out)
    assert data[0]["name"] == "Clinic B"


def test_progress_reports_count(tool_session):
    progress = _tools()[3]
    out = progress.invoke({})
    assert out.startswith("0/3 leads collected.")


def test_save_lead_rejects_fabricated_data(tool_session, monkeypatch):
    monkeypatch.setattr(tools_mod.verify, "verify_email", lambda email: {"verified": True})
    save_lead = _tools()[2]

    out = save_lead.invoke(
        {
            "name": "Example Business",
            "website": "https://example.com",
            "email": "info@example.com",
            "phone": "1234567890",
        }
    )
    assert "fabricated" in out

    db = tool_session()
    assert db.query(Lead).count() == 0
    db.close()


def test_search_businesses_records_candidates_seen(monkeypatch):
    monkeypatch.setattr(
        tools_mod.nominatim, "geocode", lambda location: {"boundingbox": (0.0, 0.0, 1.0, 1.0)}
    )
    monkeypatch.setattr(
        tools_mod.overpass,
        "search_businesses",
        lambda category, bbox, limit=25: [{"name": "Clinic A", "category": category}],
    )
    ctx = _ctx()
    search = build_tools(ctx)[0]

    out = search.invoke({"limit": 5})
    assert "Clinic A" in out
    assert ctx.saw_candidates is True
    assert "Dental Clinics" in ctx.searched_categories


def test_search_businesses_no_candidates_leaves_saw_candidates_false(monkeypatch):
    monkeypatch.setattr(
        tools_mod.nominatim, "geocode", lambda location: {"boundingbox": (0.0, 0.0, 1.0, 1.0)}
    )
    monkeypatch.setattr(
        tools_mod.overpass, "search_businesses", lambda category, bbox, limit=25: []
    )
    monkeypatch.setattr(tools_mod.nominatim, "search_places", lambda query, limit=25: [])
    ctx = _ctx()
    search = build_tools(ctx)[0]

    out = search.invoke({"limit": 5})
    assert "No candidates found" in out
    assert ctx.saw_candidates is False
    assert "Dental Clinics" in ctx.searched_categories


def test_save_lead_counts_attempts(tool_session, monkeypatch):
    monkeypatch.setattr(tools_mod.verify, "verify_email", lambda email: {"verified": False})
    ctx = _ctx()
    save_lead = build_tools(ctx)[2]

    save_lead.invoke({"name": "No Contact Biz", "website": "https://nocontact.example.com"})
    assert ctx.save_attempts == 1


def test_search_businesses_unsupported_category():
    search = _tools()[0]
    out = search.invoke({"category": "Digital Marketing Agencies"})
    assert "Unsupported category" in out
    assert "Supported categories" in out


def test_search_businesses_honors_category_override(monkeypatch):
    monkeypatch.setattr(
        tools_mod.nominatim, "geocode", lambda location: {"boundingbox": (0.0, 0.0, 1.0, 1.0)}
    )
    captured = {}

    def fake_search(category, bbox, limit=25):
        captured["category"] = category
        return [{"name": "Clinic A", "category": category}]

    monkeypatch.setattr(tools_mod.overpass, "search_businesses", fake_search)
    search = _tools()[0]

    search.invoke({"category": "Restaurants"})
    assert captured["category"] == "Restaurants"


def test_search_businesses_overpass_error_falls_back_to_nominatim(monkeypatch):
    monkeypatch.setattr(
        tools_mod.nominatim, "geocode", lambda location: {"boundingbox": (0.0, 0.0, 1.0, 1.0)}
    )

    def boom(category, bbox, limit=25):
        raise httpx.HTTPStatusError(
            "504 Gateway Timeout",
            request=httpx.Request("POST", "http://overpass"),
            response=httpx.Response(504, request=httpx.Request("POST", "http://overpass")),
        )

    monkeypatch.setattr(tools_mod.overpass, "search_businesses", boom)
    monkeypatch.setattr(
        tools_mod.nominatim,
        "search_places",
        lambda query, limit=25: [{"name": "Clinic B", "source": "nominatim"}],
    )
    search = _tools()[0]

    out = search.invoke({"limit": 5})
    data = json.loads(out)
    assert data[0]["name"] == "Clinic B"


def test_search_businesses_overpass_error_returns_message_when_no_fallback(monkeypatch):
    monkeypatch.setattr(
        tools_mod.nominatim, "geocode", lambda location: {"boundingbox": (0.0, 0.0, 1.0, 1.0)}
    )

    def boom(category, bbox, limit=25):
        raise httpx.HTTPStatusError(
            "504 Gateway Timeout",
            request=httpx.Request("POST", "http://overpass"),
            response=httpx.Response(504, request=httpx.Request("POST", "http://overpass")),
        )

    monkeypatch.setattr(tools_mod.overpass, "search_businesses", boom)
    monkeypatch.setattr(tools_mod.nominatim, "search_places", lambda query, limit=25: [])
    search = _tools()[0]

    out = search.invoke({"limit": 5})
    assert "search failed" in out
    assert "504" in out


# --- New behaviour: phone-only saves, verify_contact, loop guards, rich progress ---


def test_save_lead_passes_with_valid_phone_only(tool_session, monkeypatch):
    """A phone-only lead (no email, no website) must be saved — the Mr. Litti case."""
    monkeypatch.setattr(tools_mod.verify, "verify_email", lambda email: {"verified": False})
    save_lead = _tools()[2]

    out = save_lead.invoke(
        {
            "name": "Mr Litti",
            "phone": "+91-93343-18675",
            "city": "Bengaluru",
            "country": "India",
        }
    )
    assert out.startswith("Saved (1/3): Mr Litti")
    assert "phone" in out

    db = tool_session()
    lead = db.query(Lead).one()
    assert lead.status == "shortlisted"
    assert lead.verified is False
    assert lead.verified_method == "phone_format"
    assert lead.phone == "+919334318675"  # normalized
    db.close()


def test_save_lead_multi_value_phone_saves(tool_session, monkeypatch):
    """OSM ';'-joined phone strings must save, normalized to the first valid number."""
    monkeypatch.setattr(tools_mod.verify, "verify_email", lambda email: {"verified": False})
    save_lead = _tools()[2]

    out = save_lead.invoke(
        {
            "name": "Mr Litti",
            "phone": "+91-93343-18675;+91-93347-42008",
            "city": "Bengaluru",
            "country": "India",
        }
    )
    assert out.startswith("Saved")
    db = tool_session()
    lead = db.query(Lead).one()
    assert lead.phone == "+919334318675"
    db.close()


def test_save_lead_discard_reason_is_specific(tool_session, monkeypatch):
    """Discard messages must say exactly which contact point failed."""
    monkeypatch.setattr(tools_mod.verify, "verify_email", lambda email: {"verified": False})
    save_lead = _tools()[2]

    out = save_lead.invoke({"name": "No Contact Biz"})
    assert "no email" in out
    assert "no phone" in out

    out2 = save_lead.invoke({"name": "Bad Contact", "email": "nope", "phone": "12"})
    assert "failed format/MX verification" in out2
    assert "did not normalize to a valid number" in out2


def test_verify_contact_reports_contacts(monkeypatch):
    monkeypatch.setattr(tools_mod.verify, "verify_email", lambda email: {"verified": True})
    monkeypatch.setattr(
        tools_mod.website_svc, "check_website", lambda url: {"reachable": True, "status": 200}
    )
    verify_contact = _tools()[4]

    out = json.loads(
        verify_contact.invoke(
            {
                "phone": "+91-93343-18675;+91-93347-42008",
                "email": "a@b.com",
                "website": "https://mrlitti.com",
            }
        )
    )
    assert out["phone"]["valid"] is True
    assert out["phone"]["normalized"] == "+919334318675"
    assert out["email"]["verified"] is True
    assert out["website"]["reachable"] is True
    assert out["save_ok"] is True


def test_verify_contact_save_ok_false_when_nothing_verifies(monkeypatch):
    monkeypatch.setattr(tools_mod.verify, "verify_email", lambda email: {"verified": False})
    monkeypatch.setattr(
        tools_mod.website_svc, "check_website", lambda url: {"reachable": False, "status": 404}
    )
    verify_contact = _tools()[4]

    out = json.loads(
        verify_contact.invoke({"phone": "12", "email": "bad", "website": "https://x.example.com"})
    )
    assert out["phone"]["valid"] is False
    assert out["email"]["verified"] is False
    assert out["website"]["reachable"] is False
    assert out["save_ok"] is False


def test_verify_contact_saves_when_name_given_and_verifies(tool_session, monkeypatch):
    """Passing `name` to verify_contact must save the lead in the same call
    once its contact verifies — no separate save_lead call required."""
    monkeypatch.setattr(tools_mod.verify, "verify_email", lambda email: {"verified": False})
    verify_contact = _tools()[4]

    out = json.loads(
        verify_contact.invoke(
            {
                "phone": "+91-93343-18675",
                "name": "Mr Litti",
                "city": "Bengaluru",
                "country": "India",
            }
        )
    )
    assert out["save_ok"] is True
    assert out["save_result"].startswith("Saved (1/3): Mr Litti")

    db = tool_session()
    lead = db.query(Lead).one()
    assert lead.name == "Mr Litti"
    assert lead.phone == "+919334318675"
    db.close()


def test_verify_contact_does_not_save_when_unverified(tool_session, monkeypatch):
    monkeypatch.setattr(tools_mod.verify, "verify_email", lambda email: {"verified": False})
    verify_contact = _tools()[4]

    out = json.loads(
        verify_contact.invoke({"phone": "12", "name": "Bad Contact"})
    )
    assert out["save_ok"] is False
    assert "Not saved" in out["save_result"]

    db = tool_session()
    assert db.query(Lead).count() == 0
    db.close()


def test_verify_contact_without_name_suggests_next_action(monkeypatch):
    """Without `name`, verify_contact must not save, but must point the agent
    at the follow-up call needed to close the verify-then-save gap."""
    monkeypatch.setattr(tools_mod.verify, "verify_email", lambda email: {"verified": True})
    verify_contact = _tools()[4]

    out = json.loads(verify_contact.invoke({"email": "a@b.com"}))
    assert "save_result" not in out
    assert "save_lead" in out["next_action"]


def test_save_lead_persists_fit_reason_and_score(tool_session, monkeypatch):
    monkeypatch.setattr(tools_mod.verify, "verify_email", lambda email: {"verified": True})
    save_lead = _tools()[2]

    out = save_lead.invoke(
        {
            "name": "Smile Dental Clinic",
            "email": "hello@smiledental.example.com",
            "fit_score": 46,
            "fit_reason": "Local dental clinic with no online presence; strong buyer of growth services.",
        }
    )
    assert out.startswith("Saved")

    db = tool_session()
    lead = db.query(Lead).one()
    assert lead.fit_score == 46
    assert "strong buyer" in lead.fit_reason
    # 20 (email) + 46 (fit) = 66
    assert lead.score == 66
    assert lead.score_breakdown["fit"] == 46
    assert "phone_input" not in lead.raw
    db.close()


def test_verify_contact_saves_fit_reason_and_score(tool_session, monkeypatch):
    monkeypatch.setattr(tools_mod.verify, "verify_email", lambda email: {"verified": False})
    verify_contact = _tools()[4]

    out = json.loads(
        verify_contact.invoke(
            {
                "phone": "+91-93343-18675",
                "name": "Mr Litti",
                "fit_score": 40,
                "fit_reason": "Busy local restaurant relying on walk-ins; needs digital visibility.",
            }
        )
    )
    assert out["save_ok"] is True
    assert out["save_result"].startswith("Saved")

    db = tool_session()
    lead = db.query(Lead).one()
    assert lead.fit_score == 40
    assert lead.fit_reason == (
        "Busy local restaurant relying on walk-ins; needs digital visibility."
    )
    db.close()


def test_save_lead_bare_indian_phone_gets_country_code(tool_session, monkeypatch):
    monkeypatch.setattr(tools_mod.verify, "verify_email", lambda email: {"verified": False})
    save_lead = _tools(country_code="IN")[2]

    out = save_lead.invoke({"name": "Aroma Salon", "phone": "8409592722"})
    assert out.startswith("Saved")

    db = tool_session()
    lead = db.query(Lead).one()
    assert lead.phone == "+918409592722"
    assert lead.raw["phone_input"] == "8409592722"
    db.close()


def test_save_lead_discards_truncated_indian_landline(tool_session, monkeypatch):
    monkeypatch.setattr(tools_mod.verify, "verify_email", lambda email: {"verified": False})
    save_lead = _tools(country_code="IN")[2]

    out = save_lead.invoke({"name": "Old Clinic", "phone": "2361514"})
    assert out.startswith("Discarded")
    assert "STD" in out or "landline" in out

    db = tool_session()
    assert db.query(Lead).count() == 0
    db.close()


def test_search_businesses_loop_guard(monkeypatch):
    """Re-searching the same category+limit must be refused, with an escape hatch."""
    monkeypatch.setattr(
        tools_mod.nominatim, "geocode", lambda location: {"boundingbox": (0.0, 0.0, 1.0, 1.0)}
    )
    monkeypatch.setattr(
        tools_mod.overpass,
        "search_businesses",
        lambda category, bbox, limit=25: [{"name": "Clinic A", "category": category}],
    )
    search = _tools()[0]

    assert "Clinic A" in search.invoke({"limit": 5})
    second = search.invoke({"limit": 5})
    assert "Already searched" in second

    # A larger limit is the legitimate way to get more candidates for a category.
    assert "Clinic A" in search.invoke({"limit": 20})


def test_fetch_website_caches(monkeypatch):
    calls = {"n": 0}

    def fake_fetch(url):
        calls["n"] += 1
        return {"url": url, "title": "Mr Litti", "emails": [], "phones": [], "socials": {}}

    monkeypatch.setattr(tools_mod.website_svc, "fetch_website", fake_fetch)
    fetch = _tools()[1]

    first = json.loads(fetch.invoke({"url": "http://www.mrlitti.com"}))
    second = json.loads(fetch.invoke({"url": "http://www.mrlitti.com"}))

    assert calls["n"] == 1  # crawled once
    assert first.get("cached") is None
    assert second["cached"] is True
    assert "Already fetched" in second["note"]


def test_progress_suggests_unswept_categories(tool_session, monkeypatch):
    monkeypatch.setattr(
        tools_mod.nominatim, "geocode", lambda location: {"boundingbox": (0.0, 0.0, 1.0, 1.0)}
    )
    monkeypatch.setattr(tools_mod.overpass, "search_businesses", lambda category, bbox, limit=25: [])
    monkeypatch.setattr(tools_mod.nominatim, "search_places", lambda query, limit=25: [])
    ctx = _ctx()
    tools = build_tools(ctx)

    tools[0].invoke({"limit": 5})  # searches "Dental Clinics"
    out = tools[3].invoke({})

    assert "0/3 leads collected." in out
    assert "Dental Clinics" in out
    assert "not yet searched" in out
