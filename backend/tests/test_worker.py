import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

import app.worker.queue as queue_mod
from app.db import Base
from app.models.job import Job


@pytest.fixture()
def queue_session(tmp_path, monkeypatch):
    engine = create_engine(
        f"sqlite:///{tmp_path / 'q.db'}",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(bind=engine)
    session_local = sessionmaker(bind=engine, autoflush=False, autocommit=False)
    monkeypatch.setattr(queue_mod, "SessionLocal", session_local)
    return session_local


def _insert_job(session_local, status="queued"):
    db = session_local()
    job = Job(discovery_id=1, user_id=1, status=status)
    db.add(job)
    db.commit()
    job_id = job.id
    db.close()
    return job_id


def test_claim_and_complete(queue_session):
    job_id = _insert_job(queue_session)

    assert queue_mod.claim_next_job() == job_id

    db = queue_session()
    job = db.get(Job, job_id)
    assert job.status == "running"
    assert job.started_at is not None
    db.close()

    queue_mod.complete_job(job_id, {"stage": "done", "lead_count": 0})
    db = queue_session()
    job = db.get(Job, job_id)
    assert job.status == "completed"
    assert job.finished_at is not None
    db.close()

    assert queue_mod.claim_next_job() is None


def test_fail_job(queue_session):
    job_id = _insert_job(queue_session)
    queue_mod.claim_next_job()
    queue_mod.fail_job(job_id, "boom")

    db = queue_session()
    job = db.get(Job, job_id)
    assert job.status == "failed"
    assert job.error == "boom"
    db.close()
