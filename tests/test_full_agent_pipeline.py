"""End-to-end tests for full agent team pipeline."""

import pytest
from skills.registry import SkillRegistry
from skills.agent import BaseAgent
from cleaning.orchestrator_v2 import OrchestrationTeam, run_cleaning_workflow_v2


@pytest.fixture
def registry():
    return SkillRegistry.load("real_estate")


def test_decisions_log_isolated_per_record(registry):
    """Each execute() call must only see its own decisions, not prior calls'."""
    agent = BaseAgent("X", ["spell_checker"], registry)
    rec1 = {"municipality": "scarbbrough"}   # gets corrected → decision logged
    rec2 = {"municipality": "toronot"}       # also gets corrected → different decision

    agent.execute(rec1)
    result2 = agent.execute(rec2)

    # rec2's _decisions must NOT contain rec1's correction
    decisions2 = result2.get("_decisions", [])
    assert not any("scarbbrough" in d.get("decision", "") for d in decisions2), (
        "rec1 decision leaked into rec2 — decisions_log not scoped per record"
    )
    # rec2 must have its own correction
    assert any("toronot" in d.get("decision", "") for d in decisions2), (
        "rec2 missing its own correction decision"
    )


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


from skills.real_estate.address_standardizer.address_standardizer import AddressStandardizer
from skills.real_estate.fuzzy_matcher.fuzzy_matcher import FuzzyMatcher
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


def test_fuzzy_matches_st_to_street():
    fm = FuzzyMatcher({"threshold": 0.85})
    sim = fm.compare("123 Main st", "123 main street")
    assert sim >= 0.85


def test_fuzzy_matches_saint_catherine():
    fm = FuzzyMatcher({"threshold": 0.85})
    sim = fm.compare("st Catherine", "saint catherine")
    assert sim >= 0.85


def test_fuzzy_matches_full_variant():
    fm = FuzzyMatcher({"threshold": 0.85})
    sim = fm.compare("123 Main st, st Catherine", "123 main street, saint catherine")
    assert sim >= 0.90


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
