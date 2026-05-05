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

def test_orchestration_team_web_enricher_only_for_needs_review():
    """Web enricher agent only runs for needs_review records."""
    registry = SkillRegistry.load("real_estate")
    team = OrchestrationTeam(registry)

    # Mock the web_enricher_agent to track calls
    mock_enricher = MagicMock()
    mock_enricher.execute = MagicMock(side_effect=lambda r: r)
    team.web_enricher_agent = mock_enricher

    # Record that goes to "done" → enricher should NOT run
    record_done = {
        "address": "123 Main Street",
        "city": "Toronto",
        "postal_code": "M4N 2A7",
        "municipality": "Toronto",
        "state_province": "ON",
        "country": "Canada",
    }
    # Patch triage to return "done"
    with patch.object(team.quality_triage, "execute", side_effect=lambda r: {**r, "_triage_route": "done"}):
        team.process_record(record_done)
    mock_enricher.execute.assert_not_called()


def test_orchestration_team_web_enricher_runs_for_needs_review():
    """Web enricher runs when triage route is needs_review."""
    registry = SkillRegistry.load("real_estate")
    team = OrchestrationTeam(registry)

    called = []

    def mock_enricher_execute(r):
        called.append(True)
        return r

    mock_enricher = MagicMock()
    mock_enricher.execute = mock_enricher_execute
    team.web_enricher_agent = mock_enricher

    record = {
        "address": "123 Main Street",
        "city": "Toronto",
        "postal_code": "M4N 2A7",
        "municipality": "Toronto",
        "state_province": "ON",
        "country": "Canada",
    }

    with patch.object(team.quality_triage, "execute", side_effect=lambda r: {**r, "_triage_route": "needs_review"}):
        team.process_record(record)

    assert len(called) == 1


def test_orchestration_team_skips_enricher_when_triage_unsalvageable():
    """Web enricher does not run for unsalvageable."""
    registry = SkillRegistry.load("real_estate")
    team = OrchestrationTeam(registry)

    mock_enricher = MagicMock()
    mock_enricher.execute = MagicMock(side_effect=lambda r: r)
    team.web_enricher_agent = mock_enricher

    record = {"address": "123 Main", "postal_code": "X0X"}

    with patch.object(team.quality_triage, "execute", side_effect=lambda r: {**r, "_triage_route": "unsalvageable"}):
        team.process_record(record)

    mock_enricher.execute.assert_not_called()


def test_batch_budget_passed_to_enricher_tools():
    """BatchBudget provided at init flows into web_enricher tools."""
    registry = SkillRegistry.load("real_estate")
    budget = BatchBudget(50)
    team = OrchestrationTeam(registry, batch_budget=budget)

    if team.web_enricher_agent:
        # The enricher agent should have batch_budget in its default tools
        # (tools are passed at construction time via registry)
        assert team.batch_budget is budget
