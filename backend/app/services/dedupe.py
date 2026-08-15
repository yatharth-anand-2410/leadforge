"""Deduplication helpers: name normalization, domain extraction, canonical keys."""

import re
from urllib.parse import urlparse

_LEGAL_SUFFIXES = re.compile(
    r"\b(inc|llc|ltd|plc|llp|co|corp|corporation|company|pvt|private|limited|gmbh|ag|sa|sarl|bv|nv|oy|ab)\b",
    re.IGNORECASE,
)
_NON_ALNUM = re.compile(r"[^a-z0-9]+")


def normalize_name(name: str) -> str:
    """Lowercase, strip legal suffixes and punctuation for fuzzy comparison."""
    if not name:
        return ""
    lowered = name.lower()
    lowered = _LEGAL_SUFFIXES.sub(" ", lowered)
    lowered = _NON_ALNUM.sub(" ", lowered)
    return " ".join(lowered.split())


def domain_from_url(url: str) -> str | None:
    """Extract a normalized domain from a URL, or return None."""
    if not url:
        return None
    url = url.strip()
    if not url.startswith(("http://", "https://")):
        url = "https://" + url
    try:
        host = urlparse(url).hostname
    except ValueError:
        return None
    if not host:
        return None
    host = host.lower()
    if host.startswith("www."):
        host = host[4:]
    return host


def make_dedupe_key(name: str, website: str | None = None) -> str | None:
    """Canonical identity for a lead: domain if known, else normalized name."""
    domain = domain_from_url(website)
    if domain:
        return f"domain:{domain}"
    norm = normalize_name(name)
    if norm:
        return f"name:{norm}"
    return None
