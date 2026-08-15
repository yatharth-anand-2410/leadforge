"""SQLite-backed job queue helpers (single worker).

Claiming uses an optimistic UPDATE guarded by ``status == 'queued'`` so that
even if two workers ever race, only one claims a given job (rowcount 0 = lost).
"""

from sqlalchemy import update

from ..db import SessionLocal, utcnow
from ..models.job import Job


def claim_next_job() -> int | None:
    db = SessionLocal()
    try:
        job = (
            db.query(Job)
            .filter(Job.status == "queued")
            .order_by(Job.created_at.asc())
            .first()
        )
        if job is None:
            db.rollback()
            return None

        result = db.execute(
            update(Job)
            .where(Job.id == job.id, Job.status == "queued")
            .values(status="running", started_at=utcnow())
        )
        db.commit()
        return job.id if result.rowcount == 1 else None
    finally:
        db.close()


def update_progress(job_id: int, progress: dict) -> None:
    db = SessionLocal()
    try:
        db.execute(update(Job).where(Job.id == job_id).values(progress=progress))
        db.commit()
    finally:
        db.close()


def complete_job(job_id: int, progress: dict | None = None) -> None:
    db = SessionLocal()
    try:
        values: dict = {"status": "completed", "finished_at": utcnow()}
        if progress is not None:
            values["progress"] = progress
        db.execute(update(Job).where(Job.id == job_id).values(**values))
        db.commit()
    finally:
        db.close()


def fail_job(job_id: int, error: str) -> None:
    db = SessionLocal()
    try:
        db.execute(
            update(Job)
            .where(Job.id == job_id)
            .values(status="failed", error=error, finished_at=utcnow())
        )
        db.commit()
    finally:
        db.close()
