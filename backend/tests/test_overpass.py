import httpx
import pytest

from app.services import overpass
from app.services.overpass import build_query, canonical_category, category_to_tags, _parse_elements


def test_category_mapping():
    assert category_to_tags("Dental Clinics") == {"amenity": "dentist"}
    assert category_to_tags("Restaurants") == {"amenity": "restaurant"}
    assert category_to_tags("Real Estate Agents") == {"office": "estate_agent"}
    assert category_to_tags("SaaS companies") is None


def test_canonical_category_mirrors_mapping_precedence():
    assert canonical_category("Dental Clinics") == "dental"
    assert canonical_category("dentist") == "dentist"
    assert canonical_category("Doctor surgeries") == "doctor"
    assert canonical_category("Car repair shops") == "car repair"
    assert canonical_category("SaaS companies") is None
    assert canonical_category(None) is None


def test_build_query():
    q = build_query({"amenity": "dentist"}, (12.8, 77.4, 13.2, 77.8))
    assert 'node["amenity"="dentist"](12.8,77.4,13.2,77.8)' in q
    assert 'way["amenity"="dentist"]' in q
    assert 'relation["amenity"="dentist"]' in q
    assert "out center tags" in q


def test_build_area_query():
    q = overpass.build_area_query({"amenity": "dentist"}, 3600556706)
    assert "area(3600556706);" in q
    assert 'node["amenity"="dentist"](area)' in q
    assert 'way["amenity"="dentist"](area)' in q
    assert 'relation["amenity"="dentist"](area)' in q
    assert "out center tags" in q


def test_search_businesses_prefers_area_query(monkeypatch):
    monkeypatch.setattr(overpass, "_throttle", lambda: None)
    captured = {}

    def fake_post(query, client=None):
        captured["query"] = query
        return {
            "elements": [
                {
                    "type": "node",
                    "id": 1,
                    "lat": -41.1188,
                    "lon": 174.8931,
                    "tags": {"name": "Whitby Dental Centre"},
                }
            ]
        }

    monkeypatch.setattr(overpass, "_post_overpass", fake_post)
    results = overpass.search_businesses("dentist", area_id=3600556706, limit=5)
    assert "area(3600556706)" in captured["query"]
    assert results[0]["name"] == "Whitby Dental Centre"
    assert results[0]["source"] == "overpass"


def test_search_businesses_still_accepts_bbox(monkeypatch):
    monkeypatch.setattr(overpass, "_throttle", lambda: None)
    captured = {}

    def fake_post(query, client=None):
        captured["query"] = query
        return {"elements": []}

    monkeypatch.setattr(overpass, "_post_overpass", fake_post)
    overpass.search_businesses("dentist", (12.8, 77.4, 13.2, 77.8), limit=5)
    assert "12.8,77.4,13.2,77.8" in captured["query"]


def test_search_businesses_requires_area_or_bbox(monkeypatch):
    monkeypatch.setattr(overpass, "_post_overpass", lambda query, client=None: {"elements": []})
    assert overpass.search_businesses("dentist", limit=5) == []


def test_parse_elements_node_and_way():
    data = {
        "elements": [
            {
                "type": "node",
                "id": 1,
                "lat": 12.9,
                "lon": 77.6,
                "tags": {"name": "SmileCare", "website": "https://smilecare.com", "addr:city": "Bengaluru"},
            },
            {
                "type": "way",
                "id": 2,
                "center": {"lat": 12.95, "lon": 77.65},
                "tags": {"name": "Bright Dental"},
            },
        ]
    }
    results = _parse_elements(data, "Dental Clinics", 10)
    assert len(results) == 2
    assert results[0]["name"] == "SmileCare"
    assert results[0]["website"] == "https://smilecare.com"
    assert results[0]["lat"] == 12.9
    assert results[1]["lat"] == 12.95
    assert results[0]["source_id"] == "node/1"


def _mock_client(handler):
    return httpx.Client(transport=httpx.MockTransport(handler))


def test_post_overpass_retries_on_504_then_succeeds(monkeypatch):
    monkeypatch.setattr(overpass, "_throttle", lambda: None)
    monkeypatch.setattr("time.sleep", lambda s: None)
    calls = {"n": 0}

    def handler(request):
        calls["n"] += 1
        if calls["n"] == 1:
            return httpx.Response(504, request=request)
        return httpx.Response(200, json={"elements": []})

    with _mock_client(handler) as client:
        data = overpass._post_overpass("q", client=client)
    assert calls["n"] == 2
    assert data == {"elements": []}


def test_post_overpass_raises_after_retries_exhausted(monkeypatch):
    monkeypatch.setattr(overpass, "_throttle", lambda: None)
    monkeypatch.setattr("time.sleep", lambda s: None)
    calls = {"n": 0}

    def handler(request):
        calls["n"] += 1
        return httpx.Response(504, request=request)

    with pytest.raises(httpx.HTTPStatusError):
        with _mock_client(handler) as client:
            overpass._post_overpass("q", client=client)
    assert calls["n"] == overpass._MAX_RETRIES + 1


def test_post_overpass_does_not_retry_4xx(monkeypatch):
    monkeypatch.setattr(overpass, "_throttle", lambda: None)
    calls = {"n": 0}

    def handler(request):
        calls["n"] += 1
        return httpx.Response(400, request=request)

    with pytest.raises(httpx.HTTPStatusError):
        with _mock_client(handler) as client:
            overpass._post_overpass("q", client=client)
    assert calls["n"] == 1


def test_post_overpass_retries_transport_error(monkeypatch):
    monkeypatch.setattr(overpass, "_throttle", lambda: None)
    monkeypatch.setattr("time.sleep", lambda s: None)
    calls = {"n": 0}

    def handler(request):
        calls["n"] += 1
        if calls["n"] == 1:
            raise httpx.ConnectError("connection dropped")
        return httpx.Response(200, json={"elements": []})

    with _mock_client(handler) as client:
        data = overpass._post_overpass("q", client=client)
    assert calls["n"] == 2
    assert data == {"elements": []}
