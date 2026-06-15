from cleaning.flags import FlagType, flags_from_gaps


def test_missing_country_maps_to_unknown_country():
    assert flags_from_gaps(["missing:country"]) == [FlagType.UNKNOWN_COUNTRY]


def test_missing_postal_code_maps_to_postal_unresolved():
    # qualifier is ignored for flag derivation
    assert flags_from_gaps(["missing:postal_code|ca"]) == [FlagType.POSTAL_UNRESOLVED]


def test_missing_municipality_maps_to_municipality_unresolved():
    assert flags_from_gaps(["missing:municipality"]) == [FlagType.MUNICIPALITY_UNRESOLVED]


def test_unmapped_gap_yields_no_flag():
    assert flags_from_gaps(["missing:notes"]) == []


def test_dedupes_and_preserves_order():
    gaps = ["missing:postal_code|ca", "missing:postal_code|us", "missing:country"]
    assert flags_from_gaps(gaps) == [FlagType.POSTAL_UNRESOLVED, FlagType.UNKNOWN_COUNTRY]
