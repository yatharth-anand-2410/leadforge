"""Nominatim (OSM) geocoding and free-text place search."""

import threading
import time

import httpx

from ..config import settings

_MIN_INTERVAL = 1.1  # Nominatim public policy ~1 req/sec
_last_call = 0.0
_lock = threading.Lock()


def _throttle() -> None:
    global _last_call
    with _lock:
        wait = _last_call + _MIN_INTERVAL - time.time()
        if wait > 0:
            time.sleep(wait)
        _last_call = time.time()


def _headers() -> dict:
    return {"User-Agent": settings.nominatim_user_agent}


def geocode(query: str, client: httpx.Client | None = None) -> dict | None:
    """Resolve a free-text location to lat/lon + a (south, west, north, east) bbox."""
    _throttle()
    owns = client is None
    c = client or httpx.Client(timeout=15.0)
    try:
        resp = c.get(
            settings.nominatim_endpoint + "/search",
            params={"q": query, "format": "json", "limit": 1, "addressdetails": 1},
            headers=_headers(),
        )
        resp.raise_for_status()
        items = resp.json()
    finally:
        if owns:
            c.close()

    if not items:
        return None

    item = items[0]
    bb = item.get("boundingbox")
    if not bb or len(bb) != 4:
        return None

    south, north, west, east = (float(x) for x in bb)
    return {
        "lat": float(item["lat"]),
        "lon": float(item["lon"]),
        "display_name": item.get("display_name"),
        "boundingbox": (south, west, north, east),  # Overpass order
    }


def _to_candidate(item: dict) -> dict:
    addr = item.get("address", {})
    return {
        "name": item.get("name") or (item.get("display_name", "").split(",")[0] if item.get("display_name") else None),
        "category": item.get("type"),
        "address": item.get("display_name"),
        "city": addr.get("city") or addr.get("town") or addr.get("village"),
        "country": addr.get("country"),
        "lat": float(item["lat"]),
        "lon": float(item["lon"]),
        "website": None,
        "phone": None,
        "email": None,
        "source": "nominatim",
        "source_id": f"nominatim/{item.get('place_id')}",
    }


def search_places(query: str, limit: int = 10, client: httpx.Client | None = None) -> list[dict]:
    """Free-text place search — fallback when a category has no OSM tag mapping."""
    _throttle()
    owns = client is None
    c = client or httpx.Client(timeout=15.0)
    try:
        resp = c.get(
            settings.nominatim_endpoint + "/search",
            params={"q": query, "format": "json", "limit": limit, "addressdetails": 1},
            headers=_headers(),
        )
        resp.raise_for_status()
        return [_to_candidate(item) for item in resp.json()]
    finally:
        if owns:
            c.close()
