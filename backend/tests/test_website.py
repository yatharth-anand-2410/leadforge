import httpx

import app.services.website as website
from app.services.website import extract_contacts, extract_emails, extract_phones

HTML = """
<html>
  <head><title>Acme Dental</title></head>
  <body>
    <a href="mailto:info@acmedental.com">Email us</a>
    <a href="https://facebook.com/acmedental">Facebook</a>
    <a href="https://www.linkedin.com/company/acmedental">LinkedIn</a>
    Call us at +1 (555) 123-4567 or 080 1234 5678.
  </body>
</html>
"""


def test_extract_contacts():
    data = extract_contacts(HTML)
    assert data["title"] == "Acme Dental"
    assert "info@acmedental.com" in data["emails"]
    assert "facebook.com" in data["socials"]
    assert "linkedin.com" in data["socials"]
    assert any("555" in p for p in data["phones"])


def test_extract_emails_skips_images():
    emails = extract_emails("logo@2x.png email real@site.com")
    assert "real@site.com" in emails
    assert "logo@2x.png" not in emails


def test_extract_phones_skips_years():
    phones = extract_phones("Founded in 2020. Call 080 1234 5678")
    assert any("080" in p for p in phones)


class _StubResponse:
    def __init__(self, status_code, content_type, body="", url="https://acme.example.com/"):
        self.status_code = status_code
        self.headers = {"content-type": content_type}
        self.text = body
        self.url = url


class _StubClient:
    """httpx.Client stand-in that returns a fixed response or raises."""

    def __init__(self, response=None, error=None):
        self._response = response
        self._error = error

    def __enter__(self):
        return self

    def __exit__(self, *args):
        return False

    def get(self, url):
        if self._error is not None:
            raise self._error
        return self._response


def test_check_website_reachable(monkeypatch):
    resp = _StubResponse(
        200,
        "text/html; charset=utf-8",
        body="<html><head><title>Acme Dental</title></head></html>",
    )
    monkeypatch.setattr(website.httpx, "Client", lambda *a, **k: _StubClient(response=resp))
    out = website.check_website("acme.example.com")
    assert out["reachable"] is True
    assert out["status"] == 200
    assert out["title"] == "Acme Dental"
    assert out["error"] is None


def test_check_website_unreachable_on_http_error(monkeypatch):
    monkeypatch.setattr(
        website.httpx, "Client", lambda *a, **k: _StubClient(error=httpx.ConnectError("refused"))
    )
    out = website.check_website("https://down.example.com")
    assert out["reachable"] is False
    assert out["error"] == "ConnectError"
    assert out["status"] is None


def test_check_website_not_reachable_when_not_html(monkeypatch):
    monkeypatch.setattr(
        website.httpx,
        "Client",
        lambda *a, **k: _StubClient(response=_StubResponse(200, "application/json", body="{}")),
    )
    out = website.check_website("https://acme.example.com")
    assert out["reachable"] is False


def test_check_website_not_reachable_on_error_status(monkeypatch):
    monkeypatch.setattr(
        website.httpx,
        "Client",
        lambda *a, **k: _StubClient(response=_StubResponse(503, "text/html", body="<html></html>")),
    )
    out = website.check_website("https://acme.example.com")
    assert out["reachable"] is False
    assert out["status"] == 503


def test_check_website_empty_url():
    out = website.check_website("")
    assert out["reachable"] is False
