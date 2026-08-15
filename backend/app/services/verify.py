"""Contact verification: email format + MX lookup, phone normalization."""

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


def normalize_phone(phone: str) -> str | None:
    if not phone:
        return None
    digits = re.sub(r"\D", "", phone)
    if not (7 <= len(digits) <= 15):
        return None
    if phone.strip().startswith("+"):
        return "+" + digits
    return digits


def is_valid_phone(phone: str) -> bool:
    return normalize_phone(phone) is not None
