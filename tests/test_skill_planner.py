"""Tests for D1 (SkillPlanner) + D3 (registry dep validation + toposort)."""

import json
from unittest.mock import MagicMock, patch

import pytest

from skills.registry import SkillRegistry
from skills._common.skill_planner.skill_planner import SkillPlanner


# --- D3: Registry dependency validation + topological sort ---

def test_registry_toposort_basic():
    registry = SkillRegistry.load("real_estate")
    # address_standardizer has no depends_on, municipality_authority depends_on it
    sorted_skills = registry.topological_sort(["municipality_authority", "address_standardizer"])
    idx_std = sorted_skills.index("address_standardizer")
    idx_mun = sorted_skills.index("municipality_authority")
    assert idx_std < idx_mun, "address_standardizer must precede municipality_authority"


def test_registry_toposort_drops_unknowns():
    registry = SkillRegistry.load("real_estate")
    result = registry.topological_sort(["nonexistent_skill", "spell_checker"])
    assert "nonexistent_skill" not in result
    assert "spell_checker" in result


def test_registry_toposort_empty():
    registry = SkillRegistry.load("real_estate")
    assert registry.topological_sort([]) == []


def test_registry_skills_by_cost_low():
    registry = SkillRegistry.load("real_estate")
    low_cost = registry.skills_by_cost("low")
    for name in low_cost:
        meta = registry.get_metadata(name)
        assert meta["cost"] == "low"


def test_registry_validate_dependencies_ok():
    registry = SkillRegistry.load("real_estate")
    registry.validate_dependencies()  # should not raise


def test_registry_validate_dependencies_missing_dep():
    registry = SkillRegistry.load("real_estate")
    # Inject a skill with a bad dep
    registry.skills["bad_skill"] = MagicMock()
    registry.metadata["bad_skill"] = {"depends_on": ["nonexistent_dep"], "cost": "low"}
    with pytest.raises(ValueError, match="nonexistent_dep"):
        registry.validate_dependencies()
    del registry.skills["bad_skill"]
    del registry.metadata["bad_skill"]


def test_registry_validate_dependencies_circular():
    registry = SkillRegistry.load("real_estate")
    # Inject circular deps: A→B, B→A
    registry.skills["skill_a"] = MagicMock()
    registry.skills["skill_b"] = MagicMock()
    registry.metadata["skill_a"] = {"depends_on": ["skill_b"], "cost": "low"}
    registry.metadata["skill_b"] = {"depends_on": ["skill_a"], "cost": "low"}
    with pytest.raises(ValueError, match="Circular dependency"):
        registry.validate_dependencies()
    del registry.skills["skill_a"], registry.skills["skill_b"]
    del registry.metadata["skill_a"], registry.metadata["skill_b"]


# --- D1: SkillPlanner ---

def _make_planner(llm_response=None, conn=None):
    planner = SkillPlanner({"plan_cache_ttl_hours": 24, "pg_conn": conn})
    planner.domain = "real_estate"
    if llm_response is not None:
        mock_llm = MagicMock()
        mock_content = MagicMock()
        mock_content.text = json.dumps(llm_response)
        mock_resp = MagicMock()
        mock_resp.content = [mock_content]
        mock_llm.messages_create.return_value = mock_resp
        planner._llm = mock_llm
    return planner


def test_planner_no_registry_returns_unchanged():
    planner = _make_planner()
    record = {"_triage_route": "needs_review"}
    result = planner.run(record, tools={})
    assert "_planned_skills" not in result


def test_planner_produces_valid_plan():
    registry = SkillRegistry.load("real_estate")
    planner = _make_planner(llm_response={
        "plan": ["spell_checker", "address_standardizer", "municipality_authority"],
        "reasoning": "Address needs cleaning then municipality resolution",
    })
    record = {
        "postal_code": "M9L 1H7",
        "city": "toronot",
        "_triage_route": "needs_review",
        "_triage_data_confidence": 0.50,
    }
    result = planner.run(record, tools={"registry": registry})
    assert "_planned_skills" in result
    assert result["_plan_source"] == "llm"
    assert all(s in registry.list_skills() for s in result["_planned_skills"])


def test_planner_rejects_hallucinated_skills():
    registry = SkillRegistry.load("real_estate")
    planner = _make_planner(llm_response={
        "plan": ["spell_checker", "magic_ai_fixer", "address_standardizer"],
        "reasoning": "test",
    })
    record = {"_triage_route": "needs_review", "_triage_data_confidence": 0.50}
    result = planner.run(record, tools={"registry": registry})
    assert "magic_ai_fixer" not in result["_planned_skills"]
    assert "spell_checker" in result["_planned_skills"]


def test_planner_sorts_in_dependency_order():
    registry = SkillRegistry.load("real_estate")
    # LLM returns deps in wrong order (municipality_authority before address_standardizer)
    planner = _make_planner(llm_response={
        "plan": ["municipality_authority", "address_standardizer"],
        "reasoning": "wrong order",
    })
    record = {"_triage_route": "needs_review", "_triage_data_confidence": 0.50}
    result = planner.run(record, tools={"registry": registry})
    plan = result["_planned_skills"]
    if "address_standardizer" in plan and "municipality_authority" in plan:
        assert plan.index("address_standardizer") < plan.index("municipality_authority")


def test_planner_cache_hit_skips_llm():
    registry = SkillRegistry.load("real_estate")

    mock_cur = MagicMock()
    mock_cur.__enter__ = lambda s: s
    mock_cur.__exit__ = MagicMock(return_value=False)
    mock_cur.fetchone.return_value = (["spell_checker"], "cached reasoning")
    mock_conn = MagicMock()
    mock_conn.cursor.return_value = mock_cur

    planner = _make_planner(conn=mock_conn)
    mock_llm = MagicMock()
    planner._llm = mock_llm

    record = {"_triage_route": "needs_review", "_triage_data_confidence": 0.50, "postal_code": "M4N"}
    result = planner.run(record, tools={"registry": registry})

    mock_llm.messages_create.assert_not_called()
    assert result["_plan_source"] == "cache"
    assert result["_plan_reasoning"] == "cached reasoning"


def test_planner_llm_error_degrades_gracefully():
    registry = SkillRegistry.load("real_estate")
    planner = SkillPlanner({"plan_cache_ttl_hours": 24})
    planner.domain = "real_estate"

    mock_llm = MagicMock()
    mock_llm.messages_create.side_effect = Exception("API error")
    planner._llm = mock_llm

    record = {"_triage_route": "needs_review", "_triage_data_confidence": 0.50}
    result = planner.run(record, tools={"registry": registry})

    assert result["_planned_skills"] == []
    assert result["_plan_source"] == "error"


def test_build_prompt_includes_annotation_context():
    """When column_metadata has rows for the domain, _build_prompt includes them."""
    registry = SkillRegistry.load("real_estate")
    planner = SkillPlanner()
    planner.domain = "real_estate"

    mock_conn = MagicMock()
    cur = mock_conn.cursor.return_value.__enter__.return_value
    cur.fetchall.return_value = [
        ("raw_data", "postal_code", "CA/US postal code. Format: A1A 1A1 (CA) or 5 digits (US)."),
    ]
    planner.conn = mock_conn

    record = {"postal_code": "M5V", "city": "Toronto"}
    menu = planner._build_menu(registry)
    prompt = planner._build_prompt(record, menu)

    assert "postal_code" in prompt
    assert "CA/US postal code" in prompt


def test_build_prompt_skips_annotation_context_when_no_conn():
    """No DB conn → prompt still works, no annotation block."""
    registry = SkillRegistry.load("real_estate")
    planner = SkillPlanner()
    planner.domain = "real_estate"
    planner.conn = None

    record = {"postal_code": "M5V"}
    menu = planner._build_menu(registry)
    prompt = planner._build_prompt(record, menu)

    assert "Column Annotations" not in prompt
    assert "postal_code" in prompt  # still in record
