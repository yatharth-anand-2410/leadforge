"""Standalone background worker: polls the ``jobs`` table and runs each job.

Run with: ``uv run python -m app.worker.worker`` (or ``make worker``).
"""

import time

from ..agent.agent import run_agent
from ..db import (
    Base,
    SessionLocal,
    drop_deprecated_discovery_columns,
    engine,
    ensure_discovery_columns,
    ensure_lead_columns,
)
from ..models.discovery import Discovery
from ..models.job import Job
from ..models.lead import Lead
from .queue import claim_next_job, complete_job, fail_job, update_progress

POLL_INTERVAL = 2.0


def run_job(job_id: int) -> None:
    """Execute one discovery job: run the deep agent, then record the result."""
    db = SessionLocal()
    try:
        job = db.get(Job, job_id)
        discovery = db.get(Discovery, job.discovery_id) if job else None
    finally:
        db.close()

    if job is None or discovery is None:
        raise RuntimeError(f"Job {job_id} or its discovery not found")

    update_progress(job_id, {"stage": "discovery", "lead_count": 0})

    ctx = run_agent(discovery, job)

    db = SessionLocal()
    try:
        lead_count = (
            db.query(Lead)
            .filter(Lead.discovery_id == discovery.id, Lead.status == "shortlisted")
            .count()
        )
    finally:
        db.close()

    # Guardrail: never report a clean "0 leads" when the agent actually saw real
    # candidates but converted none. A run that searched categories and found
    # candidates yet ended with 0 leads is usually a premature STOP (the agent
    # gave up before sweeping categories / enriching websites), so surface it as
    # a failure the user can review instead of a confident empty result.
    if lead_count == 0 and ctx is not None and ctx.saw_candidates:
        categories = ", ".join(sorted(ctx.searched_categories)) or "unknown"
        fail_job(
            job_id,
            "Agent finished with 0 shortlisted leads even though it found real "
            f"candidates across {len(ctx.searched_categories)} category/categories "
            f"({categories}) and attempted {ctx.save_attempts} save(s). This looks "
            "like a premature stop — review the run before concluding there are no leads.",
        )
        return

    complete_job(job_id, {"stage": "done", "lead_count": lead_count})


def main() -> None:
    Base.metadata.create_all(bind=engine)
    ensure_lead_columns()
    ensure_discovery_columns()
    drop_deprecated_discovery_columns()
    print("Lead Forge worker started. Polling for jobs...")
    while True:
        job_id = claim_next_job()
        if job_id is None:
            time.sleep(POLL_INTERVAL)
            continue

        print(f"Running job {job_id}")
        try:
            run_job(job_id)
        except Exception as exc:  # noqa: BLE001
            fail_job(job_id, str(exc))
            print(f"Job {job_id} failed: {exc}")


if __name__ == "__main__":
    main()
