"""Contact verification: email format + MX lookup, country-aware phone normalization."""

import re

import dns.resolver

EMAIL_RE = re.compile(r"^[a-zA-Z0-9._%+\-]+@[a-zA-Z0-9.\-]+\.[a-zA-Z]{2,}$")


def is_valid_email_format(email: str) -> bool:
    return bool(EMAIL_RE.match(email or ""))


def email_domain_has_mx(domain: str) -> bool:
    try:
        answers = dns.resolver.resolve(domain, "MX", lifetime=3)
        return len(answers) > 0
    except Exception:  # noqa: BLE001 — NXDOMAIN / NoAnswer / timeout all mean "no MX"
        return False


def verify_email(email: str) -> dict:
    if not email:
        return {"email": email, "format_valid": False, "mx_ok": False, "verified": False}

    fmt = is_valid_email_format(email)
    mx = False
    if fmt:
        domain = email.rsplit("@", 1)[1]
        mx = email_domain_has_mx(domain)

    return {"email": email, "format_valid": fmt, "mx_ok": mx, "verified": fmt and mx}


# OSM phone tags are semicolon-separated multi-value strings, e.g.
# "+91-93343-18675;+91-93347-42008". Splitting on these (plus comma/slash/pipe)
# keeps each candidate independent instead of concatenating digits across values.
_PHONE_VALUE_SEP = re.compile(r"[;,/|]")

# International dialing prefix written without '+', e.g. "00919334318675".
_00_PREFIX = re.compile(r"^00")

# ISO country code -> ITU dialing code for the countries the pipeline targets.
_COUNTRY_DIALING = {
    "IN": "91",
    "US": "1",
    "CA": "1",
    "GB": "44",
    "AU": "61",
    "AE": "971",
    "SG": "65",
    "NZ": "64",
}

# Substrings that pin a free-text location (e.g. "Patna, Bihar, India") to a
# country, so a bare national number gets the right dialing code.
_COUNTRY_HINTS = {
    "IN": (
        "india", "indian", "bihar", "patna", "mumbai", "delhi", "ncr", "bengaluru",
        "bangalore", "pune", "hyderabad", "chennai", "kolkata", "jaipur", "kerala",
        "gujarat", "punjab", "uttar pradesh", "rajasthan", "lucknow", "kanpur",
        "gurgaon", "noida",
    ),
    "US": (
        "united states", "usa", "u.s.", "america", "california", "texas", "new york",
        "phoenix", "florida", "chicago",
    ),
    "GB": (
        "united kingdom", "uk", "u.k.", "britain", "england", "london", "scotland", "wales",
    ),
    "CA": ("canada", "ontario", "toronto", "vancouver", "quebec"),
    "AU": ("australia", "sydney", "melbourne", "brisbane", "perth"),
    "AE": ("united arab emirates", "uae", "dubai", "abu dhabi", "sharjah"),
    "SG": ("singapore",),
    "NZ": ("new zealand", "nz", "auckland", "wellington", "christchurch", "hamilton"),
}


def infer_country_code(location: str) -> str | None:
    """Best-effort ISO country code for a free-text location, or None."""
    if not location:
        return None
    text = " " + location.lower() + " "
    for code, hints in _COUNTRY_HINTS.items():
        if any(hint in text for hint in hints):
            return code
    return None


# Indian mobile numbers are exactly 10 digits starting 6-9. Landlines must carry
# the STD code (a leading 0 + 9-11 digits); bare 7-8 digit landlines like OSM's
# "2361514" are missing the Patna 0612 code and would not be dialable.
_IN_MOBILE = re.compile(r"[6-9]\d{9}")
_IN_LANDLINE = re.compile(r"0\d{9,11}")
_IN_TRUNCATED_REASON = (
    "not a complete Indian number (typical issue: a landline missing its STD area code)"
)


def _in_without_trunk(national: str) -> str:
    """Drop India's national-trunk '0' (used in STD dialing) for E.164 form."""
    return national.removeprefix("0")


def _chunk_result(candidate: str, dialing: str | None) -> tuple[str | None, str | None]:
    """Normalize a single phone value -> (canonical, rejection reason|None)."""
    digits = re.sub(r"\D", "", candidate)
    if not (7 <= len(digits) <= 15):
        return None, "too short to be a dialable number"

    indian = dialing == "91"
    if candidate.startswith("+"):
        body = digits
        indian = indian or digits.startswith("91")
    elif _00_PREFIX.match(digits):
        inner = digits[2:]
        if not (7 <= len(inner) <= 15):
            return None, "country-prefixed number is too short"
        body = inner
        indian = indian or inner.startswith("91")
    elif dialing == "91":
        # Bare national number in an Indian context: only dialable if it is a
        # complete mobile/landline; anything shorter is truncated and rejected.
        if not (_IN_MOBILE.fullmatch(digits) or _IN_LANDLINE.fullmatch(digits)):
            return None, _IN_TRUNCATED_REASON
        return "+91" + _in_without_trunk(digits), None
    else:
        return digits, None

    if indian:
        national = body[2:]
        if not (_IN_MOBILE.fullmatch(national) or _IN_LANDLINE.fullmatch(national)):
            return None, _IN_TRUNCATED_REASON
        return "+91" + _in_without_trunk(national), None
    return "+" + body, None


def validate_phone(phone: str | None, country_code: str | None = None) -> dict:
    """Verify a phone number and explain a rejection.

    Accepts single numbers or OSM multi-value strings (split on ``;,/|``) and
    returns the first chunk that parses as a valid phone. When ``country_code``
    is given (e.g. ``"IN"``) a bare national number is promoted to E.164 with
    the country's dialing code, and Indian plausibility rules are enforced so a
    truncated landline is flagged instead of silently "verified".
    """
    if not phone:
        return {"valid": False, "normalized": None, "reason": "no phone provided", "input": phone}
    dialing = _COUNTRY_DIALING.get((country_code or "").upper())
    last_reason = "did not parse as a valid number"
    for chunk in _PHONE_VALUE_SEP.split(phone):
        chunk = chunk.strip()
        if not chunk:
            continue
        normalized, reason = _chunk_result(chunk, dialing)
        if normalized is not None:
            return {"valid": True, "normalized": normalized, "reason": None, "input": phone}
        if reason:
            last_reason = reason
    return {"valid": False, "normalized": None, "reason": last_reason, "input": phone}


def normalize_phone(phone: str | None, country_code: str | None = None) -> str | None:
    """Normalize a phone number (country-aware) or return None if invalid."""
    return validate_phone(phone, country_code)["normalized"]


def is_valid_phone(phone: str | None, country_code: str | None = None) -> bool:
    return validate_phone(phone, country_code)["valid"]