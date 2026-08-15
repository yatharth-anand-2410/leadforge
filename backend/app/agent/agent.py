"""Deep-agent construction and execution (LangChain ``deepagents``).

The chat backend is selected by ``settings.llm_provider`` — Groq, the native
Gemini API, or OpenCode Go (OpenAI-compatible or Anthropic-compatible) — via
``_build_chat_model``. The retry/summarization tuning below was originally
calibrated to Groq's free-tier tokens-per-minute cap and is relaxed for the
other providers, whose limits are dollar-based rather than TPM-based.
"""

import asyncio
import threading
import time

from deepagents import create_deep_agent
from deepagents.backends import StateBackend
from deepagents.middleware.summarization import SummarizationMiddleware
from langchain.agents.middleware import AgentMiddleware
from langchain_anthropic import ChatAnthropic
from langchain_core.messages import HumanMessage
from langchain_google_genai import ChatGoogleGenerativeAI
from langchain_groq import ChatGroq
from langchain_openai import ChatOpenAI

from ..config import settings
from ..models.discovery import Discovery
from ..models.job import Job
from ..observability import configure, run_config
from ..services.overpass import supported_categories
from ..services.verify import infer_country_code
from .prompts import DISCOVERY_SUBAGENT_PROMPT, ORCHESTRATOR_PROMPT
from .tools import AgentContext, build_tools

# Temperature per attempt when the provider rejects a malformed tool-call
# generation (Groq HTTP 400 `tool_use_failed`). Temperature 0 is mostly-greedy
# but a rare token-level slip can emit `<function=name{...}` without the closing
# `>`; a slightly higher temperature on retry derisks a deterministic repeat.
_RETRY_TEMPERATURES = (0.0, 0.3, 0.6)

# Groq's free/on_demand tier caps `gpt-oss-20b` at 8000 tokens/minute.
# deepagents' default summarization trigger is calibrated to the model's
# ~128k context window (a 0.85 fraction), so it never fires before Groq
# rejects an oversized request with a 413. These thresholds force compaction
# to run well under the per-minute cap instead: fixed overhead is ~1.8k tokens
# (system prompt + tool schemas), so triggering at 4200 and keeping 900 leaves
# effective requests ~3.5k — comfortably inside the 8000 budget.
_SUMMARY_TRIGGER_TOKENS = 4200
_SUMMARY_KEEP_TOKENS = 900
_SUMMARY_TRIM_TOKENS = 1500

# OpenCode Go models (DeepSeek, Kimi, GLM, MiniMax, Qwen) bill by dollars, not
# tokens-per-minute, and expose 128k–256k context windows. Compacting at 4200
# tokens there would over-summarize and degrade the run, so we only trigger
# near a genuinely large context.
_LARGE_CONTEXT_TRIGGER_TOKENS = 30000
_LARGE_CONTEXT_KEEP_TOKENS = 4000
_LARGE_CONTEXT_TRIM_TOKENS = 8000

# Gemini 2.5 Flash (and current Gemini models generally) expose a ~1M token
# context window, so summarization can be deferred almost entirely: trigger
# only once the conversation is a substantial fraction of that window, keep a
# generous tail, and summarize a large slice when it does fire.
_GEMINI_TRIGGER_TOKENS = 400000
_GEMINI_KEEP_TOKENS = 100000
_GEMINI_TRIM_TOKENS = 150000


def _summary_thresholds() -> tuple[int, int, int]:
    """(trigger, keep, trim) token thresholds for the summarization middleware.

    Groq keeps the aggressive 8000-TPM-tuned values; OpenCode Go gets relaxed
    values for its 128k–256k models; Gemini gets near-window values for its
    ~1M context. This keeps compaction from firing early and degrading the run
    on providers that don't bill per minute.
    """
    if settings.llm_provider == "groq":
        return _SUMMARY_TRIGGER_TOKENS, _SUMMARY_KEEP_TOKENS, _SUMMARY_TRIM_TOKENS
    if settings.llm_provider == "gemini":
        return _GEMINI_TRIGGER_TOKENS, _GEMINI_KEEP_TOKENS, _GEMINI_TRIM_TOKENS
    return _LARGE_CONTEXT_TRIGGER_TOKENS, _LARGE_CONTEXT_KEEP_TOKENS, _LARGE_CONTEXT_TRIM_TOKENS


def _task_text(discovery: Discovery) -> str:
    """The one-line task handed to the agent, reused verbatim everywhere.

    Kept in one place because compaction repeats this text back to the model:
    if the agent and the summarizer ever disagree about what the task is, the
    summarizer wins and the run drifts (the observed café-list bug).
    """
    return (
        f"Run the lead discovery pipeline for: {discovery.lead_type} in {discovery.location}. "
        f"Collect up to {discovery.num_leads} shortlisted leads, then report the final list."
    )


# Custom summarization prompt. The default (LangChain's) records a generic
# SESSION INTENT, which let compaction silently mangle the task into something
# else (a run drifted into "find cafés"). This template:
#   - pins SESSION INTENT to the ORIGINAL task, verbatim, and tells the model
#     to treat any drift as wrong;
#   - injects the GROUND-TRUTH PIPELINE STATE computed from the live
#     ``AgentContext`` (categories searched, urls fetched, save attempts)
#     instead of asking the model to reconstruct it by re-reading trimmed
#     message history. On a small model (gpt-oss-20b) that reconstruction is a
#     guess, not a fact: a searched category can come back reported as
#     unsearched, and a NEXT STEPS category can be invented outside the
#     supported list (inverting the ICP into "find marketing agencies", which
#     the orchestrator prompt explicitly forbids). ``__PIPELINE_STATE__`` is
#     left unresolved here and is substituted per-call by
#     ``_ContextAwareSummarizationMiddleware`` so it is always current.
# Placeholders are __SENTINEL__ tokens (replaced with .replace(), so user task
# text can't break .format()); ``{{messages}}`` becomes the runtime ``{messages}``
# placeholder that LangChain's summarization middleware fills with the history.
_SUMMARY_PROMPT_TEMPLATE = """\
<role>
Context Extraction Assistant
</role>

<primary_objective>
Extract the highest-quality, most relevant context from the conversation history below so the lead-discovery agent can keep working toward its one goal without repeating work.
</primary_objective>

<objective_information>
The conversation below is near the token budget and will be replaced by the context you extract. Only keep what is needed to continue the ORIGINAL task. Never "improve", reinterpret, or replace the task — the original request stated below is the ground truth.
</objective_information>

<instructions>
The conversation history below will be replaced with the context you extract. Do NOT repeat actions already completed — recording them as done is the whole point.

Populate every section below. If a section has nothing to report, write "None". Never invent facts.

## SESSION INTENT

The user's original request — restate it VERBATIM, do not paraphrase:

"__TASK__"

That is the one and only goal. If the conversation drifted toward a different goal (for example a list of cafés, or a different location), the request above is the truth: note the drift in SUMMARY and return to the original task.

## GROUND-TRUTH PIPELINE STATE (authoritative — computed directly from real tool calls, not from the conversation text below; if the conversation disagrees with this, THIS is correct)

__PIPELINE_STATE__

Do not re-derive these facts from the conversation. Never invent a category outside the "not yet searched" list above, and never re-search anything on the "already searched" list.

## SUMMARY

Candidates the agent explicitly identified as qualifying (name, category, and which contact data verified them — email / phone / website), candidates rejected and why, scoring/quality signals, and any other context needed to continue. Do NOT restate the pipeline facts above — only note conversational detail not already captured there.

## ARTIFACTS

Leads already persisted in the database (names and the saved contact points), and any other resources created or accessed.

## NEXT STEPS

What remains to reach __NUM_LEADS__ shortlisted leads for "__CATEGORY__" in "__LOCATION__": pick the next category ONLY from the "not yet searched" list above, which candidates still need a `save_lead` or `verify_contact` call, and what to do next. Prefer continuing the sweep over stopping early.

<messages>
Messages to summarize:
{{messages}}
</messages>
"""


def _summary_prompt_for(discovery: Discovery) -> str:
    """Render the summary template for one discovery (see module docstring).

    ``__PIPELINE_STATE__`` is deliberately left unresolved: it depends on live
    ``AgentContext`` state that only exists once the run is underway, and is
    substituted per-compaction by ``_ContextAwareSummarizationMiddleware``.
    """
    return (
        _SUMMARY_PROMPT_TEMPLATE.replace("__TASK__", _task_text(discovery))
        .replace("__NUM_LEADS__", str(discovery.num_leads))
        .replace("__CATEGORY__", discovery.lead_type)
        .replace("__LOCATION__", discovery.location)
        .replace("{{messages}}", "{messages}")
    )


def _pipeline_state_text(ctx: AgentContext) -> str:
    """Render ``ctx``'s live pipeline state for injection into the summary prompt.

    Reads the same ``AgentContext`` fields the tools already maintain
    specifically to survive compaction (see the loop-guard comment on
    ``AgentContext`` in tools.py), so the summarizer is handed facts instead of
    being asked to guess them from trimmed message history.
    """
    supported = supported_categories()
    searched = sorted(ctx.searched_categories)
    remaining = [c for c in supported if c not in ctx.searched_categories]
    exclude = ctx.exclude_keywords or []
    return (
        f"- Categories already searched via `search_businesses` ({len(searched)}): "
        f"{', '.join(searched) or 'none'}.\n"
        f"- Supported categories NOT yet searched ({len(remaining)}): "
        f"{', '.join(remaining) or 'none — every supported category has been searched'}.\n"
        f"- Exclude keywords (never save a lead matching these): "
        f"{', '.join(exclude) or 'none'}.\n"
        f"- Websites already fetched: {len(ctx.fetched_urls)}.\n"
        f"- `save_lead`/`verify_contact` save attempts so far: {ctx.save_attempts}."
    )


# Deterministic instruction appended after every generated summary. The
# summary itself is written by the same weak model being summarized for, so it
# can still drift; this directive forces a hard re-sync against ground truth
# (the `progress` tool, which is DB-backed) right after compaction instead of
# letting the agent act on the summary's own guess of the lead count.
_RESYNC_DIRECTIVE = (
    "Compaction just happened. Before your next `search_businesses`, `save_lead`, "
    "or `verify_contact` call, call `progress` to reload the authoritative lead "
    "count and searched-category list — do not act on a remembered count from "
    "before compaction."
)


class _ContextAwareSummarizationMiddleware(SummarizationMiddleware):
    """SummarizationMiddleware that re-renders the summary prompt from live state.

    ``summary_prompt`` on the base middleware is a static string baked in at
    construction time, but the pipeline state it needs to report (categories
    searched, urls fetched, save attempts) only exists on the per-job
    ``AgentContext`` and changes throughout the run. This subclass rebuilds the
    prompt from ``ctx`` immediately before every summarization call, so the
    injected state is correct by construction — the model only has to fill in
    qualitative notes it can't get anywhere else — and appends a deterministic
    resync directive after the generated summary (see ``_RESYNC_DIRECTIVE``).
    """

    def __init__(self, *args, ctx: AgentContext, prompt_template: str, **kwargs):
        super().__init__(*args, summary_prompt=prompt_template, **kwargs)
        self._ctx = ctx
        self._prompt_template = prompt_template

    @property
    def name(self) -> str:
        # Preserve the base class's public alias so `create_deep_agent` still
        # replaces its default SummarizationMiddleware instead of stacking a
        # second one under a different name (see build_agent's docstring).
        return "SummarizationMiddleware"

    def _rendered_prompt(self) -> str:
        return self._prompt_template.replace("__PIPELINE_STATE__", _pipeline_state_text(self._ctx))

    def _create_summary(self, messages_to_summarize):
        self._lc_helper.summary_prompt = self._rendered_prompt()
        return super()._create_summary(messages_to_summarize)

    async def _acreate_summary(self, messages_to_summarize):
        self._lc_helper.summary_prompt = self._rendered_prompt()
        return await super()._acreate_summary(messages_to_summarize)

    def _build_new_messages_with_path(self, summary, file_path):
        messages = super()._build_new_messages_with_path(summary, file_path)
        resync = HumanMessage(
            content=_RESYNC_DIRECTIVE,
            additional_kwargs={"lc_source": "summarization"},
        )
        return [*messages, resync]

# A model call that still trips the TPM cap (a burst that exceeded the rolling
# minute) is retried in place after a backoff long enough for the window to
# roll over. Restarting the whole run would re-pay the full context; an
# in-place retry keeps the graph and the agent's progress alive.
_RATE_LIMIT_RETRIES = 3
_RATE_LIMIT_BACKOFF_SECONDS = 60.0


def _is_tool_use_failure(exc: Exception) -> bool:
    """True when the provider rejected the run because the model emitted a malformed tool call."""
    msg = str(exc)
    return "tool_use_failed" in msg or "failed_generation" in msg


def _is_rate_limit_error(exc: Exception) -> bool:
    """True when the provider rejected the call for exceeding a rate/token budget.

    Covers Groq's 413 "Request too large ... tokens per minute" and 429
    ``rate_limit_exceeded``, plus the Gemini (``RESOURCE_EXHAUSTED``/``quota``)
    and OpenAI/Anthropic-compatible (429 ``too many requests``) equivalents. The
    compaction middleware should prevent the oversized-request case; but a burst
    of back-to-back calls can still trip a rolling rate window, which clears
    with a backoff.
    """
    status = getattr(exc, "status_code", None)
    if status in (413, 429):
        return True
    response = getattr(exc, "response", None)
    if getattr(response, "status_code", None) in (413, 429):
        return True
    msg = str(exc).lower()
    markers = (
        "rate_limit_exceeded",
        "tokens per minute",
        "request too large",
        "too many requests",
        "resource_exhausted",
        "quota",
    )
    return any(marker in msg for marker in markers)


class _RateLimitRetryMiddleware(AgentMiddleware):
    """Retry a model call in place when the provider hits a rate/token cap.

    Wrapping inside the graph (rather than restarting the whole run) keeps the
    agent's context and progress intact: a failed model call is transient, and
    a backoff lets the rolling rate window roll over before retrying the same
    request.
    """

    def wrap_model_call(self, request, handler):
        for _ in range(_RATE_LIMIT_RETRIES):
            try:
                return handler(request)
            except Exception as exc:
                if not _is_rate_limit_error(exc):
                    raise
                print(
                    f"Rate limit hit; backing off {_RATE_LIMIT_BACKOFF_SECONDS}s "
                    f"and retrying the same call ({exc})"
                )
                time.sleep(_RATE_LIMIT_BACKOFF_SECONDS)
        raise RuntimeError(
            f"The provider kept rejecting the model call after {_RATE_LIMIT_RETRIES} "
            f"in-place retries with {_RATE_LIMIT_BACKOFF_SECONDS:.0f}s backoffs. "
            f"See the retry logs."
        )

    async def awrap_model_call(self, request, handler):
        for _ in range(_RATE_LIMIT_RETRIES):
            try:
                return await handler(request)
            except Exception as exc:
                if not _is_rate_limit_error(exc):
                    raise
                print(
                    f"Rate limit hit; backing off {_RATE_LIMIT_BACKOFF_SECONDS}s "
                    f"and retrying the same call ({exc})"
                )
                await asyncio.sleep(_RATE_LIMIT_BACKOFF_SECONDS)
        raise RuntimeError(
            f"The provider kept rejecting the model call after {_RATE_LIMIT_RETRIES} "
            f"in-place retries with {_RATE_LIMIT_BACKOFF_SECONDS:.0f}s backoffs. "
            f"See the retry logs."
        )


def _filters_text(discovery: Discovery) -> str:
    parts: list[str] = []
    if discovery.industry:
        parts.append(f"- Industry: {discovery.industry}")
    if discovery.keywords:
        parts.append(f"- Keywords: {', '.join(discovery.keywords)}")
    if discovery.exclude_keywords:
        parts.append(f"- Exclude: {', '.join(discovery.exclude_keywords)}")
    return "\n".join(parts) if parts else "(no additional filters)"


def _build_chat_model(temperature: float):
    """Return the chat model for the configured ``llm_provider``.

    ``llm_model`` is the provider-specific model id. Groq and the native Gemini
    API are direct; OpenCode Go is an OpenAI- or Anthropic-compatible gateway
    addressed via ``opencode_base_url``, so the same ``opencode_api_key`` serves
    both of those two styles.
    """
    provider = settings.llm_provider
    if provider == "groq":
        return ChatGroq(
            model=settings.llm_model,
            api_key=settings.groq_api_key,
            temperature=temperature,
        )
    if provider == "gemini":
        return ChatGoogleGenerativeAI(
            model=settings.llm_model,
            google_api_key=settings.gemini_api_key,
            temperature=temperature,
        )
    if provider == "opencode-openai":
        return ChatOpenAI(
            model=settings.llm_model,
            api_key=settings.opencode_api_key,
            base_url=settings.opencode_base_url,
            temperature=temperature,
        )
    if provider == "opencode-anthropic":
        return ChatAnthropic(
            model_name=settings.llm_model,
            api_key=settings.opencode_api_key,
            base_url=settings.opencode_base_url,
            temperature=temperature,
        )
    raise ValueError(f"Unknown LLM_PROVIDER: {provider!r}")


def build_agent(discovery: Discovery, job: Job, temperature: float = 0.0):
    """Construct the deep agent and its bound context for one discovery job.

    The agent's model call is wrapped by two middlewares:

    - ``SummarizationMiddleware`` replaces deepagents' default with a custom
      prompt (pins the original task verbatim and injects the live pipeline
      state) and a provider-tuned token trigger (aggressive for Groq's 8000 TPM
      cap, relaxed for the other providers).
    - ``_RateLimitRetryMiddleware`` retries a call that trips a rolling rate
      window in place after a backoff, instead of killing the run.
    """
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
        country_code=infer_country_code(discovery.location),
    )

    tools = build_tools(ctx)
    search_tool = tools[0]  # search_businesses, also exposed to the sub-agent

    system_prompt = ORCHESTRATOR_PROMPT.format(
        category=discovery.lead_type,
        location=discovery.location,
        filters=_filters_text(discovery),
        num_leads=discovery.num_leads,
        exclude_keywords=", ".join(discovery.exclude_keywords or []) or "none",
        supported_categories=supported_categories(),
    )

    model = _build_chat_model(temperature)

    trigger, keep, trim = _summary_thresholds()
    # ``create_deep_agent`` installs a default SummarizationMiddleware under the
    # name "SummarizationMiddleware"; passing one with the same name replaces it
    # in place rather than stacking a second one on top.
    summarization = _ContextAwareSummarizationMiddleware(
        model=model,
        backend=StateBackend(),
        trigger=("tokens", trigger),
        keep=("tokens", keep),
        trim_tokens_to_summarize=trim,
        ctx=ctx,
        # Custom prompt pins the ORIGINAL task verbatim; GROUND-TRUTH PIPELINE
        # STATE is re-rendered from `ctx` before every compaction call so
        # categories searched/saves/fetches can't be guessed wrong (see
        # _ContextAwareSummarizationMiddleware).
        prompt_template=_summary_prompt_for(discovery),
    )
    rate_limit_retry = _RateLimitRetryMiddleware()

    subagents = [
        {
            "name": "discovery",
            "description": (
                "Search for candidate businesses by category and location. Delegate one "
                "search at a time; use it to fan out searches in parallel."
            ),
            "system_prompt": DISCOVERY_SUBAGENT_PROMPT.format(
                category=discovery.lead_type, location=discovery.location
            ),
            "tools": [search_tool],
        }
    ]

    agent = create_deep_agent(
        model=model,
        tools=tools,
        system_prompt=system_prompt,
        subagents=subagents,
        middleware=[summarization, rate_limit_retry],
    )
    return agent, ctx


def run_agent(discovery: Discovery, job: Job) -> AgentContext:
    """Run the pipeline for a discovery; returns the context (with collected leads).

    The agent's step count is bounded by ``max_agent_iterations`` (passed as the
    LangGraph ``recursion_limit``); a wall-clock cap of ``job_timeout_seconds``
    guards against a single slow run.

    Providers intermittently reject a model generation with a malformed tool
    call (Groq HTTP 400 ``tool_use_failed``). That error is server-side and
    unrecoverable inside the graph, so we re-invoke the whole agent with a
    slightly higher temperature. Re-running is safe: dedupe and progress are
    DB-backed.
    """
    configure()

    task = _task_text(discovery)

    outcome: dict = {"error": None, "ctx": None}

    def _invoke() -> None:
        try:
            for temperature in _RETRY_TEMPERATURES:
                try:
                    agent, ctx = build_agent(discovery, job, temperature=temperature)
                    agent.invoke(
                        {"messages": [{"role": "user", "content": task}]},
                        config={
                            "recursion_limit": settings.max_agent_iterations,
                            **run_config(discovery, job),
                        },
                    )
                    outcome["ctx"] = ctx
                    return
                except Exception as exc:
                    if not _is_tool_use_failure(exc):
                        raise
                    print(
                        "Malformed tool-call generation rejected by the provider; "
                        f"retrying with temperature={temperature} ({exc})"
                    )
        except Exception as exc:  # noqa: BLE001 — re-raised in the caller's thread
            if _is_rate_limit_error(exc):
                exc = RuntimeError(
                    "The discovery agent repeatedly hit the provider's rate limit "
                    f"for `{settings.llm_model}` on `{settings.llm_provider}`. Even "
                    "with compaction the request budget was too small for a full "
                    "multi-category sweep. Upgrade the plan/tier for this provider, "
                    "or set LLM_PROVIDER / LLM_MODEL in backend/.env to a provider "
                    "with a higher request budget, then rerun the job."
                )
            outcome["error"] = exc

    thread = threading.Thread(target=_invoke, daemon=True)
    thread.start()
    thread.join(timeout=settings.job_timeout_seconds)

    if thread.is_alive():
        raise RuntimeError(f"Job timed out after {settings.job_timeout_seconds}s")
    if outcome["error"] is not None:
        raise outcome["error"]
    return outcome["ctx"]
