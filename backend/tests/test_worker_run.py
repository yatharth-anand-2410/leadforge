import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

import app.worker.queue as queue_mod
import app.worker.worker as worker_mod
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
