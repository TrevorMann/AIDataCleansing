"""End-to-end tests for full agent team pipeline."""

import pytest
from skills.registry import SkillRegistry
from cleaning.orchestrator_v2 import OrchestrationTeam, run_cleaning_workflow_v2


def test_municipality_authority_fsa_match():
    """Test MunicipalityAuthority FSA resolution."""
    registry = SkillRegistry.load("real_estate")
    agent = registry.get("municipality_authority")

    record = {
        "postal_code": "M9L 1H7",
        "municipality": "Humber Summit",  # Wrong
    }

    result = agent.run(record)

    # Should resolve to North York (from FSA M9L)
    assert result.get("municipality") == "North York"
    assert result.get("_municipality_confidence") >= 0.85


def test_municipality_authority_no_conflict():
    """Test MunicipalityAuthority when upstream matches FSA."""
    registry = SkillRegistry.load("real_estate")
    agent = registry.get("municipality_authority")

    record = {
        "postal_code": "M2N 1A1",
        "municipality": "North York",
    }

    result = agent.run(record)

    # Should confirm North York
    assert result.get("municipality") == "North York"
    assert result.get("_municipality_confidence") == 1.0


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
    assert "_decisions" in result


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

    # Should detect invalid format
    decisions = result.get("_decisions", [])
    assert any("Invalid postal" in d.get("decision", "") for d in decisions)


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
    assert result.get("_triage_confidence") > 0.85


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
    registry = SkillRegistry.load("real_estate")
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

    result = team.process_record(record)

    # Should have corrected spelling
    assert result["city"] == "toronto"

    # Should have resolved municipality via FSA
    assert result["municipality"] == "North York"

    # Should have triage decision
    assert "_triage_route" in result
    assert result["_triage_route"] in ["done", "needs_review"]

    # Should have decisions logged
    assert "_agent_decisions" in result


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
    assert "agent team" in report.summary_text.lower()


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
