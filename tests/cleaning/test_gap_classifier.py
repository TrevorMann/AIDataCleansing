from cleaning.gap_classifier import classify_gaps


def test_emits_missing_for_null_field():
    config = {"postal_code": {"missing": True}}
    record = {"postal_code": None}
    assert classify_gaps(record, config) == ["missing:postal_code"]


def test_emits_missing_for_empty_string():
    config = {"postal_code": {"missing": True}}
    record = {"postal_code": "   "}
    assert classify_gaps(record, config) == ["missing:postal_code"]


def test_no_gap_when_field_present():
    config = {"postal_code": {"missing": True}}
    record = {"postal_code": "M5H 2N2"}
    assert classify_gaps(record, config) == []


def test_qualifier_appended_from_discriminator_column():
    config = {"postal_code": {"missing": True, "discriminator": "country"}}
    record = {"postal_code": None, "country": "CA"}
    assert classify_gaps(record, config) == ["missing:postal_code|ca"]


def test_no_qualifier_when_discriminator_value_absent():
    config = {"postal_code": {"missing": True, "discriminator": "country"}}
    record = {"postal_code": None, "country": None}
    assert classify_gaps(record, config) == ["missing:postal_code"]


def test_missing_disabled_emits_nothing():
    config = {"notes": {"missing": False}}
    record = {"notes": None}
    assert classify_gaps(record, config) == []


def test_unknown_verbs_not_built_in_v1():
    # malformed is designed but not built: a malformed-only config emits nothing
    config = {"phone": {"malformed": {"by": "country", "rules": {}}}}
    record = {"phone": "garbage"}
    assert classify_gaps(record, config) == []


def test_multiple_fields_preserves_order():
    config = {"postal_code": {"missing": True}, "country": {"missing": True}}
    record = {"postal_code": None, "country": ""}
    assert classify_gaps(record, config) == ["missing:postal_code", "missing:country"]
    # NOTE: classify_gaps cannot emit duplicates from a dict config (keys are
    # unique). Real dedup coverage lives in Task 6, where classifier output is
    # merged with legacy hints + _gap_hints that CAN overlap.
