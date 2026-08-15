"""Company website crawling: fetch pages and extract contact points."""

import re

import httpx
from bs4 import BeautifulSoup

EMAIL_RE = re.compile(r"[a-zA-Z0-9._%+\-]+@[a-zA-Z0-9.\-]+\.[a-zA-Z]{2,}")
PHONE_RE = re.compile(r"(?:\+?[0-9][0-9 \-.()]{6,}[0-9])")
_SKIP_EMAIL_DOMAINS = {
    "sentry.io",
    "wixpress.com",
    "example.com",
    "godaddy.com",
    "yourdomain.com",
    "sentry-next.wixpress.com",
}
SOCIAL_DOMAINS = ["facebook.com", "linkedin.com", "twitter.com", "x.com", "instagram.com", "youtube.com"]

_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/124.0 Safari/537.36 LeadForge/0.1"
    )
}


def extract_emails(text: str) -> list[str]:
    found: set[str] = set()
    for match in EMAIL_RE.finditer(text):
        email = match.group(0).lower().strip(".,;:\"'()[]<>")
        if email.endswith((".png", ".jpg", ".jpeg", ".gif", ".webp")) or "@2x" in email or "@3x" in email:
            continue
        if "@" not in email:
            continue
        domain = email.rsplit("@", 1)[1]
        if domain in _SKIP_EMAIL_DOMAINS:
            continue
        found.add(email)
    return sorted(found)


def extract_phones(text: str) -> list[str]:
    found: set[str] = set()
    for match in PHONE_RE.finditer(text):
        candidate = match.group(0).strip()
        digits = re.sub(r"\D", "", candidate)
        if 7 <= len(digits) <= 15 and not re.match(r"^(19|20)\d{2}$", digits):
            found.add(candidate)
    return sorted(found)


def extract_socials(soup: BeautifulSoup) -> dict[str, str]:
    found: dict[str, str] = {}
    for anchor in soup.find_all("a", href=True):
        href = anchor["href"]
        for domain in SOCIAL_DOMAINS:
            if domain in href and domain not in found:
                found[domain] = href
    return found


def extract_contacts(html: str) -> dict:
    """Parse HTML and extract title, emails, phones, and social links."""
    soup = BeautifulSoup(html, "html.parser")
    text = soup.get_text(" ", strip=True)
    # Include mailto: hrefs (emails are often only present as links).
    mailto = [
        a["href"]
        for a in soup.find_all("a", href=True)
        if a["href"].strip().lower().startswith("mailto:")
    ]
    text = text + " " + " ".join(mailto)
    title = (soup.title.string or "").strip() if soup.title and soup.title.string else ""
    return {
        "title": title,
        "emails": extract_emails(text),
        "phones": extract_phones(text),
        "socials": extract_socials(soup),
    }


def fetch_website(url: str, timeout: float = 10.0) -> dict:
    """Crawl homepage + common contact pages and aggregate contact points."""
    result: dict = {"url": url, "title": "", "emails": [], "phones": [], "socials": {}}
    if not url:
        return result

    if not url.startswith(("http://", "https://")):
        url = "https://" + url

    emails: set[str] = set()
    phones: set[str] = set()
    socials: dict[str, str] = {}

    paths = ["", "contact", "about", "contact-us"]
    with httpx.Client(timeout=timeout, follow_redirects=True, headers=_HEADERS) as client:
        for path in paths:
            target = url.rstrip("/") + (f"/{path}" if path else "")
            try:
                resp = client.get(target)
                if resp.status_code != 200 or "text/html" not in resp.headers.get("content-type", ""):
                    continue
                data = extract_contacts(resp.text)
                emails.update(data["emails"])
                phones.update(data["phones"])
                socials.update(data["socials"])
                if not result["title"] and data["title"]:
                    result["title"] = data["title"]
            except httpx.HTTPError:
                continue

    result["emails"] = sorted(emails)
    result["phones"] = sorted(phones)
    result["socials"] = socials
    return result
