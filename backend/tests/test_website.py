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
