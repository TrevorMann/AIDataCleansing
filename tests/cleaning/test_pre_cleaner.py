"""Unit tests for cleaning.pre_cleaner. Pure-Python, no fixtures needed."""
from cleaning.pre_cleaner import (
    get_country_code, clean_name, clean_city, clean_address,
    expand_country, expand_state_province, normalize_postal,
    format_phone, needs_research, pre_clean_record,
)


def test_get_country_code_from_full_name():
    assert get_country_code("Canada") == "CA"
    assert get_country_code("United States") == "USA"
    assert get_country_code("Holland") == "NL"


def test_get_country_code_from_abbrev():
    assert get_country_code("CA") == "CA"
    assert get_country_code("USA") == "USA"


def test_get_country_code_unknown():
    assert get_country_code("Atlantis") is None
    assert get_country_code("") is None
    assert get_country_code(None) is None


def test_clean_name_titlecase():
    assert clean_name("john doe") == "John Doe"
    assert clean_name("  alice   smith  ") == "Alice   Smith"


def test_normalize_postal_canada():
    assert normalize_postal("M6H1E7", "CA") == "M6H 1E7"
    assert normalize_postal("m6h 1e7", "CA") == "M6H 1E7"


def test_format_phone_north_america():
    assert format_phone("4165550123", "CA") == "(416) 555-0123"
    assert format_phone("1-416-555-0123", "USA") == "(416) 555-0123"


def test_needs_research_missing_municipality():
    assert needs_research({"municipality": "", "postal_code": "M6H 1E7"}) is True
    assert needs_research({"municipality": "N/A", "postal_code": "M6H 1E7"}) is True


def test_needs_research_complete_record():
    assert needs_research({"municipality": "Toronto", "postal_code": "M6H 1E7"}) is False


def test_pre_clean_record_full_run():
    raw = {
        "name": "john doe", "city": "toronto", "address": "25 Muir St.",
        "country": "CA", "state_province": "ON", "phone": "4165550123",
        "postal_code": "M6H1E7", "municipality": "",
    }
    cleaned = pre_clean_record(raw)
    assert cleaned["name"] == "John Doe"
    assert cleaned["city"] == "Toronto"
    assert cleaned["address"] == "25 Muir Street."
    assert cleaned["country"] == "Canada"
    assert cleaned["state_province"] == "Ontario"
    assert cleaned["phone"] == "(416) 555-0123"
    assert cleaned["postal_code"] == "M6H 1E7"
    assert cleaned["_pre_clean_changes"]  # non-empty list
