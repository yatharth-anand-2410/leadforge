"""LangSmith tracing configuration and per-run metadata.

Tracing is opt-in: ``configure()`` only exports the ``LANGSMITH_*`` environment
variables when both ``langsmith_tracing`` and ``langsmith_api_key`` are set in
settings. LangChain's auto-tracer reads these lazily, so calling ``configure()``
before ``agent.invoke`` is sufficient — no direct ``langsmith`` import required.
"""

import os

from .config import settings


def configure() -> None:
    """Export ``LANGSMITH_*`` env vars so LangChain auto-traces. Idempotent; cheap."""
    if not settings.langsmith_tracing or not settings.langsmith_api_key:
        return
    os.environ["LANGSMITH_TRACING"] = "true"
    os.environ["LANGSMITH_API_KEY"] = settings.langsmith_api_key
    os.environ["LANGSMITH_PROJECT"] = settings.langsmith_project or "leadforge"
    if settings.langsmith_endpoint:
        os.environ["LANGSMITH_ENDPOINT"] = settings.langsmith_endpoint


def run_config(discovery, job) -> dict:
    """Attribution metadata for one discovery job's run (RunnableConfig keys)."""
    return {
        "run_name": f"discovery-{discovery.id}-job-{job.id}",
        "tags": ["leadforge", f"discovery:{discovery.id}", f"user:{discovery.user_id}"],
        "metadata": {
            "discovery_id": discovery.id,
            "job_id": job.id,
            "user_id": discovery.user_id,
            "lead_type": discovery.lead_type,
            "location": discovery.location,
            "num_leads": discovery.num_leads,
        },
    }
