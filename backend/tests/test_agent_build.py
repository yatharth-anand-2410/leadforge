import pytest
from langchain_anthropic import ChatAnthropic
from langchain_google_genai import ChatGoogleGenerativeAI
from langchain_groq import ChatGroq
from langchain_openai import ChatOpenAI

from app.agent.agent import (
    _SUMMARY_KEEP_TOKENS,
    _SUMMARY_TRIGGER_TOKENS,
    _SUMMARY_TRIM_TOKENS,
    _brief_text,
    _build_chat_model,
    _ContextAwareSummarizationMiddleware,
    _is_rate_limit_error,
    _is_tool_use_failure,
    _pipeline_state_text,
    _RateLimitRetryMiddleware,
    _resolve_category,
    _summary_thresholds,
    _task_text,
    build_agent,
)
from app.agent.prompts import DISCOVERY_SUBAGENT_PROMPT, ORCHESTRATOR_PROMPT
from app.models.discovery import Discovery
from app.models.job import Job


@pytest.fixture(autouse=True)
def _default_groq_provider(monkeypatch):
    """Pin the provider to groq so tests are independent of the local .env.

    The `.env` can set ``LLM_PROVIDER`` (e.g. to gemini) which would otherwise
    change which model ``build_agent`` constructs and which summarization
    thresholds it uses. Tests that exercise another provider override it
    explicitly.
    """
    monkeypatch.setattr("app.agent.agent.settings.llm_provider", "groq")


def _discovery(**overrides):
    base = dict(
        id=1,
        user_id=1,
        lead_type="Dental Clinics",
        location="Bengaluru, India",
        industry="Healthcare",
        keywords=["implant", "orthodontist"],
        exclude_keywords=["franchise"],
        num_leads=5,
    )
    base.update(overrides)
    return Discovery(**base)


def _job():
    return Job(id=1, discovery_id=1, user_id=1, status="running")


def test_build_agent_constructs(monkeypatch):
    monkeypatch.setattr("app.agent.agent.settings.groq_api_key", "dummy")
    agent, ctx = build_agent(_discovery(), _job())

    assert agent is not None
    assert ctx.category == "Dental Clinics"
    assert ctx.location == "Bengaluru, India"
    assert ctx.num_leads == 5
    assert ctx.exclude_keywords == ["franchise"]


def test_build_agent_handles_null_filters(monkeypatch):
    monkeypatch.setattr("app.agent.agent.settings.groq_api_key", "dummy")
    agent, ctx = build_agent(
        _discovery(industry=None, keywords=None, exclude_keywords=None),
        _job(),
    )
    assert agent is not None
    assert ctx.exclude_keywords == []


def test_tool_use_failure_classifier():
    assert _is_tool_use_failure(RuntimeError("... tool_use_failed ..."))
    assert _is_tool_use_failure(RuntimeError("... failed_generation ..."))
    assert not _is_tool_use_failure(RuntimeError("Rate limit exceeded"))


def test_orchestrator_prompt_forbids_premature_zero_lead_conclusion():
    assert 'NEVER conclude "zero leads" after a single search' in ORCHESTRATOR_PROMPT
    assert "at least 5 DIFFERENT supported categories" in ORCHESTRATOR_PROMPT
    assert "BUYERS" in ORCHESTRATOR_PROMPT
    assert "NEVER interpret the ICP as the service PROVIDERS" in ORCHESTRATOR_PROMPT


def test_orchestrator_prompt_pins_single_category_icp():
    """A single-category ICP must never spread into unrelated categories."""
    assert "SINGLE-CATEGORY ICP" in ORCHESTRATOR_PROMPT
    assert "NEVER spread into unrelated categories" in ORCHESTRATOR_PROMPT
    assert "refuse off-target" in ORCHESTRATOR_PROMPT.lower() or "off-target" in ORCHESTRATOR_PROMPT
    assert "NEVER save a lead outside that vertical" in ORCHESTRATOR_PROMPT


def test_brief_text_empty_without_brief():
    assert _brief_text(_discovery()) == ""


def test_brief_text_marks_brief_authoritative():
    text = _brief_text(
        _discovery(brief="Find camera shops in Kathmandu for a POS system.")
    )
    assert "USER BRIEF (AUTHORITATIVE" in text
    assert "camera shops in Kathmandu" in text


def test_task_text_uses_brief():
    text = _task_text(_discovery(brief="Find cafes in Lisbon needing loyalty apps."))
    assert "user brief" in text
    assert "loyalty apps" in text


def test_task_text_structured_without_brief():
    text = _task_text(_discovery())
    assert "Dental Clinics in Bengaluru, India" in text


def test_subagent_prompt_handles_non_supported_category():
    assert "closest supported category" in DISCOVERY_SUBAGENT_PROMPT


def test_resolve_category_prefers_lead_type():
    assert _resolve_category(_discovery()) == "Dental Clinics"


def test_resolve_category_derives_from_brief():
    d = _discovery(
        lead_type="",
        location="",
        brief="I want dental clinics in new zealand that is looking for website development",
    )
    assert _resolve_category(d) == "dental"


def test_resolve_category_empty_for_broad_brief():
    d = _discovery(
        lead_type="",
        location="",
        brief="SMEs looking for digital marketing and growth services",
    )
    assert _resolve_category(d) == ""


def test_build_agent_brief_only_pins_category_and_brief(monkeypatch):
    """A brief-only discovery must surface the brief AND gate on the derived
    category, so it can't sweep and save off-target categories again."""
    monkeypatch.setattr("app.agent.agent.settings.groq_api_key", "dummy")
    captured = {}

    class _Sentinel:
        pass

    def fake_create_deep_agent(**kwargs):
        captured.update(kwargs)
        return _Sentinel()

    monkeypatch.setattr("app.agent.agent.create_deep_agent", fake_create_deep_agent)
    d = _discovery(
        lead_type="",
        location="",
        brief="I want dental clinics in new zealand that is looking for website development",
    )

    agent, ctx = build_agent(d, _job())

    assert ctx.category == "dental"
    system = captured["system_prompt"]
    assert "dental" in system
    assert "USER BRIEF (AUTHORITATIVE" in system
    assert "new zealand" in system

    from app.agent.tools import build_tools

    search = build_tools(ctx)[0]
    out = search.invoke({"category": "bakery", "location": "Auckland, New Zealand"})
    assert "does not match" in out
    assert "dental" in out


class _StatusError(Exception):
    """Exception that carries an HTTP-like status_code, as Groq's APIStatusError does."""

    status_code = 413


def test_rate_limit_error_classifier():
    """The classifier catches 413/429 rate-limit errors across providers."""
    assert _is_rate_limit_error(_StatusError("Request too large"))
    assert _is_rate_limit_error(
        RuntimeError(
            "Error code: 413 - Request too large for model `openai/gpt-oss-20b` ... "
            "on tokens per minute (TPM): Limit 8000, Requested 10113"
        )
    )
    assert _is_rate_limit_error(RuntimeError("... 'code': 'rate_limit_exceeded'"))
    assert _is_rate_limit_error(RuntimeError("tokens per minute"))
    # Gemini RESOURCE_EXHAUSTED / quota wording.
    assert _is_rate_limit_error(RuntimeError("429 RESOURCE_EXHAUSTED ... quota exceeded"))
    assert _is_rate_limit_error(RuntimeError("You exceeded your current quota"))
    # OpenAI/Anthropic-compatible 429 wording.
    assert _is_rate_limit_error(RuntimeError("429 Too Many Requests"))
    assert not _is_rate_limit_error(RuntimeError("tool_use_failed"))
    assert not _is_rate_limit_error(RuntimeError("connection refused"))


def test_groq_rate_limit_middleware_reraises_non_rate_limit():
    """Non rate-limit errors must pass straight through the retry middleware."""
    mw = _RateLimitRetryMiddleware()

    def handler(_request):
        raise RuntimeError("tool_use_failed")

    with pytest.raises(RuntimeError, match="tool_use_failed"):
        mw.wrap_model_call("request", handler)


def test_build_agent_wires_compaction_and_retry_middleware(monkeypatch):
    """build_agent must hand create_deep_agent the TPM-tuned middleware stack."""
    monkeypatch.setattr("app.agent.agent.settings.groq_api_key", "dummy")
    captured = {}

    class _Sentinel:
        pass

    def fake_create_deep_agent(**kwargs):
        captured.update(kwargs)
        return _Sentinel()

    monkeypatch.setattr("app.agent.agent.create_deep_agent", fake_create_deep_agent)

    agent, _ctx = build_agent(_discovery(), _job())

    assert isinstance(agent, _Sentinel)
    middleware = captured["middleware"]
    assert any(isinstance(mw, _RateLimitRetryMiddleware) for mw in middleware)
    summarization = next(mw for mw in middleware if mw.name == "SummarizationMiddleware")
    assert summarization._lc_helper.trigger == ("tokens", _SUMMARY_TRIGGER_TOKENS)
    assert summarization._lc_helper.keep == ("tokens", _SUMMARY_KEEP_TOKENS)
    assert summarization._lc_helper.trim_tokens_to_summarize == _SUMMARY_TRIM_TOKENS


def test_summary_prompt_pins_task_and_keeps_placeholder(monkeypatch):
    """The custom summarization prompt must restate the ORIGINAL task verbatim
    (so compaction can't cause task drift) and keep the ``{messages}`` runtime
    placeholder that LangChain fills with the conversation history."""
    monkeypatch.setattr("app.agent.agent.settings.groq_api_key", "dummy")
    captured = {}

    class _Sentinel:
        pass

    def fake_create_deep_agent(**kwargs):
        captured.update(kwargs)
        return _Sentinel()

    monkeypatch.setattr("app.agent.agent.create_deep_agent", fake_create_deep_agent)

    build_agent(_discovery(), _job())

    summarization = next(
        mw for mw in captured["middleware"] if mw.name == "SummarizationMiddleware"
    )
    prompt = summarization._lc_helper.summary_prompt
    assert "Run the lead discovery pipeline for: Dental Clinics in Bengaluru, India" in prompt
    assert "Collect up to 5 shortlisted leads" in prompt
    assert "PIPELINE STATE" in prompt
    assert "{messages}" in prompt


def test_pipeline_state_text_reflects_live_context(monkeypatch):
    """The injected pipeline state must come from AgentContext, not be guessed."""
    monkeypatch.setattr("app.agent.agent.settings.groq_api_key", "dummy")
    _agent, ctx = build_agent(_discovery(exclude_keywords=["franchise", "chain"]), _job())

    ctx.searched_categories.add("restaurant")
    ctx.searched_categories.add("cafe")
    ctx.fetched_urls.add("https://a.example.com")
    ctx.save_attempts = 2

    state = _pipeline_state_text(ctx)

    assert "restaurant" in state
    assert "cafe" in state
    assert "already searched (2)" in state.lower() or "(2):" in state
    assert "franchise" in state and "chain" in state
    assert "websites already fetched: 1" in state.lower()
    assert "save attempts so far: 2" in state.lower()
    # Restaurant/cafe must not also show up as "not yet searched".
    not_yet_section = state.split("NOT yet searched")[1].split("\n")[0]
    assert "restaurant" not in not_yet_section
    assert "cafe" not in not_yet_section


def test_summarization_middleware_appends_resync_directive(monkeypatch):
    """After compaction, a deterministic directive must tell the agent to
    reload ground truth via `progress` instead of trusting the summary."""
    monkeypatch.setattr("app.agent.agent.settings.groq_api_key", "dummy")
    captured = {}

    class _Sentinel:
        pass

    def fake_create_deep_agent(**kwargs):
        captured.update(kwargs)
        return _Sentinel()

    monkeypatch.setattr("app.agent.agent.create_deep_agent", fake_create_deep_agent)
    build_agent(_discovery(), _job())

    summarization = next(
        mw for mw in captured["middleware"] if mw.name == "SummarizationMiddleware"
    )
    assert isinstance(summarization, _ContextAwareSummarizationMiddleware)

    messages = summarization._build_new_messages_with_path("a summary", None)
    assert len(messages) == 2
    assert "progress" in messages[-1].content
    assert "before your next" in messages[-1].content.lower()


def _configure_provider(monkeypatch, provider: str):
    monkeypatch.setattr("app.agent.agent.settings.llm_provider", provider)
    monkeypatch.setattr("app.agent.agent.settings.llm_model", "some-model")
    monkeypatch.setattr("app.agent.agent.settings.groq_api_key", "groq-key")
    monkeypatch.setattr("app.agent.agent.settings.gemini_api_key", "gemini-key")
    monkeypatch.setattr("app.agent.agent.settings.opencode_api_key", "opencode-key")
    monkeypatch.setattr(
        "app.agent.agent.settings.opencode_base_url", "https://opencode.ai/zen/go/v1"
    )


def test_build_chat_model_groq(monkeypatch):
    _configure_provider(monkeypatch, "groq")
    model = _build_chat_model(0.0)
    assert isinstance(model, ChatGroq)
    assert model.model_name == "some-model"


def test_build_chat_model_gemini(monkeypatch):
    _configure_provider(monkeypatch, "gemini")
    model = _build_chat_model(0.0)
    assert isinstance(model, ChatGoogleGenerativeAI)
    assert model.model == "some-model"


def test_build_chat_model_opencode_openai(monkeypatch):
    _configure_provider(monkeypatch, "opencode-openai")
    model = _build_chat_model(0.0)
    assert isinstance(model, ChatOpenAI)
    assert model.model_name == "some-model"
    assert model.openai_api_base == "https://opencode.ai/zen/go/v1"


def test_build_chat_model_opencode_anthropic(monkeypatch):
    _configure_provider(monkeypatch, "opencode-anthropic")
    model = _build_chat_model(0.0)
    assert isinstance(model, ChatAnthropic)
    assert model.model == "some-model"
    assert model.anthropic_api_url == "https://opencode.ai/zen/go/v1"


def test_build_chat_model_unknown_provider_raises(monkeypatch):
    _configure_provider(monkeypatch, "does-not-exist")
    with pytest.raises(ValueError, match="Unknown LLM_PROVIDER"):
        _build_chat_model(0.0)


def test_summary_thresholds_groq_vs_other(monkeypatch):
    monkeypatch.setattr("app.agent.agent.settings.llm_provider", "groq")
    assert _summary_thresholds() == (
        _SUMMARY_TRIGGER_TOKENS,
        _SUMMARY_KEEP_TOKENS,
        _SUMMARY_TRIM_TOKENS,
    )
    monkeypatch.setattr("app.agent.agent.settings.llm_provider", "gemini")
    gemini = _summary_thresholds()
    assert gemini[0] > _SUMMARY_TRIGGER_TOKENS
    assert gemini[1] > _SUMMARY_KEEP_TOKENS

    monkeypatch.setattr("app.agent.agent.settings.llm_provider", "opencode-openai")
    opencode = _summary_thresholds()
    assert opencode[0] > _SUMMARY_TRIGGER_TOKENS
    assert opencode[0] < gemini[0]
