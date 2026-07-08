"""Orchestrator v2 batch resilience and report aggregation.

- a record that raises mid-batch doesn't lose the rest of the batch
- CleaningRunReport aggregates triage routes instead of hardcoding zeros
"""
import importlib.util
import sys
from pathlib import Path
from unittest.mock import MagicMock, patch

# Load orchestrator_v2 directly (cleaning/__init__.py has legacy imports) —
# same pattern as tests/test_metadata_annotation.py.
_ORCH_PATH = Path(__file__).resolve().parent.parent / "cleaning" / "orchestrator_v2.py"
_spec = importlib.util.spec_from_file_location("cleaning.orchestrator_v2", _ORCH_PATH)
_orch_mod = importlib.util.module_from_spec(_spec)
sys.modules["cleaning.orchestrator_v2"] = _orch_mod
_spec.loader.exec_module(_orch_mod)
OrchestrationTeam = _orch_mod.OrchestrationTeam

from skills.registry import SkillRegistry


# ── 3.4 / 3.5 orchestrator batch resilience + report aggregation ─────────────

def _team_with_no_skills():
    registry = MagicMock(spec=SkillRegistry)
    registry.get.return_value = None
    registry.metadata = {}
    registry.runtime = {}
    registry.domain = None
    return OrchestrationTeam(registry)


def test_process_batch_survives_record_exception():
    team = _team_with_no_skills()
    records = [{"id": 1}, {"id": 2}, {"id": 3}]

    original = team.process_record

    def flaky(record):
        if record["id"] == 2:
            raise ValueError("boom")
        return original(record)

    team.process_record = flaky
    processed, audit = team.process_batch(records)

    assert len(processed) == 3
    assert processed[1]["id"] == 2
    assert processed[1]["_error"] == "boom"
    assert any(e.get("skill") == "OrchestrationTeam" and "boom" in e.get("reason", "")
               for e in audit)


def test_report_aggregates_triage_routes():
    records = [
        {"id": 1, "_triage_route": "done"},
        {"id": 2, "_triage_route": "needs_review"},
        {"id": 3, "_triage_route": "unsalvageable"},
        {"id": 4, "_triage_route": "needs_review"},
    ]
    with patch.object(_orch_mod.SkillRegistry, "load") as mock_load, \
         patch.object(_orch_mod.OrchestrationTeam, "process_batch",
                      return_value=(records, [])):
        mock_load.return_value = MagicMock(spec=SkillRegistry, metadata={}, runtime={},
                                           domain=None)
        mock_load.return_value.get.return_value = None
        report = _orch_mod.run_cleaning_workflow_v2([{"id": i} for i in range(1, 5)])

    assert report.flags_by_type == {"done": 1, "needs_review": 2, "unsalvageable": 1}
    assert report.flagged_count == 3  # everything not routed "done"
