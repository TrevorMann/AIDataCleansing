"""Tests for guardrails.py — pure validation functions.

All tests use real code (no mocks needed — pure functions with no side effects).
Positive and negative cases for every exported function.
"""
import pytest
from guardrails import (
    GuardrailError,
    check_age,
    check_country,
    check_protected_fields,
    check_no_wildcard_update,
    check_delete_confirmation,
    check_delete_not_bulk,
    check_usa_state,
    check_nl_phone_format,
    normalize_country,
)


# ── normalize_country ─────────────────────────────────────────────────────────

class TestNormalizeCountry:
    def test_exact_code_ca(self):
        code, suggestion = normalize_country("CA")
        assert code == "CA"
        assert suggestion == "CA"

    def test_exact_code_us(self):
        code, suggestion = normalize_country("US")
        assert code == "US"
        assert suggestion == "US"

    def test_exact_code_usa(self):
        code, suggestion = normalize_country("USA")
        assert code == "US"
        assert suggestion == "US"

    def test_full_name_canada(self):
        code, suggestion = normalize_country("Canada")
        assert code == "CA"

    def test_full_name_case_insensitive(self):
        code, _ = normalize_country("canada")
        assert code == "CA"
        code2, _ = normalize_country("CANADA")
        assert code2 == "CA"

    def test_full_name_mexico(self):
        code, _ = normalize_country("Mexico")
        assert code == "MX"

    def test_full_name_netherlands(self):
        code, _ = normalize_country("Netherlands")
        assert code == "NL"

    def test_full_name_japan(self):
        code, _ = normalize_country("Japan")
        assert code == "JP"

    def test_alias_united_states(self):
        code, _ = normalize_country("United States")
        assert code == "US"

    def test_alias_america(self):
        code, _ = normalize_country("America")
        assert code == "US"

    def test_alias_uk(self):
        code, _ = normalize_country("UK")
        assert code == "GB"

    def test_alias_deutschland(self):
        code, _ = normalize_country("Deutschland")
        assert code == "DE"

    def test_fuzzy_misspelling_returns_suggestion(self):
        # "Canads" is close enough to "canada"
        code, suggestion = normalize_country("Canads")
        assert code is None
        assert suggestion == "CA"

    def test_garbage_returns_none_none(self):
        code, suggestion = normalize_country("XYZ123$$")
        assert code is None
        assert suggestion is None

    def test_empty_string_returns_none_none(self):
        code, suggestion = normalize_country("")
        assert code is None
        assert suggestion is None


# ── check_country ─────────────────────────────────────────────────────────────

class TestCheckCountry:
    def test_passes_none(self):
        check_country(None)  # no raise

    def test_passes_empty_string(self):
        check_country("")  # no raise

    def test_passes_standard_code_ca(self):
        check_country("CA")

    def test_passes_standard_code_us(self):
        check_country("US")

    def test_passes_standard_code_usa(self):
        check_country("USA")

    def test_passes_full_name_canada(self):
        check_country("Canada")

    def test_passes_full_name_germany(self):
        check_country("Germany")

    def test_passes_full_name_australia(self):
        check_country("Australia")

    def test_raises_on_garbage(self):
        with pytest.raises(GuardrailError, match="not recognizable"):
            check_country("XYZNOTACOUNTRY123")

    def test_raises_on_numeric_string(self):
        with pytest.raises(GuardrailError):
            check_country("12345")


# ── check_age ─────────────────────────────────────────────────────────────────

class TestCheckAge:
    def test_passes_none(self):
        check_age(None)

    def test_passes_valid_age(self):
        check_age(30)
        check_age(1)
        check_age(120)

    def test_raises_zero(self):
        with pytest.raises(GuardrailError):
            check_age(0)

    def test_raises_negative(self):
        with pytest.raises(GuardrailError):
            check_age(-1)

    def test_raises_over_max(self):
        with pytest.raises(GuardrailError):
            check_age(121)

    def test_raises_float(self):
        with pytest.raises(GuardrailError):
            check_age(30.5)

    def test_raises_string(self):
        with pytest.raises(GuardrailError):
            check_age("30")


# ── check_protected_fields ────────────────────────────────────────────────────

class TestCheckProtectedFields:
    def test_passes_clean_fields(self):
        check_protected_fields({"name": "Alice", "city": "Toronto"}, "raw_data")

    def test_raises_on_protected_id(self):
        with pytest.raises(GuardrailError, match="protected"):
            check_protected_fields({"id": 1, "name": "Alice"}, "raw_data")

    def test_raises_on_imported_at(self):
        with pytest.raises(GuardrailError):
            check_protected_fields({"imported_at": "2024-01-01"}, "raw_data")

    def test_cleaned_data_protected_fields(self):
        with pytest.raises(GuardrailError):
            check_protected_fields({"cleaned_at": "2024-01-01"}, "cleaned_data")

    def test_empty_fields_passes(self):
        check_protected_fields({}, "raw_data")


# ── check_no_wildcard_update ──────────────────────────────────────────────────

class TestCheckNoWildcardUpdate:
    def test_passes_with_fields(self):
        check_no_wildcard_update({"name": "Alice"})

    def test_raises_on_empty_dict(self):
        with pytest.raises(GuardrailError):
            check_no_wildcard_update({})


# ── check_delete_confirmation ─────────────────────────────────────────────────

class TestCheckDeleteConfirmation:
    def test_passes_yes(self):
        check_delete_confirmation("yes")

    def test_raises_on_no(self):
        with pytest.raises(GuardrailError):
            check_delete_confirmation("no")

    def test_raises_on_yes_uppercase(self):
        with pytest.raises(GuardrailError):
            check_delete_confirmation("YES")

    def test_raises_on_empty(self):
        with pytest.raises(GuardrailError):
            check_delete_confirmation("")


# ── check_delete_not_bulk ─────────────────────────────────────────────────────

class TestCheckDeleteNotBulk:
    def test_passes_positive_int(self):
        check_delete_not_bulk(1)
        check_delete_not_bulk(999)

    def test_raises_on_zero(self):
        with pytest.raises(GuardrailError):
            check_delete_not_bulk(0)

    def test_raises_on_negative(self):
        with pytest.raises(GuardrailError):
            check_delete_not_bulk(-5)

    def test_raises_on_string(self):
        with pytest.raises(GuardrailError):
            check_delete_not_bulk("1")

    def test_raises_on_none(self):
        with pytest.raises(GuardrailError):
            check_delete_not_bulk(None)


# ── check_usa_state ───────────────────────────────────────────────────────────

class TestCheckUsaState:
    def test_passes_full_name(self):
        check_usa_state("California")
        check_usa_state("New York")
        check_usa_state("Texas")

    def test_passes_none_or_empty(self):
        check_usa_state(None)
        check_usa_state("")

    def test_raises_on_abbreviation(self):
        with pytest.raises(GuardrailError, match="abbreviation"):
            check_usa_state("CA")

    def test_raises_on_ny_abbreviation(self):
        with pytest.raises(GuardrailError):
            check_usa_state("NY")

    def test_raises_on_invalid_state_name(self):
        with pytest.raises(GuardrailError):
            check_usa_state("Narnia")


# ── check_nl_phone_format ─────────────────────────────────────────────────────

class TestCheckNlPhoneFormat:
    def test_passes_valid_nl_phone(self):
        check_nl_phone_format("+31 20 123 4567")
        check_nl_phone_format("+31612345678")

    def test_passes_na_or_empty(self):
        check_nl_phone_format("N/A")
        check_nl_phone_format("")
        check_nl_phone_format(None)

    def test_raises_without_country_code(self):
        with pytest.raises(GuardrailError, match=r"\+31"):
            check_nl_phone_format("020 123 4567")

    def test_raises_with_wrong_country_code(self):
        with pytest.raises(GuardrailError):
            check_nl_phone_format("+44 20 1234 5678")
