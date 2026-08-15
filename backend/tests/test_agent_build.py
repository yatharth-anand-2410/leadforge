from app.agent.agent import build_agent
from app.models.discovery import Discovery
from app.models.job import Job


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
