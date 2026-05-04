"""Tests for cleaning.flags."""
from cleaning.flags import (
    FlagType, FlagSeverity, Flag, persist_flags, query_unresolved_flags,
)


def test_flagtype_values():
    assert FlagType.UNKNOWN_COUNTRY.value == "unknown_country"
    assert FlagType.CROSS_REGION_MISMATCH.value == "cross_region_mismatch"
    assert FlagType.POSTAL_UNRESOLVED.value == "postal_unresolved"
    assert FlagType.POSTAL_AMBIGUOUS.value == "postal_ambiguous"
    assert FlagType.MUNICIPALITY_UNRESOLVED.value == "municipality_unresolved"
    assert FlagType.LOW_CONFIDENCE_RESEARCH.value == "low_confidence_research"
    assert FlagType.GUARDRAIL_BLOCKED.value == "guardrail_blocked"
    assert FlagType.RESOLVED_AFTER_ESCALATION.value == "resolved_after_escalation"


def test_flagseverity_values():
    assert FlagSeverity.INFO.value == "INFO"
    assert FlagSeverity.WARN.value == "WARN"
    assert FlagSeverity.NEEDS_REVIEW.value == "NEEDS_REVIEW"
    assert FlagSeverity.BLOCKED.value == "BLOCKED"


def test_flag_dataclass_construction():
    f = Flag(
        flag_type=FlagType.POSTAL_UNRESOLVED,
        severity=FlagSeverity.NEEDS_REVIEW,
        reason="could not verify M6H against street address",
        raised_by="agent:CA",
    )
    assert f.flag_type is FlagType.POSTAL_UNRESOLVED
    assert f.cleaned_data_id is None  # optional


def test_persist_flags_writes_each_flag(tmp_db):
    from db_helpers import insert_raw_data
    raw_id = insert_raw_data(tmp_db, name="x", country="CA")
    flags = [
        Flag(FlagType.POSTAL_UNRESOLVED, FlagSeverity.NEEDS_REVIEW, "r1", "agent:CA"),
        Flag(FlagType.MUNICIPALITY_UNRESOLVED, FlagSeverity.NEEDS_REVIEW, "r2", "agent:CA"),
    ]
    ids = persist_flags(tmp_db, raw_data_id=raw_id, cleaned_data_id=None, flags=flags)
    assert len(ids) == 2
    assert all(isinstance(i, int) for i in ids)


def test_query_unresolved_flags(tmp_db):
    from db_helpers import insert_raw_data
    raw_id = insert_raw_data(tmp_db, name="x", country="CA")
    persist_flags(tmp_db, raw_data_id=raw_id, cleaned_data_id=None, flags=[
        Flag(FlagType.POSTAL_UNRESOLVED, FlagSeverity.NEEDS_REVIEW, "r", "agent:CA"),
    ])
    results = query_unresolved_flags(tmp_db)
    assert len(results) == 1
    assert results[0]["flag_type"] == "postal_unresolved"
