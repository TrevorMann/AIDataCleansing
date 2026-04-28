"""Tests for cleaning.agent.

Includes needs_escalation predicate (this task) and CleaningAgent (Task 9).
"""
from cleaning.flags import FlagType
from cleaning.types import CleaningOutput


def _output_with(record: dict) -> CleaningOutput:
    return CleaningOutput(cleaned_record=record)


def test_needs_escalation_unknown_country():
    from cleaning.agent import needs_escalation
    out = _output_with({"country": "", "postal_code": "M5V 1A1", "municipality": "Toronto"})
    assert FlagType.UNKNOWN_COUNTRY in needs_escalation(out)


def test_needs_escalation_postal_unresolved():
    from cleaning.agent import needs_escalation
    out = _output_with({"country": "Canada", "postal_code": "N/A", "municipality": "Toronto"})
    assert FlagType.POSTAL_UNRESOLVED in needs_escalation(out)


def test_needs_escalation_postal_ambiguous():
    from cleaning.agent import needs_escalation
    out = _output_with({"country": "Canada", "postal_code": "M6H ?", "municipality": "Toronto"})
    assert FlagType.POSTAL_AMBIGUOUS in needs_escalation(out)


def test_needs_escalation_municipality_unresolved():
    from cleaning.agent import needs_escalation
    out = _output_with({"country": "Canada", "postal_code": "M6H 1E7", "municipality": "N/A"})
    assert FlagType.MUNICIPALITY_UNRESOLVED in needs_escalation(out)


def test_needs_escalation_low_confidence_in_notes():
    from cleaning.agent import needs_escalation
    out = _output_with({
        "country": "Canada", "postal_code": "M6H 1E7", "municipality": "Toronto",
        "validation_notes": "Confidence: LOW; could not verify",
    })
    assert FlagType.LOW_CONFIDENCE_RESEARCH in needs_escalation(out)


def test_needs_escalation_clean_record_returns_empty():
    from cleaning.agent import needs_escalation
    out = _output_with({
        "country": "Canada", "postal_code": "M6H 1E7", "municipality": "Toronto",
        "validation_notes": "Confidence: HIGH",
    })
    assert needs_escalation(out) == []


def test_needs_escalation_returns_multiple_when_applicable():
    from cleaning.agent import needs_escalation
    out = _output_with({
        "country": "Canada", "postal_code": "N/A", "municipality": "N/A",
    })
    flags = needs_escalation(out)
    assert FlagType.POSTAL_UNRESOLVED in flags
    assert FlagType.MUNICIPALITY_UNRESOLVED in flags
