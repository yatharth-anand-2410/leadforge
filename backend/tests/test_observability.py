import os

from app.config import settings
from app.observability import configure, run_config

_LS_VARS = [
    "LANGSMITH_TRACING",
    "LANGSMITH_API_KEY",
    "LANGSMITH_PROJECT",
    "LANGSMITH_ENDPOINT",
]


def _clear(monkeypatch):
    for var in _LS_VARS:
        monkeypatch.delenv(var, raising=False)


def test_configure_noop_when_disabled(monkeypatch):
    _clear(monkeypatch)
    monkeypatch.setattr(settings, "langsmith_tracing", False)
    monkeypatch.setattr(settings, "langsmith_api_key", "lsv2_test")
    configure()
    assert os.environ.get("LANGSMITH_TRACING") is None


def test_configure_noop_without_key(monkeypatch):
    _clear(monkeypatch)
    monkeypatch.setattr(settings, "langsmith_tracing", True)
    monkeypatch.setattr(settings, "langsmith_api_key", "")
    configure()
    assert os.environ.get("LANGSMITH_TRACING") is None


def test_configure_sets_env(monkeypatch):
    _clear(monkeypatch)
    monkeypatch.setattr(settings, "langsmith_tracing", True)
    monkeypatch.setattr(settings, "langsmith_api_key", "lsv2_test")
    monkeypatch.setattr(settings, "langsmith_project", "leadforge-test")
    monkeypatch.setattr(settings, "langsmith_endpoint", "")
    configure()
    assert os.environ["LANGSMITH_TRACING"] == "true"
    assert os.environ["LANGSMITH_API_KEY"] == "lsv2_test"
    assert os.environ["LANGSMITH_PROJECT"] == "leadforge-test"
    assert os.environ.get("LANGSMITH_ENDPOINT") is None


def test_configure_sets_endpoint(monkeypatch):
    _clear(monkeypatch)
    monkeypatch.setattr(settings, "langsmith_tracing", True)
    monkeypatch.setattr(settings, "langsmith_api_key", "lsv2_test")
    monkeypatch.setattr(settings, "langsmith_endpoint", "https://eu.api.smith.langchain.com")
    configure()
    assert os.environ["LANGSMITH_ENDPOINT"] == "https://eu.api.smith.langchain.com"


def test_run_config_metadata():
    class Discovery:
        id = 7
        user_id = 3
        brief = "Dentist clinics in Patna"
        num_leads = 10

    class Job:
        id = 42

    cfg = run_config(Discovery(), Job())
    assert cfg["run_name"] == "discovery-7-job-42"
    assert cfg["metadata"] == {
        "discovery_id": 7,
        "job_id": 42,
        "user_id": 3,
        "brief": "Dentist clinics in Patna",
        "num_leads": 10,
    }
    assert cfg["tags"] == ["leadforge", "discovery:7", "user:3"]
