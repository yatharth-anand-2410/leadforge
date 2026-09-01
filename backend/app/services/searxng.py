"""Free web search via the SearXNG metasearch engine JSON API.

SearXNG aggregates Google/Bing/DuckDuckGo et al. into one JSON endpoint
(``GET /search?q=...&format=json``). It is self-hostable (no API key), which is
why it's the "free web search" source: it covers categories Overpass/OSM has no
tag for (digital/SaaS firms) without paying a search vendor.

Web results carry no structured geography/contacts — only a title, URL, and
snippet — so candidates are emitted with the same schema as Overpass candidates
but with ``lat/lon``, ``address``, etc. left ``None``; the agent enriches them
with ``fetch_website`` as it already does for thin OSM candidates.

The endpoint is optional (``settings.searxng_endpoint``="" disables it) and all
failures are non-fatal: a flaky or rate-limited SearXNG instance must never kill
an agent run that could still succeed on OSM data alone.
"""

import re
import threading
import time
from urllib.parse import urlparse

import httpx

from ..config import settings
from . import website as website_svc

# Polite pacing for shared/public instances. Self-hosted instances don't need
# it, but a 1s minimum is negligible against overpass/nominatim's own throttle.
_MIN_INTERVAL = 1.0
_last_call = 0.0
_lock = threading.Lock()

_RETRYABLE_STATUS = {429, 500, 502, 503, 504}
_MAX_RETRIES = 3
_RETRY_BACKOFF = (1.0, 2.0, 4.0)

# Title separators used by marketing/SERP listings ("Smile Dental | Best
# Dentist in Bengaluru - Book Now"); the first segment is the business name.
_TITLE_SEPARATORS = re.compile(r"\s+[-|·—–]\s+")
_SKIP_UNLESS_DOMAIN = re.compile(r"\.(pdf|docx?|xlsx?|pptx?|csv)$", re.IGNORECASE)

# Link-aggregator / directory URLs that are not the business's own site.
_AGGREGATOR_HOSTS = {
    "facebook.com", "www.facebook.com", "instagram.com", "www.instagram.com",
    "twitter.com", "x.com", "linkedin.com", "www.linkedin.com",
    "youtube.com", "www.youtube.com", "yelp.com", "www.yelp.com",
    "justdial.com", "www.justdial.com", "practo.com", "www.practo.com",
    "google.com", "www.google.com", "maps.google.com",
}

_warned_endpoints: set[str] = set()


def _warn_once(endpoint: str, reason: str) -> None:
    """Print a one-line diagnostic the first time an endpoint fails hard."""
    if endpoint in _warned_endpoints:
        return
    _warned_endpoints.add(endpoint)
    print(
        f"SearXNG web search is configured ({endpoint}) but {reason}; "
        "results are OSM-only until this is fixed (self-host SearXNG or point "
        "SEARXNG_ENDPOINT at a working instance)."
    )


def _throttle() -> None:
    global _last_call
    with _lock:
        wait = _last_call + _MIN_INTERVAL - time.time()
        if wait > 0:
            time.sleep(wait)
        _last_call = time.time()


def _retry_delay(attempt: int, retry_after: str | None = None) -> float:
    if retry_after and retry_after.isdigit():
        return min(int(retry_after), 10.0)
    return _RETRY_BACKOFF[min(attempt, len(_RETRY_BACKOFF) - 1)]


def _is_usable(url: str) -> bool:
    """A web result is usable as a candidate when it points at a real web page."""
    if not url or not url.startswith(("http://", "https://")):
        return False
    try:
        host = urlparse(url).hostname
    except ValueError:
        return False
    if not host:
        return False
    return _SKIP_UNLESS_DOMAIN.search(url) is None


def _to_candidate(result: dict, category: str) -> dict:
    """Map a SearXNG result to the candidate schema emitted by ``search_businesses``."""
    url = (result.get("url") or "").strip()
    title = (result.get("title") or "").strip()
    content = result.get("content") or ""

    name = _TITLE_SEPARATORS.split(title, maxsplit=1)[0].strip() if title else ""
    if not name:
        try:
            name = urlparse(url).hostname or ""
        except ValueError:
            name = ""
    name = name.removeprefix("www.")

    email = website_svc.extract_emails(content)[:1]
    phone = website_svc.extract_phones(content)[:1]

    return {
        "name": name or None,
        "category": category,
        "address": None,
        "city": None,
        "country": None,
        "lat": None,
        "lon": None,
        "website": url or None,
        "phone": phone[0] if phone else None,
        "email": email[0] if email else None,
        "source": "searxng",
        "source_id": url,
    }


def _parse_results(data: dict, category: str, limit: int) -> list[dict]:
    results: list[dict] = []
    for result in data.get("results", []):
        url = result.get("url")
        if not _is_usable(url):
            continue
        host = urlparse(url).hostname or ""
        if host.lower() in _AGGREGATOR_HOSTS:
            continue
        results.append(_to_candidate(result, category))
        if len(results) >= limit:
            break
    return results


def lookup(
    query: str,
    limit: int = 8,
    client: httpx.Client | None = None,
) -> list[dict]:
    """Search the web and return RAW results for a business-lookup call.

    Unlike :func:`search_web` (which maps results to the candidate schema and
    drops snippets/social hosts), this returns each result verbatim as
    ``{"url", "title", "content"}`` so the caller can verify relevance from the
    snippet, extract contacts, and collect social links — aggregator/social
    hosts are kept (they ARE the socials), and the caller decides what to crawl.
    """
    if not settings.searxng_endpoint:
        return []
    if not query:
        return []

    endpoint = settings.searxng_endpoint.rstrip("/") + "/search"
    _throttle()
    owns = client is None
    c = client or httpx.Client(timeout=settings.searxng_timeout)
    try:
        data: dict | None = None
        for attempt in range(_MAX_RETRIES + 1):
            try:
                resp = c.get(
                    endpoint,
                    params={"q": query, "format": "json"},
                    headers={"User-Agent": settings.nominatim_user_agent},
                )
            except httpx.HTTPError:
                if attempt >= _MAX_RETRIES:
                    return []
                time.sleep(_retry_delay(attempt))
                continue

            if resp.status_code in _RETRYABLE_STATUS:
                if attempt >= _MAX_RETRIES:
                    _warn_once(endpoint, "it kept rate-limiting our requests")
                    return []
                time.sleep(_retry_delay(attempt, resp.headers.get("Retry-After")))
                continue

            if resp.status_code == 403:
                _warn_once(endpoint, "the instance is rejecting requests (HTTP 403)")
                return []
            try:
                resp.raise_for_status()
                data = resp.json()
            except httpx.HTTPError:
                return []
            except ValueError:
                _warn_once(endpoint, "the instance is not returning JSON (blocked or erroring)")
                return []
            break
        if data is None:
            return []

        raw: list[dict] = []
        for result in data.get("results", []):
            url = result.get("url")
            if not _is_usable(url):
                continue
            raw.append(
                {
                    "url": url,
                    "title": (result.get("title") or "").strip(),
                    "content": result.get("content") or "",
                }
            )
            if len(raw) >= limit:
                break
        if not raw and data.get("unresponsive_engines"):
            _warn_once(endpoint, "returning no results (upstream engines blocked/suspended)")
        return raw
    finally:
        if owns:
            c.close()


def search_web(
    query: str,
    limit: int = 12,
    category: str = "web",
    client: httpx.Client | None = None,
) -> list[dict]:
    """Search the web via SearXNG and return candidates.

    Returns ``[]`` when web search is disabled (``settings.searxng_endpoint`` is
    empty) or on any HTTP/parse failure — the caller should treat this as "no
    web results" and keep whatever OSM returned.
    """
    if not settings.searxng_endpoint:
        return []
    if not query:
        return []

    endpoint = settings.searxng_endpoint.rstrip("/") + "/search"
    _throttle()
    owns = client is None
    c = client or httpx.Client(timeout=settings.searxng_timeout)
    try:
        data: dict | None = None
        for attempt in range(_MAX_RETRIES + 1):
            try:
                resp = c.get(
                    endpoint,
                    params={"q": query, "format": "json"},
                    headers={"User-Agent": settings.nominatim_user_agent},
                )
            except httpx.HTTPError:
                if attempt >= _MAX_RETRIES:
                    return []
                time.sleep(_retry_delay(attempt))
                continue

            if resp.status_code in _RETRYABLE_STATUS:
                if attempt >= _MAX_RETRIES:
                    _warn_once(endpoint, "it kept rate-limiting our requests")
                    return []
                time.sleep(_retry_delay(attempt, resp.headers.get("Retry-After")))
                continue

            if resp.status_code == 403:
                # Some public instances throttle/block the JSON API entirely.
                _warn_once(endpoint, "the instance is rejecting requests (HTTP 403)")
                return []
            try:
                resp.raise_for_status()
                data = resp.json()
            except httpx.HTTPError:
                return []
            except ValueError:
                # Non-JSON body = a block/error page, not a real result set.
                _warn_once(endpoint, "the instance is not returning JSON (blocked or erroring)")
                return []
            break
        if data is None:
            return []

        results = _parse_results(data, category, limit)
        if not results and data.get("unresponsive_engines"):
            # SearXNG returned a healthy 200/JSON body but every engine failed
            # to answer (CAPTCHA / 403 / timeout). That is a real degradation —
            # the agent will see OSM-only data — surfacing it once per endpoint.
            _warn_once(endpoint, "returning no results (upstream engines blocked/suspended)")
        return results
    finally:
        if owns:
            c.close()