"""OpenStreetMap / Overpass discovery."""

import threading
import time

import httpx

from ..config import settings

# --- category → OSM tag mapping (substring match, order = specificity) ---
CATEGORY_TAGS: dict[str, dict[str, str]] = {
    "restaurant": {"amenity": "restaurant"},
    "cafe": {"amenity": "cafe"},
    "coffee": {"amenity": "cafe"},
    "bar": {"amenity": "bar"},
    "pub": {"amenity": "pub"},
    "hotel": {"tourism": "hotel"},
    "dentist": {"amenity": "dentist"},
    "dental": {"amenity": "dentist"},
    "clinic": {"amenity": "clinic"},
    "hospital": {"amenity": "hospital"},
    "pharmacy": {"amenity": "pharmacy"},
    "doctor": {"amenity": "doctors"},
    "salon": {"shop": "beauty"},
    "beauty": {"shop": "beauty"},
    "spa": {"shop": "beauty"},
    "gym": {"leisure": "fitness_centre"},
    "fitness": {"leisure": "fitness_centre"},
    "real estate": {"office": "estate_agent"},
    "estate agent": {"office": "estate_agent"},
    "lawyer": {"office": "lawyer"},
    "legal": {"office": "lawyer"},
    "accountant": {"office": "accountant"},
    "accounting": {"office": "accountant"},
    "insurance": {"office": "insurance"},
    "bank": {"amenity": "bank"},
    "supermarket": {"shop": "supermarket"},
    "grocery": {"shop": "supermarket"},
    "bakery": {"shop": "bakery"},
    "florist": {"shop": "florist"},
    "optician": {"shop": "optician"},
    "veterinary": {"amenity": "veterinary"},
    "vet": {"amenity": "veterinary"},
    "car repair": {"shop": "car_repair"},
    "mechanic": {"shop": "car_repair"},
    "plumber": {"craft": "plumber"},
    "electrician": {"craft": "electrician"},
    "travel": {"office": "travel_agent"},
    "school": {"amenity": "school"},
}

_MIN_INTERVAL = 1.1  # seconds between Overpass requests (public API politeness)
_last_call = 0.0
_lock = threading.Lock()


def _throttle() -> None:
    global _last_call
    with _lock:
        wait = _last_call + _MIN_INTERVAL - time.time()
        if wait > 0:
            time.sleep(wait)
        _last_call = time.time()


def category_to_tags(category: str) -> dict[str, str] | None:
    cat = (category or "").lower()
    for key, tags in CATEGORY_TAGS.items():
        if key in cat:
            return tags
    return None


def build_query(tags: dict[str, str], bbox: tuple[float, float, float, float], timeout: int = 25) -> str:
    """Build an Overpass QL query for tags within a (south, west, north, east) bbox."""
    south, west, north, east = bbox
    selector = "".join(f'["{k}"="{v}"]' for k, v in tags.items())
    return (
        f"[out:json][timeout:{timeout}];"
        f"("
        f"node{selector}({south},{west},{north},{east});"
        f"way{selector}({south},{west},{north},{east});"
        f"relation{selector}({south},{west},{north},{east});"
        f");"
        f"out center tags;"
    )


def _post_overpass(query: str, client: httpx.Client | None = None) -> dict:
    _throttle()
    owns = client is None
    c = client or httpx.Client(timeout=30.0)
    try:
        resp = c.post(
            settings.overpass_endpoint,
            data={"data": query},
            headers={"User-Agent": settings.nominatim_user_agent},
        )
        resp.raise_for_status()
        return resp.json()
    finally:
        if owns:
            c.close()


def _parse_elements(data: dict, category: str, limit: int) -> list[dict]:
    results: list[dict] = []
    for el in data.get("elements", []):
        if el.get("type") not in ("node", "way", "relation"):
            continue
        tags = el.get("tags") or {}
        name = tags.get("name")
        if not name:
            continue
        if "lat" in el:
            lat, lon = el["lat"], el["lon"]
        elif "center" in el:
            lat, lon = el["center"]["lat"], el["center"]["lon"]
        else:
            continue

        address = " ".join(filter(None, [tags.get("addr:housenumber"), tags.get("addr:street")])).strip()
        results.append(
            {
                "name": name,
                "category": category,
                "address": address or None,
                "city": tags.get("addr:city"),
                "country": tags.get("addr:country"),
                "lat": lat,
                "lon": lon,
                "website": tags.get("website") or tags.get("contact:website"),
                "phone": tags.get("phone") or tags.get("contact:phone"),
                "email": tags.get("email") or tags.get("contact:email"),
                "source": "overpass",
                "source_id": f"{el['type']}/{el['id']}",
            }
        )
        if len(results) >= limit:
            break
    return results


def search_businesses(
    category: str,
    bbox: tuple[float, float, float, float],
    limit: int = 50,
    client: httpx.Client | None = None,
) -> list[dict]:
    """Discover businesses by category within a bounding box via Overpass."""
    tags = category_to_tags(category)
    if tags is None:
        return []
    query = build_query(tags, bbox)
    data = _post_overpass(query, client=client)
    return _parse_elements(data, category, limit)
