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


def test_normalize_phone_osm_multi_value():
    """OSM phone tags are ';'-joined multi-values; each must be handled alone
    instead of concatenating digits (the bug that rejected Mr. Litti)."""
    assert verify.normalize_phone("+91-93343-18675;+91-93347-42008") == "+919334318675"
    assert verify.normalize_phone("+1 (555) 123-4567;+1 (555) 987-6543") == "+15551234567"
    # Comma/pipe/slash separators and empty chunks are tolerated too.
    assert verify.normalize_phone("+44 20 7946 0958, +44 20 7946 0959") == "+442079460958"
    assert verify.normalize_phone(";;+34 600 123 456;;") == "+34600123456"


def test_normalize_phone_international_00_prefix():
    """'00' CEPT dialing prefix maps to '+' (common in Indian OSM data)."""
    assert verify.normalize_phone("00919334318675") == "+919334318675"


def test_normalize_phone_first_valid_chunk_wins():
    assert verify.normalize_phone("12;+91-93343-18675;34") == "+919334318675"


def test_normalize_phone_all_invalid_chunks_returns_none():
    assert verify.normalize_phone("12;34") is None
    assert verify.normalize_phone("") is None
    assert verify.normalize_phone(None) is None


def test_infer_country_code():
    assert verify.infer_country_code("Patna, Bihar, India") == "IN"
    assert verify.infer_country_code("Bengaluru, India") == "IN"
    assert verify.infer_country_code("Phoenix, Arizona, USA") == "US"
    assert verify.infer_country_code("Dubai, UAE") == "AE"
    assert verify.infer_country_code("") is None
    assert verify.infer_country_code(None) is None


def test_normalize_phone_prepends_country_code_to_bare_indian_number():
    """A bare 10-digit Indian mobile (no +, no 00) must get +91 attached."""
    assert verify.normalize_phone("8409592722", "IN") == "+918409592722"
    assert verify.normalize_phone("8409592722;8210691911", "IN") == "+918409592722"


def test_normalize_phone_rejects_truncated_indian_landline():
    """'2361514' is a 7-digit landline missing the Patna 0612 STD code."""
    assert verify.normalize_phone("2361514", "IN") is None
    assert verify.normalize_phone("+91 2361514", "IN") is None
    assert verify.normalize_phone("+912361514") is None


def test_validate_phone_explains_indian_truncation():
    result = verify.validate_phone("2361514", "IN")
    assert result["valid"] is False
    assert "STD" in result["reason"] or "landline" in result["reason"]


def test_normalize_phone_accepts_complete_indian_landline():
    """'0612 2361514' carries the STD code and is a dialable landline."""
    assert verify.normalize_phone("0612 2361514", "IN") == "+916122361514"


def test_normalize_phone_mobile_with_country_code_still_valid():
    assert verify.normalize_phone("+919334318675", "IN") == "+919334318675"
    assert verify.normalize_phone("+919334318675") == "+919334318675"


def test_normalize_phone_without_country_keeps_generic_behavior():
    """Unknown-country numbers keep the old lenient 7-15 digit handling."""
    assert verify.normalize_phone("080 1234 5678") == "08012345678"
    assert verify.normalize_phone("+1 (555) 123-4567", "US") == "+15551234567"


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
