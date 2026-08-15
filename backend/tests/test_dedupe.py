from app.services.dedupe import domain_from_url, make_dedupe_key, normalize_name


def test_normalize_name():
    assert normalize_name("Acme Dental Pvt. Ltd.") == "acme dental"
    assert normalize_name("  SmileCare, Inc.  ") == "smilecare"


def test_domain_from_url():
    assert domain_from_url("https://www.acmedental.com/contact") == "acmedental.com"
    assert domain_from_url("acmedental.com") == "acmedental.com"
    assert domain_from_url(None) is None


def test_make_dedupe_key_prefers_domain():
    assert make_dedupe_key("Acme Dental", "https://www.acmedental.com") == "domain:acmedental.com"


def test_make_dedupe_key_falls_back_to_name():
    assert make_dedupe_key("Acme Dental", None) == "name:acme dental"


def test_make_dedupe_key_none():
    assert make_dedupe_key("", None) is None
