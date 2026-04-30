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


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
