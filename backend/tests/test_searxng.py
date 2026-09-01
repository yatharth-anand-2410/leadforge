import dataclasses

import httpx
import pytest

from app.services import searxng
from app.services.searxng import _parse_results, search_web


@pytest.fixture(autouse=True)
def _no_throttle(monkeypatch):
    monkeypatch.setattr(searxng, "_throttle", lambda: None)


@dataclasses.dataclass
class _FakeResp:
    status_code: int
    payload: dict | None = None
    json_exc: Exception | None = None

    headers: dict = dataclasses.field(default_factory=dict)

    def raise_for_status(self):
        if self.status_code >= 400:
            raise httpx.HTTPStatusError(
                f"HTTP {self.status_code}",
                request=httpx.Request("GET", "http://searx"),
                response=httpx.Response(self.status_code, request=httpx.Request("GET", "http://searx")),
            )

    def json(self):
        if self.json_exc:
            raise self.json_exc
        return self.payload


@dataclasses.dataclass
class _FakeClient:
    resp: _FakeResp

    calls: list = dataclasses.field(default_factory=list)

    def get(self, url, params=None, headers=None):
        self.calls.append((url, params))
        return self.resp

    def close(self):
        pass


def _enable(monkeypatch):
    monkeypatch.setattr(searxng.settings, "searxng_endpoint", "https://searx.be")


def test_to_candidate_cleans_title_and_extracts_contacts():
    candidate = searxng._to_candidate(
        {
            "url": "https://smiledental.example.com",
            "title": "Smile Dental Clinic | Best Dentist in Bengaluru - Book Now",
            "content": "Call +91-98765-43210 or email hello@smiledental.example.com for appointments.",
        },
        "Dental Clinics",
    )
    assert candidate["name"] == "Smile Dental Clinic"
    assert candidate["category"] == "Dental Clinics"
    assert candidate["website"] == "https://smiledental.example.com"
    assert candidate["source"] == "searxng"
    assert candidate["source_id"] == "https://smiledental.example.com"
    assert candidate["email"] == "hello@smiledental.example.com"
    assert candidate["phone"] == "+91-98765-43210"
    assert candidate["lat"] is None and candidate["lon"] is None
    assert candidate["address"] is None


def test_to_candidate_falls_back_to_domain_name():
    candidate = searxng._to_candidate({"url": "https://www.example.com", "title": "", "content": ""}, "cat")
    assert candidate["name"] == "example.com"


def test_parse_results_filters_aggregators_and_documents():
    data = {
        "results": [
            {"url": "https://facebook.com/acme", "title": "Acme - Facebook", "content": ""},
            {"url": "https://example.com/acme-catalog.pdf", "title": "Acme PDF", "content": ""},
            {"url": "https://acme.example.com", "title": "Acme Consulting | Home", "content": ""},
        ]
    }
    parsed = _parse_results(data, "Consulting", 10)
    assert len(parsed) == 1
    assert parsed[0]["website"] == "https://acme.example.com"


def test_search_web_disabled_returns_empty(monkeypatch):
    monkeypatch.setattr(searxng.settings, "searxng_endpoint", "")
    assert search_web("Dental Clinics Bengaluru", client=_FakeClient(_FakeResp(200, {"results": []}))) == []


def test_search_web_builds_query_and_parses(monkeypatch):
    _enable(monkeypatch)
    client = _FakeClient(
        _FakeResp(
            200,
            {
                "results": [
                    {
                        "url": "https://smile.example.com",
                        "title": "Smile Dental | Home",
                        "content": "contact@smile.example.com",
                    }
                ]
            },
        )
    )
    out = search_web("Dental Clinics Bengaluru", category="Dental Clinics", client=client)
    assert client.calls[0][0] == "https://searx.be/search"
    assert client.calls[0][1]["q"] == "Dental Clinics Bengaluru"
    assert client.calls[0][1]["format"] == "json"
    assert out[0]["name"] == "Smile Dental"
    assert out[0]["category"] == "Dental Clinics"


def test_search_web_retries_transient_errors(monkeypatch):
    _enable(monkeypatch)
    client = _FakeClient(_FakeResp(429, None))
    assert search_web("q", client=client) == []
    assert len(client.calls) == 4  # 1 initial + 3 retries


def test_search_web_403_returns_empty_without_retries(monkeypatch):
    _enable(monkeypatch)
    client = _FakeClient(_FakeResp(403, None))
    assert search_web("q", client=client) == []
    assert len(client.calls) == 1


def test_search_web_bad_json_returns_empty(monkeypatch):
    _enable(monkeypatch)
    client = _FakeClient(_FakeResp(200, None, json_exc=ValueError("no json")))
    assert search_web("q", client=client) == []


def test_search_web_warns_when_unresponsive_engines(monkeypatch, capsys):
    """A healthy 200/JSON body whose engines all failed must surface a warning."""
    _enable(monkeypatch)
    searxng._warned_endpoints.clear()
    client = _FakeClient(
        _FakeResp(
            200,
            {
                "results": [],
                "unresponsive_engines": [["brave", "Suspended: CAPTCHA"]],
            },
        )
    )
    assert search_web("q", client=client) == []
    captured = capsys.readouterr().out
    assert "upstream engines blocked/suspended" in captured


def test_search_web_does_not_warn_when_results_returned(monkeypatch, capsys):
    """Engines that answered must not trigger the degradation warning."""
    _enable(monkeypatch)
    searxng._warned_endpoints.clear()
    client = _FakeClient(
        _FakeResp(
            200,
            {
                "results": [{"url": "https://a.example.com", "title": "A | Home", "content": ""}],
                "unresponsive_engines": [["brave", "Suspended: CAPTCHA"]],
            },
        )
    )
    assert search_web("q", client=client) != []
    assert "upstream engines" not in capsys.readouterr().out


def test_lookup_returns_raw_results_with_snippets(monkeypatch):
    """lookup() must keep full snippets and social hosts (unlike search_web)."""
    _enable(monkeypatch)
    client = _FakeClient(
        _FakeResp(
            200,
            {
                "results": [
                    {"url": "https://whitbydental.example.com", "title": "Whitby Dental", "content": "snippet a"},
                    {"url": "https://www.facebook.com/whitbydental", "title": "FB", "content": "snippet b"},
                ]
            },
        )
    )
    out = searxng.lookup("Whitby Dental Porirua", limit=5, client=client)
    assert len(out) == 2
    assert out[0] == {"url": "https://whitbydental.example.com", "title": "Whitby Dental", "content": "snippet a"}
    assert out[1]["url"] == "https://www.facebook.com/whitbydental"


def test_lookup_disabled_returns_empty(monkeypatch):
    monkeypatch.setattr(searxng.settings, "searxng_endpoint", "")
    assert searxng.lookup("q", client=_FakeClient(_FakeResp(200, {"results": []}))) == []