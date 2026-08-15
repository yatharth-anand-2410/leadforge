import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

import app.worker.queue as queue_mod
import app.worker.worker as worker_mod
from app.agent.tools import AgentContext
from app.db import Base
from app.models.discovery import Discovery
from app.models.job import Job
from app.models.lead import Lead


@pytest.fixture()
def run_session(tmp_path, monkeypatch):
    engine = create_engine(
        f"sqlite:///{tmp_path / 'run.db'}",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(bind=engine)
    sl = sessionmaker(bind=engine, autoflush=False, autocommit=False)
    monkeypatch.setattr(worker_mod, "SessionLocal", sl)
    monkeypatch.setattr(queue_mod, "SessionLocal", sl)
    return sl


def _seed(run_session):
    db = run_session()
    disc = Discovery(
        user_id=1,
        name="Dental in Bengaluru",
        lead_type="Dental Clinics",
        location="Bengaluru, India",
        num_leads=3,
    )
    db.add(disc)
    db.commit()
    job = Job(discovery_id=disc.id, user_id=1, status="running")
    db.add(job)
    db.commit()
    db.add(
        Lead(
            discovery_id=disc.id,
            user_id=1,
            name="Smile Dental",
            status="shortlisted",
            score=50,
            verified=True,
        )
    )
    db.commit()
    ids = (disc.id, job.id)
    db.close()
    return ids


def test_run_job_completes_with_lead_count(run_session, monkeypatch):
    disc_id, job_id = _seed(run_session)
    monkeypatch.setattr(worker_mod, "run_agent", lambda d, j: None)

    worker_mod.run_job(job_id)

    db = run_session()
    job = db.get(Job, job_id)
    assert job.status == "completed"
    assert job.progress["stage"] == "done"
    assert job.progress["lead_count"] == 1
    db.close()


def test_run_job_fails_on_missing_job(run_session, monkeypatch):
    monkeypatch.setattr(worker_mod, "run_agent", lambda d, j: None)
    with pytest.raises(RuntimeError):
        worker_mod.run_job(9999)


def _seed_no_leads(run_session):
    db = run_session()
    disc = Discovery(
        user_id=1,
        name="Dental in Bengaluru",
        lead_type="Dental Clinics",
        location="Bengaluru, India",
        num_leads=3,
    )
    db.add(disc)
    db.commit()
    job = Job(discovery_id=disc.id, user_id=1, status="running")
    db.add(job)
    db.commit()
    ids = (disc.id, job.id)
    db.close()
    return ids


def _ctx(**overrides):
    base = {
        "discovery_id": 1,
        "user_id": 1,
        "job_id": 1,
        "category": "Dental Clinics",
        "location": "Bengaluru, India",
        "industry": None,
        "keywords": [],
        "exclude_keywords": [],
        "num_leads": 3,
    }
    base.update(overrides)
    return AgentContext(**base)


def test_run_job_flags_premature_stop_when_candidates_seen_but_none_saved(run_session, monkeypatch):
    """0 leads + real candidates seen => job is failed, not a clean '0 leads'."""
    disc_id, job_id = _seed_no_leads(run_session)
    monkeypatch.setattr(
        worker_mod,
        "run_agent",
        lambda d, j: _ctx(
            discovery_id=disc_id,
            job_id=job_id,
            saw_candidates=True,
            searched_categories={"restaurant", "salon"},
            save_attempts=0,
        ),
    )

    worker_mod.run_job(job_id)

    db = run_session()
    job = db.get(Job, job_id)
    assert job.status == "failed"
    assert "premature" in job.error
    assert "restaurant" in job.error
    db.close()


def test_run_job_completes_with_zero_when_no_candidates_seen(run_session, monkeypatch):
    """0 leads + no candidates seen => legitimately completed with lead_count 0."""
    disc_id, job_id = _seed_no_leads(run_session)
    monkeypatch.setattr(
        worker_mod,
        "run_agent",
        lambda d, j: _ctx(discovery_id=disc_id, job_id=job_id, saw_candidates=False),
    )

    worker_mod.run_job(job_id)

    db = run_session()
    job = db.get(Job, job_id)
    assert job.status == "completed"
    assert job.progress["stage"] == "done"
    assert job.progress["lead_count"] == 0
    db.close()


def test_run_job_completes_with_lead_count_when_candidates_saved(run_session, monkeypatch):
    """>0 leads saved => completed even though candidates were seen."""
    disc_id, job_id = _seed(run_session)
    monkeypatch.setattr(
        worker_mod,
        "run_agent",
        lambda d, j: _ctx(
            discovery_id=disc_id, job_id=job_id, saw_candidates=True, save_attempts=2
        ),
    )

    worker_mod.run_job(job_id)

    db = run_session()
    job = db.get(Job, job_id)
    assert job.status == "completed"
    assert job.progress["lead_count"] == 1
    db.close()
