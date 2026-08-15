from app.services.overpass import build_query, category_to_tags, _parse_elements


def test_category_mapping():
    assert category_to_tags("Dental Clinics") == {"amenity": "dentist"}
    assert category_to_tags("Restaurants") == {"amenity": "restaurant"}
    assert category_to_tags("Real Estate Agents") == {"office": "estate_agent"}
    assert category_to_tags("SaaS companies") is None


def test_build_query():
    q = build_query({"amenity": "dentist"}, (12.8, 77.4, 13.2, 77.8))
    assert 'node["amenity"="dentist"](12.8,77.4,13.2,77.8)' in q
    assert 'way["amenity"="dentist"]' in q
    assert 'relation["amenity"="dentist"]' in q
    assert "out center tags" in q


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
