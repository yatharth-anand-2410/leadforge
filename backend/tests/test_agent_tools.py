import json

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


def test_progress_reports_count():
    progress = _tools()[3]
    assert progress.invoke({}) == "0/3 leads collected."
