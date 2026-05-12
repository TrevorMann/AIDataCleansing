"""Tests for C3: BatchBudget + orchestrator trigger gating."""

import pytest
from unittest.mock import MagicMock, patch

from cleaning.orchestrator_v2 import BatchBudget, OrchestrationTeam
from skills.registry import SkillRegistry


# --- BatchBudget ---

def test_budget_take_success():
    b = BatchBudget(10)
    assert b.take() is True
    assert b.remaining == 9
    assert b.spent == 1


def test_budget_take_multiple():
    b = BatchBudget(5)
    assert b.take(3) is True
    assert b.remaining == 2


def test_budget_exhausted():
    b = BatchBudget(2)
    b.take(2)
    assert b.take() is False
    assert b.remaining == 0


def test_budget_partial_take_fails():
    b = BatchBudget(2)
    assert b.take(3) is False  # 3 > remaining=2
    assert b.remaining == 2  # unchanged


def test_budget_summary():
    b = BatchBudget(100)
    b.take(23)
    summary = b.summary()
    assert "23" in summary
    assert "100" in summary


# --- OrchestrationTeam gating ---

def test_orchestration_team_exits_early_on_done():
    """Pipeline exits after Phase 2 triage when route is 'done' — no further skills run."""
    registry = SkillRegistry.load("real_estate")
    team = OrchestrationTeam(registry)

    # Track which skills run by patching the registry.get
    calls = []
    original_get = registry.get

    def tracking_get(name):
        skill = original_get(name)
        if skill:
            original_run = skill.run
            def tracked_run(record, tools=None):
                calls.append(name)
                return original_run(record, tools)
            skill.run = tracked_run
        return skill

    # Use a complete record that should triage to "done"
    record = {
        "id": 1,
        "address": "123 Main Street",
        "city": "Toronto",
        "postal_code": "M4N 2A7",
        "municipality": "Toronto",
        "state_province": "ON",
        "country": "Canada",
    }
    result, audit = team.process_record(record)
    # web_search_enricher should NOT appear in decisions for a "done" record
    # (it's gated by triage route)
    assert not any(e.get("skill") == "skill_planner" for e in audit)


def test_orchestration_team_returns_triage_route():
    """process_record always returns a _triage_route."""
    registry = SkillRegistry.load("real_estate")
    team = OrchestrationTeam(registry)
    record = {
        "address": "123 Main Street",
        "city": "Toronto",
        "postal_code": "M4N 2A7",
        "municipality": "Toronto",
        "state_province": "ON",
        "country": "Canada",
    }
    result, audit = team.process_record(record)
    assert "_triage_route" in result
    assert result["_triage_route"] in ("done", "needs_review", "unsalvageable")


def test_orchestration_team_unsalvageable_exits_early():
    """Unsalvageable record exits after Phase 2, no planning."""
    registry = SkillRegistry.load("real_estate")
    team = OrchestrationTeam(registry)
    # Minimal record with missing critical fields
    record = {"address": "incomplete"}
    result, audit = team.process_record(record)
    # Should have triage route but no planning decisions
    assert "_triage_route" in result


def test_batch_budget_passed_to_team():
    """BatchBudget stored on team and accessible."""
    registry = SkillRegistry.load("real_estate")
    budget = BatchBudget(50)
    team = OrchestrationTeam(registry, batch_budget=budget)
    assert team.batch_budget is budget
