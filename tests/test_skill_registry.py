"""Tests for skill registry and agent team."""

import pytest
from skills.registry import SkillRegistry
from skills.agent import BaseAgent


def test_skill_registry_load():
    """Test loading skills from YAML."""
    registry = SkillRegistry.load("real_estate")
    assert registry is not None
    assert len(registry.list_skills()) > 0


def test_skill_registry_get():
    """Test O(1) skill lookup."""
    registry = SkillRegistry.load("real_estate")
    spell_checker = registry.get("spell_checker")
    assert spell_checker is not None
    assert spell_checker.name == "SpellChecker"


def test_skill_registry_metadata():
    """Test metadata retrieval."""
    registry = SkillRegistry.load("real_estate")
    meta = registry.get_metadata("spell_checker")
    assert meta is not None
    assert "tools" in meta
    assert "cost" in meta


def test_address_cleaning_agent():
    """Test AddressCleaningAgent with spell_checker and address_standardizer."""
    registry = SkillRegistry.load("real_estate")

    # Create agent with address cleaning skills
    agent = BaseAgent(
        name="AddressCleaningAgent",
        skills=["spell_checker", "address_standardizer"],
        registry=registry,
        tools={},
    )

    # Test with messy record
    record = {
        "address": "123 Muir Ave",
        "city": "toronot",
        "municipality": "scarbbrough",
    }

    result = agent.execute(record)

    # symspellpy corrects common words even without DB; address_standardizer always runs
    assert "Avenue" in result["address"]  # Ave→Avenue expansion (deterministic)


def test_spell_checker_no_conn_no_corrections():
    """SpellChecker without pg_conn uses only symspellpy — address field untouched (not in text_fields)."""
    registry = SkillRegistry.load("real_estate")
    spell_checker = registry.get("spell_checker")

    record = {
        "address": "123 Main St",
        "city": "toronot",
        "municipality": "scarbbrough",
    }

    result = spell_checker.run(record)

    # address is NOT in text_fields → always untouched
    assert result["address"] == "123 Main St"
    # city and municipality are in text_fields — symspellpy may correct them
    # (domain overrides are empty without pg_conn, but symspellpy general dictionary still runs)
    assert "city" in result
    assert "municipality" in result


def test_spell_checker_with_injected_corrections():
    from unittest.mock import MagicMock, patch
    from skills._common.spell_checker.spell_checker import SpellChecker

    with patch("cleaning.spell_corrections_data.get_corrections_dict", return_value={"scarbbrough": "scarborough"}):
        spell_checker = SpellChecker({
            "pg_conn": MagicMock(),
            "threshold": 0.85,
            "text_fields": ["municipality"],
        })
    result = spell_checker.run({"municipality": "scarbbrough", "address": "123 Main St"})
    assert result["municipality"] == "scarborough"
    assert result["address"] == "123 Main St"   # not in text_fields — untouched
    assert "_decisions" not in result
    assert len(spell_checker.get_audit()) == 1


def test_address_standardizer_skill():
    """Test AddressStandardizer skill."""
    registry = SkillRegistry.load("real_estate")
    standardizer = registry.get("address_standardizer")

    record = {
        "address": "25 Muir Ave, Apt 123",
    }

    result = standardizer.run(record)

    # Should have expanded abbreviation
    assert "Avenue" in result["address"]


def test_record_linker_exact_match():
    """RecordLinker exact-match rule links identical field values."""
    from skills._common.record_linker.record_linker import RecordLinker
    linker = RecordLinker({
        "blocking_fields": [],
        "match_rules": [{
            "name": "address_exact",
            "fields": ["address"],
            "match_type": "exact",
            "weight": 1.0,
        }]
    })
    record = {"id": "A", "address": "25 Muir Avenue"}
    candidates = [{"id": "B", "address": "25 Muir Avenue"}]
    result = linker.run(record, tools={"candidates": candidates})
    linked = result.get("_linked_records", [])
    assert len(linked) == 1
    assert linked[0]["confidence"] == 1.0


def test_record_linker_fuzzy_variant():
    """RecordLinker fuzzy rule links near-duplicate addresses."""
    from skills._common.record_linker.record_linker import RecordLinker
    linker = RecordLinker({
        "blocking_fields": [],
        "match_rules": [{
            "name": "address_fuzzy",
            "fields": ["address"],
            "match_type": "fuzzy",
            "threshold": 0.60,
            "weight": 1.0,
        }]
    })
    record = {"id": "A", "address": "25 Muir Ave"}
    candidates = [{"id": "B", "address": "25 Muir Avenue"}]
    result = linker.run(record, tools={"candidates": candidates})
    assert len(result.get("_linked_records", [])) >= 1


def test_record_linker_no_match():
    """RecordLinker returns no links when addresses are too different."""
    from skills._common.record_linker.record_linker import RecordLinker
    linker = RecordLinker({
        "blocking_fields": [],
        "match_rules": [{
            "name": "address_fuzzy",
            "fields": ["address"],
            "match_type": "fuzzy",
            "threshold": 0.85,
            "weight": 1.0,
        }]
    })
    record = {"id": "A", "address": "123 Main St"}
    candidates = [{"id": "B", "address": "456 Queen Ave"}]
    result = linker.run(record, tools={"candidates": candidates})
    assert len(result.get("_linked_records", [])) == 0


def test_orchestration_team():
    """Test OrchestrationTeam agent pipeline."""
    from cleaning.orchestrator_v2 import OrchestrationTeam

    registry = SkillRegistry.load("real_estate")
    team = OrchestrationTeam(registry)

    record = {
        "id": 1,
        "address": "123 Muir Ave",
        "city": "toronot",
        "municipality": "scarbbrough",
    }

    result, audit = team.process_record(record)

    # Result must have triage metadata
    assert any(key.startswith("_") for key in result.keys())
    # Audit entries are returned separately, not embedded in record
    assert "_agent_decisions" not in result
    assert isinstance(audit, list)


def test_audit_entry_model():
    from skills.models import AuditEntry
    entry = AuditEntry(
        skill="SpellChecker",
        field="city",
        original="toronot",
        corrected="toronto",
        reason="symspellpy edit_dist=1",
        confidence=0.9,
    )
    assert entry.skill == "SpellChecker"
    assert entry.confidence == 0.9


def test_baseskill_audit_accumulation():
    from skills.models import AuditEntry
    registry = SkillRegistry.load("real_estate")
    spell = registry.get("spell_checker")

    spell.clear_audit()
    spell.run({"city": "toronot"}, {})  # no corrections loaded but audit API must exist
    entries = spell.get_audit()
    assert isinstance(entries, list)


def test_address_standardizer_config_fields():
    """Standardizer only touches address_fields — not other fields."""
    from skills._common.address_standardizer.address_standardizer import AddressStandardizer
    std = AddressStandardizer({"address_fields": ["address", "mailing_address"]})
    result = std.run({
        "address": "123 Main St",
        "mailing_address": "456 Oak Ave",
        "notes": "Near Muir Ave",   # not in address_fields
    })
    assert "Street" in result["address"]
    assert "Avenue" in result["mailing_address"]
    assert result["notes"] == "Near Muir Ave"   # untouched


def test_address_standardizer_audit_not_in_record():
    """_decisions must NOT be in returned record."""
    from skills._common.address_standardizer.address_standardizer import AddressStandardizer
    std = AddressStandardizer({"address_fields": ["address"]})
    result = std.run({"address": "123 Main St"})
    assert "_decisions" not in result


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
