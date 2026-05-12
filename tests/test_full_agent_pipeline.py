"""End-to-end tests for full agent team pipeline."""

import pytest
from unittest.mock import patch, MagicMock
from skills.registry import SkillRegistry
from skills.agent import BaseAgent
from cleaning.orchestrator_v2 import OrchestrationTeam, run_cleaning_workflow_v2

_REAL_ESTATE_CORRECTIONS = {
    "scarbbrough": "scarborough",
    "scarbrough": "scarborough",
    "toronot": "toronto",
    "north yokr": "north york",
    "etobicoe": "etobicoke",
    "yorl": "york",
    "oakvile": "oakville",
    "vaughn": "vaughan",
}


@pytest.fixture
def registry():
    return SkillRegistry.load("real_estate")


def test_decisions_log_isolated_per_record():
    """Each run() must only accumulate its own audit, not prior calls'."""
    with patch("cleaning.spell_corrections_data.get_corrections_dict", return_value=_REAL_ESTATE_CORRECTIONS):
        registry_with_conn = SkillRegistry.load("real_estate", runtime={"pg_conn": MagicMock()})
    spell = registry_with_conn.get("spell_checker")

    spell.clear_audit()
    spell.run({"city": "scarbbrough"}, {})
    audit1 = spell.get_audit()

    spell.clear_audit()
    spell.run({"city": "toronot"}, {})
    audit2 = spell.get_audit()

    assert not any("scarbbrough" in e.get("decision", "") for e in audit2), (
        "rec1 audit leaked into rec2"
    )
    assert any("toronot" in e.get("decision", "") for e in audit2), (
        "rec2 missing its own correction"
    )


def test_municipality_authority_fsa_match():
    """Without a DB conn, skill skips resolution and signals confidence=0.0."""
    registry = SkillRegistry.load("real_estate")
    agent = registry.get("municipality_authority")

    record = {
        "postal_code": "M9L 1H7",
        "municipality": "Humber Summit",
    }

    result = agent.run(record)

    # No pg_conn configured → resolution skipped, municipality unchanged
    assert result.get("_municipality_confidence") == 0.0
    audit = agent.get_audit()
    assert any(
        "skipped" in d.get("decision", "").lower() or "failed" in d.get("decision", "").lower()
        for d in audit
    )


def test_municipality_authority_no_conflict():
    """Without a DB conn, skill skips resolution even when upstream looks correct."""
    registry = SkillRegistry.load("real_estate")
    agent = registry.get("municipality_authority")

    record = {
        "postal_code": "M2N 1A1",
        "municipality": "North York",
    }

    result = agent.run(record)

    # No pg_conn configured → resolution skipped, confidence=0.0
    assert result.get("_municipality_confidence") == 0.0


def test_geographic_validator_valid_postal():
    """Test GeographicValidator with valid postal code."""
    registry = SkillRegistry.load("real_estate")
    validator = registry.get("geographic_validator")

    record = {
        "country": "CA",
        "postal_code": "M9L 1H7",
        "state_province": "ON",
        "municipality": "North York",
    }

    result = validator.run(record)

    assert result.get("_geographic_validated") == True
    assert len(validator.get_audit()) > 0


def test_geographic_validator_invalid_postal():
    """Test GeographicValidator with invalid postal."""
    registry = SkillRegistry.load("real_estate")
    validator = registry.get("geographic_validator")

    record = {
        "country": "CA",
        "postal_code": "INVALID",
        "state_province": "ON",
    }

    result = validator.run(record)

    # Should detect invalid format — audit entries are on the skill, not in the record
    audit = validator.get_audit()
    assert any("Invalid postal" in d.get("decision", "") for d in audit)


def test_data_quality_triage_complete_high_confidence():
    """Test DataQualityTriage routes complete, high-confidence record as 'done'."""
    registry = SkillRegistry.load("real_estate")
    triage = registry.get("data_quality_triage")

    record = {
        "address": "123 Main St",
        "city": "Toronto",
        "postal_code": "M9L 1H7",
        "municipality": "North York",
        "country": "Canada",
        "state_province": "ON",
        "_municipality_confidence": 0.95,
        "_geographic_validated": True,
    }

    result = triage.run(record)

    assert result.get("_triage_route") == "done"
    assert result.get("_triage_confidence") >= 0.85


def test_data_quality_triage_incomplete():
    """Test DataQualityTriage routes incomplete record as 'unsalvageable'."""
    registry = SkillRegistry.load("real_estate")
    triage = registry.get("data_quality_triage")

    record = {
        "address": "123 Main St",
        # Missing: city, postal_code, municipality, country
    }

    result = triage.run(record)

    assert result.get("_triage_route") == "unsalvageable"


def test_full_pipeline_messy_record():
    """Test full agent team pipeline on messy record."""
    with patch("cleaning.spell_corrections_data.get_corrections_dict", return_value=_REAL_ESTATE_CORRECTIONS):
        registry = SkillRegistry.load("real_estate", runtime={"pg_conn": MagicMock()})
    team = OrchestrationTeam(registry)

    # Messy record with spelling errors and incomplete data
    record = {
        "id": 1,
        "address": "25 Muir Ave",
        "city": "toronot",
        "postal_code": "M9L 1H7",
        "municipality": "Humber Summit",  # Wrong for M9L
        "state_province": "ON",
        "country": "Canada",
    }

    result, audit = team.process_record(record)

    # Should have corrected spelling
    assert result["city"] == "toronto"

    # Should have triage decision
    assert "_triage_route" in result

    # Decisions are now in audit, not in record
    assert "_agent_decisions" not in result
    assert len(audit) > 0


def test_full_pipeline_clean_record():
    """Test full pipeline on clean record."""
    records = [
        {
            "id": 1,
            "address": "123 Main Street",
            "city": "Toronto",
            "postal_code": "M4N 2A7",
            "municipality": "Toronto",
            "state_province": "ON",
            "country": "Canada",
        }
    ]

    report = run_cleaning_workflow_v2(records, verbose=False)

    assert report.records_processed == 1
    assert report.cleaned_count == 1
    assert report.summary_text


def test_full_pipeline_batch():
    """Test full pipeline on batch of records."""
    records = [
        {
            "id": 1,
            "address": "25 Muir Ave",
            "city": "toronot",
            "postal_code": "M9L 1H7",
            "municipality": "Humber Summit",
            "state_province": "ON",
            "country": "Canada",
        },
        {
            "id": 2,
            "address": "123 Scarbbrough Blvd",
            "city": "Toronto",
            "postal_code": "M1A 1B1",
            "municipality": "Scarborough",
            "state_province": "ON",
            "country": "Canada",
        },
        {
            "id": 3,
            "address": "456 St",
            # Missing critical fields
            "state_province": "ON",
            "country": "Canada",
        },
    ]

    report = run_cleaning_workflow_v2(records, verbose=False)

    assert report.records_processed == 3
    assert report.cleaned_count == 3
    assert report.summary_text


from skills.real_estate.address_standardizer.address_standardizer import AddressStandardizer
from skills.real_estate.data_quality_triage.data_quality_triage import DataQualityTriageAgent


def test_triage_uses_min_confidence():
    triage = DataQualityTriageAgent()
    rec = {
        "_municipality_confidence": 0.5,
        "_geographic_validated": True,
        "_agent_decisions": [],
        "address": "123 Main St",
        "city": "Toronto",
        "postal_code": "M1A1B1",
        "municipality": "Scarborough",
        "country": "CA",
    }
    out = triage.run(rec)
    # With avg: (0.5 + 0.85 + 0.9) / 3 = 0.75 — would pass this assertion wrongly
    # With min: min(0.5, 0.85, 0.9) = 0.5 — correctly low
    assert out["_triage_data_confidence"] <= 0.6, (
        f"Expected min-based confidence ~0.5, got {out['_triage_data_confidence']:.3f} — "
        "looks like average is still being used"
    )


def test_quadrant_ne_expanded():
    s = AddressStandardizer()
    result = s._standardize("123 Main St NE")
    assert "Northeast" in result


def test_quadrant_nw_expanded():
    s = AddressStandardizer()
    result = s._standardize("456 Pine Rd NW")
    assert "Northwest" in result


def test_single_letter_directional_not_expanded():
    s = AddressStandardizer()
    out = s._standardize("123 Doe N Main St")
    assert "North" not in out, f"Single-letter N must not expand. Got: {out}"


def test_record_linker_address_composite():
    from skills._common.record_linker.record_linker import RecordLinker
    linker = RecordLinker({
        "blocking_fields": [],
        "match_rules": [{
            "name": "address_composite",
            "fields": ["address", "city"],
            "match_type": "fuzzy",
            "threshold": 0.75,
            "weight": 0.9,
        }]
    })
    record = {"id": "A", "address": "123 Main St", "city": "toronto"}
    candidates = [{"id": "B", "address": "123 Main Street", "city": "toronto"}]
    result = linker.run(record, tools={"candidates": candidates})
    assert len(result.get("_linked_records", [])) == 1


def test_municipality_authority_no_hardcoded_fsa_dict():
    """Lock: hardcoded FSA dict must not exist in source."""
    from pathlib import Path
    src = Path("skills/real_estate/municipality_authority/municipality_authority.py").read_text()
    assert "fsa_to_municipality" not in src, "Hardcoded FSA dict reintroduced"
    assert "M1A" not in src, "Hardcoded FSA entry found in source"


def test_municipality_authority_no_conn_signals_gracefully():
    """No conn configured → confidence=0.0 and a 'skipped' decision logged."""
    from skills.real_estate.municipality_authority.municipality_authority import MunicipalityAuthorityAgent
    skill = MunicipalityAuthorityAgent(config={})
    out = skill.run({"postal_code": "M1A 1B1", "municipality": "Scarborough"})
    assert out.get("_municipality_confidence", 1.0) == 0.0
    audit = skill.get_audit()
    assert any(
        "skipped" in d.get("decision", "").lower() or "failed" in d.get("decision", "").lower()
        for d in audit
    )


def test_phase1_audit_not_in_record():
    """After process_record, _decisions and _agent_decisions must not be in record."""
    registry = SkillRegistry.load("real_estate")
    team = OrchestrationTeam(registry)
    record = {"id": "r1", "city": "toronot", "address": "123 Main St"}
    result, audit = team.process_record(record)
    assert "_decisions" not in result
    assert "_agent_decisions" not in result
    assert isinstance(audit, list)
    assert len(audit) > 0, "Expected at least one audit entry from Phase-1 skills"


def test_process_record_returns_tuple():
    """process_record must return (record_dict, audit_list)."""
    registry = SkillRegistry.load("real_estate")
    team = OrchestrationTeam(registry)
    result = team.process_record({"id": "r1"})
    assert isinstance(result, tuple)
    assert len(result) == 2


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
