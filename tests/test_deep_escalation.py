"""Tests for the deep-tier escalation skill (v2 Phase 6)."""
import importlib.util
import sys
from pathlib import Path
from unittest.mock import MagicMock

from cleaning.flags import Flag, FlagSeverity, FlagType
from cleaning.types import CleaningOutput
from skills._common.deep_escalation.deep_escalation import DeepEscalation
from skills.registry import SkillRegistry

_ORCH_PATH = Path(__file__).resolve().parent.parent / "cleaning" / "orchestrator_v2.py"
_spec = importlib.util.spec_from_file_location("cleaning.orchestrator_v2", _ORCH_PATH)
_orch_mod = importlib.util.module_from_spec(_spec)
sys.modules["cleaning.orchestrator_v2"] = _orch_mod
_spec.loader.exec_module(_orch_mod)
OrchestrationTeam = _orch_mod.OrchestrationTeam
BatchBudget = _orch_mod.BatchBudget


def _escalator_returning(cleaned: dict, flags):
    esc = MagicMock()
    esc.investigate.return_value = CleaningOutput(
        cleaned_record=cleaned, flags=flags
    )
    return esc


def test_skips_records_not_needing_review():
    esc = MagicMock()
    skill = DeepEscalation({"escalator": esc})
    record = {"id": 1, "_triage_route": "done"}
    assert skill.run(dict(record)) == record
    esc.investigate.assert_not_called()


def test_merges_resolved_fields_and_flags():
    esc = _escalator_returning(
        {"id": 1, "municipality": "Toronto"},
        [Flag(flag_type=FlagType.RESOLVED_AFTER_ESCALATION,
              severity=FlagSeverity.INFO, reason="", raised_by="escalator")],
    )
    skill = DeepEscalation({"escalator": esc})
    record = {"id": 1, "municipality": "", "_triage_route": "needs_review",
              "_gap_hints": ["municipality_unresolved"]}
    out = skill.run(record)
    assert out["municipality"] == "Toronto"
    assert out["_escalation_flags"] == ["resolved_after_escalation"]
    # gap hint was mapped to a FlagType hint
    hints = esc.investigate.call_args.kwargs["flag_hints"]
    assert hints == [FlagType.MUNICIPALITY_UNRESOLVED]


def test_prior_web_evidence_becomes_search_log():
    esc = _escalator_returning({"id": 1}, [])
    skill = DeepEscalation({"escalator": esc})
    record = {
        "id": 1, "_triage_route": "needs_review",
        "_web_search_evidence": [{"query": "toronto M5V", "snippet": "found it"}],
    }
    skill.run(record)
    log = esc.investigate.call_args.kwargs["prior_search_log"]
    assert len(log) == 1 and log[0].query == "toronto M5V"


def test_escalator_exception_leaves_record_intact():
    esc = MagicMock()
    esc.investigate.side_effect = RuntimeError("deep model down")
    skill = DeepEscalation({"escalator": esc})
    record = {"id": 1, "_triage_route": "needs_review"}
    out = skill.run(dict(record))
    assert out["id"] == 1
    assert any("failed" in e["decision"].lower() for e in skill.get_audit())


def test_orchestrator_runs_phase6_only_for_needs_review():
    registry = MagicMock(spec=SkillRegistry)
    registry.metadata = {}
    registry.runtime = {}
    registry.domain = None
    deep = MagicMock()
    deep.run.side_effect = lambda r, tools=None: {**r, "_deep_ran": True}
    deep.get_audit.return_value = []

    def get(name):
        return deep if name == "deep_escalation" else None

    registry.get.side_effect = get
    team = OrchestrationTeam(registry)

    out, _ = team.process_record({"id": 1, "_triage_route": "needs_review"})
    assert out.get("_deep_ran") is True


def test_orchestrator_phase6_respects_budget():
    registry = MagicMock(spec=SkillRegistry)
    registry.metadata = {}
    registry.runtime = {}
    registry.domain = None
    deep = MagicMock()

    def get(name):
        return deep if name == "deep_escalation" else None

    registry.get.side_effect = get
    budget = BatchBudget(max_queries=0)
    team = OrchestrationTeam(registry, batch_budget=budget)

    _, audit = team.process_record({"id": 1, "_triage_route": "needs_review"})
    deep.run.assert_not_called()
    assert any("budget" in e.get("reason", "").lower() or
               "budget" in e.get("decision", "").lower() for e in audit)
