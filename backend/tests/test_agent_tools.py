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


@pytest.fixture(autouse=True)
def _web_search_disabled(monkeypatch):
    """Keep SearXNG off unless a test explicitly enables it.

    A developer's local backend/.env may set SEARXNG_ENDPOINT; without this the
    existing search tests would make real HTTP calls to a public instance.
    """
    monkeypatch.setattr(tools_mod.settings, "searxng_endpoint", "")
    # Same for OSM: a local .env may set ENABLE_OSM=false, which must not change
    # the behavior under test unless a test opts in explicitly.
    monkeypatch.setattr(tools_mod.settings, "enable_osm", True)


def _enable_web_search(monkeypatch):
    monkeypatch.setattr(tools_mod.settings, "searxng_endpoint", "https://searx.be")


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
    # Score is the LLM's judgment; without one provided this lead is 0-scored.
    assert lead.score == 0.0
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


def test_search_businesses_requires_location_when_empty():
    search = build_tools(_ctx(location=""))[0]

    out = search.invoke({})
    assert "No location given" in out
    assert "`location` argument" in out


def test_search_businesses_accepts_location_argument(monkeypatch):
    captured: dict[str, str] = {}

    def fake_geocode(location):
        captured["location"] = location
        return {"boundingbox": (0.0, 0.0, 1.0, 1.0)}

    monkeypatch.setattr(tools_mod.nominatim, "geocode", fake_geocode)
    monkeypatch.setattr(
        tools_mod.overpass,
        "search_businesses",
        lambda category, bbox, limit=25: [
            {"name": "Clinic A", "category": category, "website": "https://a.example.com"}
        ],
    )
    search = build_tools(_ctx(location=""))[0]

    out = search.invoke(
        {"category": "Dental Clinics", "location": "Phoenix, AZ", "limit": 5}
    )
    data = json.loads(out)
    assert captured["location"] == "Phoenix, AZ"
    assert data[0]["name"] == "Clinic A"


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

    search.invoke({"category": "dentist"})
    assert captured["category"] == "dentist"


def test_search_businesses_refuses_off_target_category(monkeypatch):
    """A single-category run must refuse searching an unrelated mapped category."""
    called = {"n": 0}

    def fake_search(category, bbox, limit=25):
        called["n"] += 1
        return [{"name": "Clinic A", "category": category}]

    monkeypatch.setattr(tools_mod.overpass, "search_businesses", fake_search)
    ctx = _ctx()
    search = build_tools(ctx)[0]

    out = search.invoke({"category": "bakery"})
    assert "does not match" in out
    assert "Dental Clinics" in out
    assert called["n"] == 0
    assert "bakery" in ctx.searched_categories


def test_save_lead_discards_off_target_category(tool_session, monkeypatch):
    monkeypatch.setattr(tools_mod.verify, "verify_email", lambda email: {"verified": True})
    save_lead = _tools()[2]

    out = save_lead.invoke(
        {
            "name": "Arobake",
            "website": "https://arobake.example.com",
            "email": "a@arobake.example.com",
            "category": "bakery",
        }
    )
    assert "Discarded" in out
    assert "does not match" in out
    db = tool_session()
    assert db.query(Lead).count() == 0
    db.close()


def test_save_lead_accepts_matching_category(tool_session, monkeypatch):
    monkeypatch.setattr(tools_mod.verify, "verify_email", lambda email: {"verified": True})
    save_lead = _tools()[2]

    out = save_lead.invoke(
        {
            "name": "Whitby Dental",
            "website": "https://whitby.example.com",
            "email": "info@whitby.example.com",
            "category": "dentist",
        }
    )
    assert out.startswith("Saved (1/3): Whitby Dental")
    db = tool_session()
    row = db.query(Lead).first()
    assert row.category == "dental"
    db.close()


def test_save_lead_rejects_unmapped_category_when_gated(tool_session, monkeypatch):
    monkeypatch.setattr(tools_mod.verify, "verify_email", lambda email: {"verified": True})
    save_lead = _tools()[2]

    out = save_lead.invoke(
        {
            "name": "Some Practice",
            "website": "https://somepractice.example.com",
            "email": "a@somepractice.example.com",
            "category": "general practice",
        }
    )
    assert "Discarded" in out
    assert "does not match" in out
    db = tool_session()
    assert db.query(Lead).count() == 0
    db.close()


def test_save_lead_ungated_allows_off_target_category(tool_session, monkeypatch):
    """A broad-buyer ICP (no OSM mapping) keeps the multi-category sweep."""
    monkeypatch.setattr(tools_mod.verify, "verify_email", lambda email: {"verified": True})
    save_lead = _tools(category="SMEs looking for digital marketing services")[2]

    out = save_lead.invoke(
        {
            "name": "Arobake",
            "website": "https://arobake.example.com",
            "email": "a@arobake.example.com",
            "category": "bakery",
        }
    )
    assert out.startswith("Saved (1/3): Arobake")


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


def test_save_lead_persists_score_reason_and_what_to_sell(tool_session, monkeypatch):
    monkeypatch.setattr(tools_mod.verify, "verify_email", lambda email: {"verified": True})
    save_lead = _tools()[2]

    out = save_lead.invoke(
        {
            "name": "Smile Dental Clinic",
            "email": "hello@smiledental.example.com",
            "score": 88,
            "reason": "Local dental clinic with no online presence; strong buyer of growth services.",
            "what_to_sell": "A local-SEO + Google Business profile optimization package.",
        }
    )
    assert out.startswith("Saved")

    db = tool_session()
    lead = db.query(Lead).one()
    assert lead.fit_score == 88
    assert "strong buyer" in lead.fit_reason
    assert lead.what_to_sell == "A local-SEO + Google Business profile optimization package."
    assert lead.score == 88
    assert lead.score_breakdown["llm"] == 88
    assert "phone_input" not in lead.raw
    db.close()


def test_save_lead_accepts_legacy_fit_args(tool_session, monkeypatch):
    """fit_score/fit_reason remain valid aliases so old callers don't break."""
    monkeypatch.setattr(tools_mod.verify, "verify_email", lambda email: {"verified": True})
    save_lead = _tools()[2]

    out = save_lead.invoke(
        {"name": "Smile Dental Clinic", "email": "hello@smiledental.example.com", "fit_score": 40}
    )
    assert out.startswith("Saved")

    db = tool_session()
    lead = db.query(Lead).one()
    assert lead.fit_score == 40
    assert lead.score == 40
    db.close()


def test_verify_contact_saves_score_reason_and_what_to_sell(tool_session, monkeypatch):
    monkeypatch.setattr(tools_mod.verify, "verify_email", lambda email: {"verified": False})
    verify_contact = _tools()[4]

    out = json.loads(
        verify_contact.invoke(
            {
                "phone": "+91-93343-18675",
                "name": "Mr Litti",
                "score": 78,
                "reason": "Busy local restaurant relying on walk-ins; needs digital visibility.",
                "what_to_sell": "A catalog upgrade and online ordering platform.",
            }
        )
    )
    assert out["save_ok"] is True
    assert out["save_result"].startswith("Saved")

    db = tool_session()
    lead = db.query(Lead).one()
    assert lead.fit_score == 78
    assert lead.fit_reason == (
        "Busy local restaurant relying on walk-ins; needs digital visibility."
    )
    assert lead.what_to_sell == "A catalog upgrade and online ordering platform."
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
    ctx = _ctx(category="SMEs looking for digital marketing services")
    tools = build_tools(ctx)

    tools[0].invoke({"category": "restaurant", "limit": 5})  # broad-buyer sweep category
    out = tools[3].invoke({})

    assert "0/3 leads collected." in out
    assert "restaurant" in out
    assert "not yet searched" in out


def test_progress_pins_single_category_run(tool_session, monkeypatch):
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
    assert "single-category run" in out
    assert "dental" in out
    assert "not yet searched" not in out


# --- Web search (SearXNG) merged into search_businesses ---


def test_search_businesses_web_enabled_unsupported_category(monkeypatch):
    """An unsupported OSM category must fall through to web search when enabled."""
    _enable_web_search(monkeypatch)
    monkeypatch.setattr(
        tools_mod.searxng,
        "search_web",
        lambda query, category="web", limit=12: [
            {
                "name": "DigiGrowth Marketing",
                "category": category,
                "website": "https://digigrowth.example.com",
                "source": "searxng",
            }
        ],
    )
    ctx = _ctx(category="Digital Marketing Agencies")
    search = build_tools(ctx)[0]

    out = search.invoke({})
    assert "Unsupported category" not in out
    data = json.loads(out)
    assert data[0]["name"] == "DigiGrowth Marketing"
    assert data[0]["source"] == "searxng"
    assert ctx.saw_candidates is True
    assert "Digital Marketing Agencies" in ctx.searched_categories


def test_search_businesses_web_disabled_rejects_unsupported_category():
    search = _tools()[0]
    out = search.invoke({"category": "Digital Marketing Agencies"})
    assert "Unsupported category" in out


def test_search_businesses_merges_web_and_osm(monkeypatch):
    _enable_web_search(monkeypatch)
    monkeypatch.setattr(
        tools_mod.nominatim, "geocode", lambda location: {"boundingbox": (0.0, 0.0, 1.0, 1.0)}
    )
    monkeypatch.setattr(
        tools_mod.overpass,
        "search_businesses",
        lambda category, bbox, limit=12: [
            {"name": "Clinic A", "category": category, "website": "https://a.example.com", "source": "overpass"}
        ],
    )
    monkeypatch.setattr(
        tools_mod.searxng,
        "search_web",
        lambda query, category="web", limit=12: [
            {"name": "Clinic B", "category": category, "website": "https://b.example.com", "source": "searxng"}
        ],
    )
    search = _tools()[0]

    data = json.loads(search.invoke({"limit": 5}))
    assert {d["name"] for d in data} == {"Clinic A", "Clinic B"}


def test_search_businesses_web_present_when_osm_saturated(monkeypatch):
    """Web candidates must survive even when OSM already meets the limit — the
    50/50 interleave keeps web results from being starved out."""
    _enable_web_search(monkeypatch)
    monkeypatch.setattr(
        tools_mod.nominatim, "geocode", lambda location: {"boundingbox": (0.0, 0.0, 1.0, 1.0)}
    )
    monkeypatch.setattr(
        tools_mod.overpass,
        "search_businesses",
        lambda category, bbox, limit=12: [
            {"name": f"Osm Clinic {i}", "category": category, "website": f"https://o{i}.example.com", "source": "overpass"}
            for i in range(12)
        ],
    )
    monkeypatch.setattr(
        tools_mod.searxng,
        "search_web",
        lambda query, category="web", limit=12: [
            {"name": f"Web Clinic {i}", "category": category, "website": f"https://w{i}.example.com", "source": "searxng"}
            for i in range(12)
        ],
    )
    search = _tools()[0]

    data = json.loads(search.invoke({"limit": 12}))
    srcs = [d["source"] for d in data]
    assert len(data) == 12
    assert srcs.count("overpass") == 6
    assert srcs.count("searxng") == 6
    # 50/50 interleave starts with OSM: o[0], w[0], o[1], w[1], ...
    assert srcs[:4] == ["overpass", "searxng", "overpass", "searxng"]


def test_search_businesses_dedupes_web_against_osm(monkeypatch):
    """A web result for a business already found via OSM (same domain) is dropped."""
    _enable_web_search(monkeypatch)
    monkeypatch.setattr(
        tools_mod.nominatim, "geocode", lambda location: {"boundingbox": (0.0, 0.0, 1.0, 1.0)}
    )
    monkeypatch.setattr(
        tools_mod.overpass,
        "search_businesses",
        lambda category, bbox, limit=12: [
            {
                "name": "Clinic A",
                "category": category,
                "website": "https://a.example.com",
                "phone": "1234567890",
                "source": "overpass",
            }
        ],
    )
    monkeypatch.setattr(
        tools_mod.searxng,
        "search_web",
        lambda query, category="web", limit=12: [
            {"name": "Clinic A", "category": category, "website": "http://a.example.com", "source": "searxng"}
        ],
    )
    search = _tools()[0]

    data = json.loads(search.invoke({"limit": 5}))
    assert len(data) == 1
    assert data[0]["name"] == "Clinic A"
    assert data[0]["source"] == "overpass"


def test_search_businesses_web_empty_no_candidates(monkeypatch):
    """Empty web results must not break the 'No candidates found' path."""
    _enable_web_search(monkeypatch)
    monkeypatch.setattr(
        tools_mod.nominatim, "geocode", lambda location: {"boundingbox": (0.0, 0.0, 1.0, 1.0)}
    )
    monkeypatch.setattr(tools_mod.overpass, "search_businesses", lambda category, bbox, limit=12: [])
    monkeypatch.setattr(tools_mod.nominatim, "search_places", lambda query, limit=12: [])
    monkeypatch.setattr(tools_mod.searxng, "search_web", lambda query, category="web", limit=12: [])
    ctx = _ctx()
    search = build_tools(ctx)[0]

    out = search.invoke({"limit": 5})
    assert "No candidates found" in out
    assert ctx.saw_candidates is False


# --- Geography scoping (area filter) ---


def test_search_businesses_uses_geo_area(monkeypatch):
    """A geocoded administrative relation must scope Overpass via `area_id`."""
    monkeypatch.setattr(
        tools_mod.nominatim,
        "geocode",
        lambda location: {"boundingbox": (0.0, 0.0, 1.0, 1.0), "osm_type": "relation", "osm_id": 556706},
    )
    captured = {}

    def fake_search(category, bbox=None, area_id=None, limit=12):
        captured.update(category=category, bbox=bbox, area_id=area_id, limit=limit)
        return [{"name": "Clinic A", "category": category, "source": "overpass"}]

    monkeypatch.setattr(tools_mod.overpass, "search_businesses", fake_search)
    search = _tools()[0]

    out = search.invoke({"limit": 5})
    assert captured["area_id"] == 3600556706
    assert captured["limit"] == 5
    assert "Clinic A" in out


def test_search_businesses_area_empty_does_not_fallback_to_bbox(monkeypatch):
    """Empty area results must NOT re-search with the world-spanning bbox (the
    New Zealand antimeridian case) — they fall through to the geoscoped
    nominatim text search instead, so foreign candidates never leak in."""
    monkeypatch.setattr(
        tools_mod.nominatim,
        "geocode",
        lambda location: {"boundingbox": (0.0, 0.0, 1.0, 1.0), "osm_type": "relation", "osm_id": 556706},
    )
    calls = []

    def fake_search(category, bbox=None, area_id=None, limit=12):
        calls.append(area_id)
        return []

    monkeypatch.setattr(tools_mod.overpass, "search_businesses", fake_search)
    monkeypatch.setattr(
        tools_mod.nominatim,
        "search_places",
        lambda query, limit=12: [{"name": "Clinic A", "category": "clinic", "source": "nominatim"}],
    )
    search = _tools()[0]

    out = search.invoke({"limit": 5})
    assert calls == [3600556706]  # only the area query — never the bare bbox
    data = json.loads(out)
    assert data[0]["name"] == "Clinic A"
    assert data[0]["source"] == "nominatim"


def test_search_businesses_bbox_results_filtered_to_bbox(monkeypatch):
    """When only a bbox is available (no area relation), candidates outside the
    geocoded box must be dropped — including across a dateline-wrapping bbox."""
    monkeypatch.setattr(
        tools_mod.nominatim,
        "geocode",
        lambda location: {"boundingbox": (-1.0, 170.0, 1.0, -170.0)},  # wraps antimeridian
    )
    monkeypatch.setattr(
        tools_mod.overpass,
        "search_businesses",
        lambda category, bbox, limit=12: [
            {"name": "In Box East", "category": category, "lat": 0.0, "lon": 179.0, "source": "overpass"},
            {"name": "In Box West", "category": category, "lat": 0.0, "lon": -179.0, "source": "overpass"},
            {"name": "Outside Box", "category": category, "lat": 0.0, "lon": 0.0, "source": "overpass"},
        ],
    )
    monkeypatch.setattr(tools_mod.nominatim, "search_places", lambda query, limit=12: [])
    search = _tools()[0]

    data = json.loads(search.invoke({"limit": 5}))
    names = {d["name"] for d in data}
    assert names == {"In Box East", "In Box West"}


def test_within_bbox_antimeridian_wrap():
    wrapped = (-1.0, 170.0, 1.0, -170.0)
    assert tools_mod._within_bbox({"lat": 0.0, "lon": 179.0}, wrapped) is True
    assert tools_mod._within_bbox({"lat": 0.0, "lon": -179.0}, wrapped) is True
    assert tools_mod._within_bbox({"lat": 0.0, "lon": 0.0}, wrapped) is False
    assert tools_mod._within_bbox({"lat": 5.0, "lon": 179.0}, wrapped) is False


def test_within_bbox_normal_and_no_coords():
    normal = (-1.0, 10.0, 1.0, 20.0)
    assert tools_mod._within_bbox({"lat": 0.0, "lon": 15.0}, normal) is True
    assert tools_mod._within_bbox({"lat": 0.0, "lon": 25.0}, normal) is False
    # Web candidates have no lat/lon; they must never be dropped by the filter.
    assert tools_mod._within_bbox({"name": "Web Co", "website": "https://x.com"}, normal) is True


# --- Web-search degradation surfaced in progress ---


def test_progress_notes_web_degraded_when_configured_but_empty(monkeypatch):
    """With SEARXNG_ENDPOINT set and no web results, progress must warn."""
    _enable_web_search(monkeypatch)
    monkeypatch.setattr(
        tools_mod.nominatim, "geocode", lambda location: {"boundingbox": (0.0, 0.0, 1.0, 1.0)}
    )
    monkeypatch.setattr(tools_mod.overpass, "search_businesses", lambda category, bbox, limit=12: [])
    monkeypatch.setattr(tools_mod.nominatim, "search_places", lambda query, limit=12: [])
    monkeypatch.setattr(tools_mod.searxng, "search_web", lambda query, category="web", limit=12: [])
    ctx = _ctx()
    tools = build_tools(ctx)

    tools[0].invoke({"limit": 5})
    out = tools[3].invoke({})
    assert "OSM-only" in out
    assert ctx.web_results_seen is False


def test_progress_omits_web_note_when_web_results_flowed(monkeypatch):
    """Once web results appear, progress must not warn about degradation."""
    _enable_web_search(monkeypatch)
    monkeypatch.setattr(
        tools_mod.nominatim, "geocode", lambda location: {"boundingbox": (0.0, 0.0, 1.0, 1.0)}
    )
    monkeypatch.setattr(tools_mod.overpass, "search_businesses", lambda category, bbox, limit=12: [])
    monkeypatch.setattr(tools_mod.nominatim, "search_places", lambda query, limit=12: [])
    monkeypatch.setattr(
        tools_mod.searxng,
        "search_web",
        lambda query, category="web", limit=12: [{"name": "Clinic W", "source": "searxng"}],
    )
    ctx = _ctx()
    tools = build_tools(ctx)

    tools[0].invoke({"limit": 5})
    assert ctx.web_results_seen is True
    out = tools[3].invoke({})
    assert "OSM-only" not in out


# --- research_business (SearXNG business lookup) ---


def test_research_business_extracts_grounded_data(monkeypatch):
    """Snippets + crawled top site contribute emails/phones/socials; never invents."""
    _enable_web_search(monkeypatch)
    monkeypatch.setattr(
        tools_mod.searxng,
        "lookup",
        lambda query, limit=8: [
            {
                "url": "https://whitbydental.example.com",
                "title": "Whitby Dental Centre | Home",
                "content": "Call +64 4 234 7237 or email info@whitbydental.example.com.",
            },
            {
                "url": "https://www.facebook.com/whitbydental",
                "title": "Whitby Dental - Facebook",
                "content": "Whitby Dental Centre Porirua",
            },
        ],
    )
    monkeypatch.setattr(
        tools_mod.website_svc,
        "fetch_website",
        lambda url: {
            "url": "https://whitbydental.example.com/",
            "title": "Whitby Dental",
            "emails": ["info@whitbydental.example.com", "book@whitbydental.example.com"],
            "phones": ["+64 4 234 7237"],
            "socials": {"facebook.com": "https://www.facebook.com/whitbydental"},
        },
    )
    ctx = _ctx()
    research = build_tools(ctx)[5]

    out = json.loads(research.invoke({"name": "Whitby Dental Centre", "city": "Porirua"}))
    assert out["found"] is True
    assert out["emails"] == ["book@whitbydental.example.com", "info@whitbydental.example.com"]
    assert "+64 4 234 7237" in out["phones"]
    assert out["socials"]["facebook.com"] == "https://www.facebook.com/whitbydental"
    assert out["website"] == "https://whitbydental.example.com/"
    assert len(out["matches"]) == 2


def test_research_business_skips_social_host_for_crawl(monkeypatch):
    """The crawl enriches from the business's own site, not its social page."""
    _enable_web_search(monkeypatch)
    monkeypatch.setattr(
        tools_mod.searxng,
        "lookup",
        lambda query, limit=8: [
            {"url": "https://www.facebook.com/somedentist", "title": "SFB", "content": "A page"},
            {"url": "https://somedentist.co.nz", "title": "Some Dentist | Home", "content": "hello there"},
        ],
    )
    monkeypatch.setattr(
        tools_mod.website_svc,
        "fetch_website",
        lambda url: {"url": url, "title": "X", "emails": [], "phones": [], "socials": {}},
    )
    research = _tools()[5]

    out = json.loads(research.invoke({"name": "Some Dentist"}))
    assert out["website"] == "https://somedentist.co.nz"
    assert out["found"] is True


def test_research_business_empty_lookup_notes_fallback(monkeypatch):
    """No web results -> found:false + note pointing at fetch_website."""
    _enable_web_search(monkeypatch)
    monkeypatch.setattr(tools_mod.searxng, "lookup", lambda query, limit=8: [])
    research = _tools()[5]

    out = json.loads(research.invoke({"name": "No Presence Biz"}))
    assert out["found"] is False
    assert out["note"] and "fetch_website" in out["note"]
    assert out["emails"] == [] and out["phones"] == []


def test_research_business_caches(monkeypatch):
    _enable_web_search(monkeypatch)
    calls = {"n": 0}

    def fake_lookup(query, limit=8):
        calls["n"] += 1
        return [{"url": "https://a.example.com", "title": "A | Home", "content": "call +64 4 234 7237"}]

    monkeypatch.setattr(tools_mod.searxng, "lookup", fake_lookup)
    monkeypatch.setattr(
        tools_mod.website_svc,
        "fetch_website",
        lambda url: {"url": url, "title": "", "emails": [], "phones": [], "socials": {}},
    )
    ctx = _ctx()
    research = build_tools(ctx)[5]

    research.invoke({"name": "A"})
    second = json.loads(research.invoke({"name": "A"}))
    assert calls["n"] == 1
    assert second["cached"] is True


def test_search_businesses_records_target_country(monkeypatch):
    """A successful geocode pins the run's target country for the save gate."""
    monkeypatch.setattr(
        tools_mod.nominatim,
        "geocode",
        lambda location: {
            "boundingbox": (0.0, 0.0, 1.0, 1.0),
            "country": "New Zealand",
            "country_code": "nz",
        },
    )
    monkeypatch.setattr(
        tools_mod.overpass,
        "search_businesses",
        lambda category, bbox, limit=25: [
            {"name": "Clinic A", "category": category, "website": "https://a.example.com"}
        ],
    )
    ctx = _ctx(location="New Zealand")
    search = build_tools(ctx)[0]

    out = search.invoke({"limit": 5})
    assert json.loads(out)[0]["name"] == "Clinic A"
    assert ctx.target_countries == {"New Zealand"}


def test_search_businesses_records_country_code_when_name_missing(monkeypatch):
    monkeypatch.setattr(
        tools_mod.nominatim,
        "geocode",
        lambda location: {"boundingbox": (0.0, 0.0, 1.0, 1.0), "country_code": "in"},
    )
    monkeypatch.setattr(
        tools_mod.overpass,
        "search_businesses",
        lambda category, bbox, limit=25: [{"name": "Clinic A", "category": category}],
    )
    ctx = _ctx()
    search = build_tools(ctx)[0]

    search.invoke({"limit": 5})
    assert ctx.target_countries == {"in"}


def test_save_lead_discards_off_location_country(tool_session, monkeypatch):
    """A lead whose country contradicts the run's geo-scope must be discarded."""
    monkeypatch.setattr(tools_mod.verify, "verify_email", lambda email: {"verified": True})
    ctx = _ctx()
    ctx.target_countries.add("New Zealand")
    save_lead = build_tools(ctx)[2]

    out = save_lead.invoke(
        {
            "name": "Mumbai Dental",
            "website": "https://mumbai.example.com",
            "email": "info@mumbai.example.com",
            "country": "India",
            "category": "dentist",
        }
    )
    assert "Discarded" in out
    assert "does not match the run's target location" in out
    db = tool_session()
    assert db.query(Lead).count() == 0
    db.close()


def test_save_lead_accepts_matching_country_alias(tool_session, monkeypatch):
    """'NZ' and 'New Zealand' are the same target; the lead saves."""
    monkeypatch.setattr(tools_mod.verify, "verify_email", lambda email: {"verified": True})
    ctx = _ctx()
    ctx.target_countries.add("New Zealand")
    save_lead = build_tools(ctx)[2]

    out = save_lead.invoke(
        {
            "name": "Wellington Dental",
            "website": "https://wellington.example.com",
            "email": "info@wellington.example.com",
            "country": "NZ",
            "category": "dentist",
        }
    )
    assert out.startswith("Saved (1/3): Wellington Dental")
    db = tool_session()
    row = db.query(Lead).first()
    assert row.country == "NZ"
    db.close()


def test_save_lead_accepts_when_target_countries_empty(tool_session, monkeypatch):
    """No search has geocoded yet -> no target to contradict, lead still saves."""
    monkeypatch.setattr(tools_mod.verify, "verify_email", lambda email: {"verified": True})
    save_lead = _tools()[2]

    out = save_lead.invoke(
        {
            "name": "Any Dental",
            "website": "https://any.example.com",
            "email": "info@any.example.com",
            "country": "India",
            "category": "dentist",
        }
    )
    assert out.startswith("Saved (1/3): Any Dental")


def test_save_lead_matches_country_code_target(tool_session, monkeypatch):
    """A target recorded as 'in' still accepts a candidate country 'India'."""
    monkeypatch.setattr(tools_mod.verify, "verify_email", lambda email: {"verified": True})
    ctx = _ctx()
    ctx.target_countries.add("in")
    save_lead = build_tools(ctx)[2]

    out = save_lead.invoke(
        {
            "name": "Patna Dental",
            "website": "https://patna.example.com",
            "email": "info@patna.example.com",
            "country": "India",
            "category": "dentist",
        }
    )
    assert out.startswith("Saved (1/3): Patna Dental")


def test_country_alias_matching():
    assert tools_mod._country_matches("NZ", {"New Zealand"})
    assert tools_mod._country_matches("New Zealand", {"nz"})
    assert tools_mod._country_matches("USA", {"United States"})
    assert tools_mod._country_matches("u.k.", {"united kingdom"})
    assert not tools_mod._country_matches("India", {"New Zealand"})
    assert not tools_mod._country_matches(None, {"New Zealand"})


def test_country_matching_handles_nominatim_combined_name():
    """Nominatim returns NZ as 'New Zealand / Aotearoa'; a 'New Zealand'
    candidate must still match that target (and vice versa)."""
    assert tools_mod._country_matches("New Zealand", {"New Zealand / Aotearoa"})
    assert tools_mod._country_matches("NZ", {"New Zealand / Aotearoa"})
    assert tools_mod._country_matches("New Zealand / Aotearoa", {"New Zealand"})
    assert not tools_mod._country_matches("India", {"New Zealand / Aotearoa"})


def _enable_osm_disabled(monkeypatch):
    monkeypatch.setattr(tools_mod.settings, "enable_osm", False)


def test_search_businesses_osm_disabled_is_web_only(monkeypatch):
    """ENABLE_OSM=false must skip Overpass/Nominatim entirely and return web results."""
    _enable_web_search(monkeypatch)
    _enable_osm_disabled(monkeypatch)
    called = {"overpass": 0, "nominatim": 0}

    def boom(*args, **kwargs):
        called["overpass"] += 1
        raise AssertionError("overpass.search_businesses must not be called")

    def boom_geo(*args, **kwargs):
        called["nominatim"] += 1
        raise AssertionError("nominatim.geocode must not be called")

    monkeypatch.setattr(tools_mod.overpass, "search_businesses", boom)
    monkeypatch.setattr(tools_mod.nominatim, "geocode", boom_geo)
    monkeypatch.setattr(
        tools_mod.searxng,
        "search_web",
        lambda query, category="web", limit=12: [
            {
                "name": "NZ Dental",
                "category": category,
                "website": "https://nzdental.example.com",
                "source": "searxng",
            }
        ],
    )
    ctx = _ctx(location="New Zealand")
    search = build_tools(ctx)[0]

    out = json.loads(search.invoke({"category": "dental", "location": "New Zealand"}))
    assert out[0]["name"] == "NZ Dental"
    assert out[0]["source"] == "searxng"
    assert called["overpass"] == 0
    assert called["nominatim"] == 0
    # Location guard stays active in web-only mode: country inferred from location.
    assert ctx.target_countries == {"NZ"}


def test_search_businesses_osm_disabled_records_target_country_for_gate(monkeypatch):
    """Web-only runs must still reject off-country candidates at save time."""
    _enable_web_search(monkeypatch)
    _enable_osm_disabled(monkeypatch)
    monkeypatch.setattr(
        tools_mod.searxng,
        "search_web",
        lambda query, category="web", limit=12: [
            {"name": "NZ Dental", "category": category, "source": "searxng"}
        ],
    )
    ctx = _ctx(location="New Zealand")
    search = build_tools(ctx)[0]

    search.invoke({"category": "dental", "location": "Auckland, New Zealand"})
    assert ctx.target_countries == {"NZ"}


def test_search_businesses_osm_disabled_and_web_disabled_message(monkeypatch):
    """Both OSM and web disabled -> a clear 'no data source' message, not
    'Unsupported category'."""
    _enable_osm_disabled(monkeypatch)
    search = _tools()[0]

    out = search.invoke({"category": "dental"})
    assert "No data source is enabled" in out
    assert "ENABLE_OSM" in out
