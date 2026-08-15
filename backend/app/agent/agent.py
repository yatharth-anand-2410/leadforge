"""Deep-agent construction and execution (LangChain ``deepagents`` on Groq)."""

import threading

from deepagents import create_deep_agent
from langchain_groq import ChatGroq

from ..config import settings
from ..models.discovery import Discovery
from ..models.job import Job
from .prompts import DISCOVERY_SUBAGENT_PROMPT, ORCHESTRATOR_PROMPT
from .tools import AgentContext, build_tools


def _filters_text(discovery: Discovery) -> str:
    parts: list[str] = []
    if discovery.industry:
        parts.append(f"- Industry: {discovery.industry}")
    if discovery.keywords:
        parts.append(f"- Keywords: {', '.join(discovery.keywords)}")
    if discovery.exclude_keywords:
        parts.append(f"- Exclude: {', '.join(discovery.exclude_keywords)}")
    return "\n".join(parts) if parts else "(no additional filters)"


def build_agent(discovery: Discovery, job: Job):
    """Construct the deep agent and its bound context for one discovery job."""
    ctx = AgentContext(
        discovery_id=discovery.id,
        user_id=discovery.user_id,
        job_id=job.id,
        category=discovery.lead_type,
        location=discovery.location,
        industry=discovery.industry,
        keywords=list(discovery.keywords or []),
        exclude_keywords=list(discovery.exclude_keywords or []),
        num_leads=discovery.num_leads,
    )

    tools = build_tools(ctx)
    search_tool = tools[0]  # search_businesses, also exposed to the sub-agent

    system_prompt = ORCHESTRATOR_PROMPT.format(
        category=discovery.lead_type,
        location=discovery.location,
        filters=_filters_text(discovery),
        num_leads=discovery.num_leads,
        exclude_keywords=", ".join(discovery.exclude_keywords or []) or "none",
    )

    model = ChatGroq(
        model=settings.llm_model,
        api_key=settings.groq_api_key,
        temperature=0,
    )

    subagents = [
        {
            "name": "discovery",
            "description": (
                "Search for candidate businesses by category and location. Delegate one "
                "search at a time; use it to fan out searches in parallel."
            ),
            "system_prompt": DISCOVERY_SUBAGENT_PROMPT,
            "tools": [search_tool],
        }
    ]

    agent = create_deep_agent(
        model=model,
        tools=tools,
        system_prompt=system_prompt,
        subagents=subagents,
    )
    return agent, ctx


def run_agent(discovery: Discovery, job: Job) -> AgentContext:
    """Run the pipeline for a discovery; returns the context (with collected leads).

    The agent's step count is bounded by ``max_agent_iterations`` (passed as the
    LangGraph ``recursion_limit``); a wall-clock cap of ``job_timeout_seconds``
    guards against a single slow run.
    """
    agent, ctx = build_agent(discovery, job)

    task = (
        f"Run the lead discovery pipeline for: {discovery.lead_type} in {discovery.location}. "
        f"Collect up to {discovery.num_leads} shortlisted leads, then report the final list."
    )

    outcome: dict = {"error": None}

    def _invoke() -> None:
        try:
            agent.invoke(
                {"messages": [{"role": "user", "content": task}]},
                config={"recursion_limit": settings.max_agent_iterations},
            )
        except Exception as exc:  # noqa: BLE001 — re-raised in the caller's thread
            outcome["error"] = exc

    thread = threading.Thread(target=_invoke, daemon=True)
    thread.start()
    thread.join(timeout=settings.job_timeout_seconds)

    if thread.is_alive():
        raise RuntimeError(f"Job timed out after {settings.job_timeout_seconds}s")
    if outcome["error"] is not None:
        raise outcome["error"]
    return ctx
