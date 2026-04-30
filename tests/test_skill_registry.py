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

    # Should have corrected spelling and standardized address
    assert result["city"] == "toronto"
    assert result["municipality"] == "scarborough"
    assert "Avenue" in result["address"]  # St→Street, Ave→Avenue


def test_spell_checker_skill():
    """Test SpellChecker skill directly."""
    registry = SkillRegistry.load("real_estate")
    spell_checker = registry.get("spell_checker")

    record = {
        "address": "123 Main St",
        "city": "toronot",
        "municipality": "scarbbrough",
    }

    result = spell_checker.run(record)

    # Check corrections were made
    assert result["city"] == "toronto"
    assert result["municipality"] == "scarborough"
    assert "_decisions" in result


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


def test_fuzzy_matcher_exact_match():
    """Test FuzzyMatcher with exact match."""
    registry = SkillRegistry.load("real_estate")
    fuzzy = registry.get("fuzzy_matcher")

    similarity, decision = fuzzy.match("25 Muir Avenue", "25 Muir Avenue")
    assert similarity == 1.0
    assert decision["confidence"] == 1.0


def test_fuzzy_matcher_variant():
    """Test FuzzyMatcher with address variants."""
    registry = SkillRegistry.load("real_estate")
    fuzzy = registry.get("fuzzy_matcher")

    # Should match "Ave" vs "Avenue" (tokens match, just abbreviation difference)
    similarity, decision = fuzzy.match("25 Muir Ave", "25 Muir Avenue")
    assert similarity >= 0.60  # Should be reasonable similarity


def test_fuzzy_matcher_different():
    """Test FuzzyMatcher with different addresses."""
    registry = SkillRegistry.load("real_estate")
    fuzzy = registry.get("fuzzy_matcher")

    similarity, decision = fuzzy.match("123 Main St", "456 Queen Ave")
    assert similarity < 0.5  # Should be low similarity


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

    result = team.process_record(record)

    # Should have agent decisions logged
    assert "_agent_decisions" in result or any(key.startswith("_") for key in result.keys())
    # Spelling should be corrected
    assert result.get("city") == "toronto"
    assert result.get("municipality") == "scarborough"


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
