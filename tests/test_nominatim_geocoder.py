"""Tests for NominatimGeocoderSkill (B5)."""

from unittest.mock import MagicMock, patch
import pytest

from skills.real_estate.nominatim_geocoder.nominatim_geocoder import NominatimGeocoderSkill


_NOMINATIM_RESULT = [
    {
        "lat": "43.7234",
        "lon": "-79.5432",
        "display_name": "25 Muir Avenue, North York, Toronto, Ontario, Canada",
    }
]


def _make_skill(conn=None):
    return NominatimGeocoderSkill({"rate_limit": 1, "pg_conn": conn})


# --- No address → skip ---

def test_no_address_returns_unchanged():
    sc = _make_skill()
    record = {"city": "Toronto"}
    result = sc.run(record)
    assert "_geocode_confidence" not in result


# --- Successful geocode (mocked HTTP) ---

def test_geocode_success_no_cache():
    sc = _make_skill()
    with patch("cleaning.nominatim_client.NominatimClient.search_structured", return_value=_NOMINATIM_RESULT):
        result = sc.run({
            "address": "25 Muir Avenue",
            "city": "North York",
            "postal_code": "M9L 1H7",
            "country": "Canada",
        })

    assert result["_geocode_validated"] is True
    assert result["_geocode_lat"] == 43.7234
    assert result["_geocode_lon"] == -79.5432
    assert result["_geocode_confidence"] == 0.90  # city "north york" in display
    assert len(sc.get_audit()) > 0


def test_geocode_city_not_in_display_lower_confidence():
    sc = _make_skill()
    result_no_city = [{"lat": "43.0", "lon": "-79.0", "display_name": "Some random place"}]
    with patch("cleaning.nominatim_client.NominatimClient.search_structured", return_value=result_no_city):
        result = sc.run({
            "address": "123 Main St",
            "city": "Toronto",
            "postal_code": "M4N 2A7",
            "country": "Canada",
        })
    assert result["_geocode_confidence"] == 0.70


# --- No results ---

def test_geocode_no_results():
    sc = _make_skill()
    with patch("cleaning.nominatim_client.NominatimClient.search_structured", return_value=[]):
        result = sc.run({
            "address": "999 Fake Street",
            "city": "Narnia",
            "postal_code": "X0X 0X0",
            "country": "Canada",
        })
    assert result["_geocode_validated"] is False
    assert result["_geocode_confidence"] == 0.0
    assert len(sc.get_audit()) > 0


# --- HTTP error ---

def test_geocode_http_error_degrades_gracefully():
    sc = _make_skill()
    with patch("cleaning.nominatim_client.NominatimClient.search_structured", side_effect=Exception("connection refused")):
        result = sc.run({
            "address": "25 Muir Ave",
            "city": "Toronto",
            "postal_code": "M9L 1H7",
            "country": "Canada",
        })
    assert result["_geocode_confidence"] == 0.0
    assert result["_geocode_validated"] is False


# --- PG cache hit skips HTTP ---

def test_geocode_cache_hit_skips_http():
    mock_cur = MagicMock()
    mock_cur.__enter__ = lambda s: s
    mock_cur.__exit__ = MagicMock(return_value=False)
    import json
    mock_cur.fetchone.return_value = (json.dumps(_NOMINATIM_RESULT),)
    mock_conn = MagicMock()
    mock_conn.cursor.return_value = mock_cur

    sc = _make_skill(conn=mock_conn)

    with patch("cleaning.nominatim_client.NominatimClient.search_structured") as mock_search:
        result = sc.run({
            "address": "25 Muir Avenue",
            "city": "North York",
            "postal_code": "M9L 1H7",
            "country": "Canada",
        })
        mock_search.assert_not_called()  # cache hit → no HTTP

    assert result["_geocode_validated"] is True


# --- Skill registry loads nominatim_geocoder ---

def test_skill_registry_loads_nominatim_geocoder():
    from skills.registry import SkillRegistry
    registry = SkillRegistry.load("real_estate")
    skill = registry.get("nominatim_geocoder")
    assert skill is not None
    assert skill.name == "NominatimGeocoderSkill"
