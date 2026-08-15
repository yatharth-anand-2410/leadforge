import app.services.verify as verify


def test_email_format_valid():
    assert verify.is_valid_email_format("info@acmedental.com") is True


def test_email_format_invalid():
    assert verify.is_valid_email_format("not-an-email") is False
    assert verify.is_valid_email_format("") is False


def test_normalize_phone():
    assert verify.normalize_phone("+1 (555) 123-4567") == "+15551234567"
    assert verify.normalize_phone("080 1234 5678") == "08012345678"
    assert verify.normalize_phone("12") is None  # too short


def test_verify_email_format_only(monkeypatch):
    monkeypatch.setattr(verify, "email_domain_has_mx", lambda domain: False)
    result = verify.verify_email("info@acmedental.com")
    assert result["format_valid"] is True
    assert result["mx_ok"] is False
    assert result["verified"] is False


def test_verify_email_with_mx(monkeypatch):
    monkeypatch.setattr(verify, "email_domain_has_mx", lambda domain: True)
    result = verify.verify_email("info@acmedental.com")
    assert result["verified"] is True


def test_email_domain_has_mx(monkeypatch):
    class _Answers:
        def __len__(self):
            return 1

    monkeypatch.setattr(verify.dns.resolver, "resolve", lambda *a, **k: _Answers())
    assert verify.email_domain_has_mx("acmedental.com") is True
