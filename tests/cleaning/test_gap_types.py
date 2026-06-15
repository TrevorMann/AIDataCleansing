import pytest
from cleaning.gap_types import (
    VERBS, build_gap, parse_gap, is_valid_gap, ParsedGap,
)


def test_verbs_are_the_closed_set():
    assert VERBS == ("missing", "malformed", "ambiguous", "mismatch", "out_of_range")


def test_build_base_gap():
    assert build_gap("missing", "postal_code") == "missing:postal_code"


def test_build_gap_with_qualifier_is_lowercased_and_stripped():
    assert build_gap("missing", "postal_code", qualifier=" CA ") == "missing:postal_code|ca"


def test_build_mismatch_joins_sorted_fields_with_plus():
    assert build_gap("mismatch", ["province", "city"]) == "mismatch:city+province"


def test_build_gap_rejects_unknown_verb():
    with pytest.raises(ValueError):
        build_gap("frobnicated", "postal_code")


def test_parse_base_gap():
    assert parse_gap("missing:postal_code") == ParsedGap("missing", ("postal_code",), None)


def test_parse_qualified_gap():
    assert parse_gap("missing:postal_code|ca") == ParsedGap("missing", ("postal_code",), "ca")


def test_parse_mismatch_gap():
    assert parse_gap("mismatch:city+province") == ParsedGap("mismatch", ("city", "province"), None)


def test_is_valid_gap():
    assert is_valid_gap("missing:postal_code|ca") is True
    assert is_valid_gap("bogus:postal_code") is False
    assert is_valid_gap("missing") is False
