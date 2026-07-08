"""Tests for the 2026-07-08 audit fix-now batch.

Covers:
- 2.2: annotation LLM *call* failures are not persisted (parse failures still are)
- 3.4: a record that raises mid-batch doesn't lose the rest of the batch
- 3.5: CleaningRunReport aggregates triage routes instead of hardcoding zeros
"""
import importlib.util
import sys
from pathlib import Path
from unittest.mock import MagicMock, patch

from services.metadata_annotation import MetadataAnnotationService

# Load orchestrator_v2 directly (cleaning/__init__.py has legacy imports) —
# same pattern as tests/test_metadata_annotation.py.
_ORCH_PATH = Path(__file__).resolve().parent.parent / "cleaning" / "orchestrator_v2.py"
_spec = importlib.util.spec_from_file_location("cleaning.orchestrator_v2", _ORCH_PATH)
_orch_mod = importlib.util.module_from_spec(_spec)
sys.modules["cleaning.orchestrator_v2"] = _orch_mod
_spec.loader.exec_module(_orch_mod)
OrchestrationTeam = _orch_mod.OrchestrationTeam

from skills.registry import SkillRegistry


def _mock_conn(*fetchall_results):
    conn = MagicMock()
    cur = conn.cursor.return_value.__enter__.return_value
    cur.fetchall.side_effect = list(fetchall_results)
    return conn, cur


# ── 2.2 annotation call failure is not persisted ─────────────────────────────

def test_annotation_call_failure_not_persisted():
    llm = MagicMock()
    llm.messages_create.side_effect = RuntimeError("API down")
    svc = MetadataAnnotationService(llm_client=llm)
    conn, cur = _mock_conn(
        [],                          # existing annotations
        [{"column_name": "city"}],   # raw_data columns
        [],                          # samples
    )
    with patch("services.metadata_annotation.SeederRegistry") as mock_sr:
        mock_sr.return_value.manifest = {"description": "Test domain"}
        report = svc.run("test_domain", conn, tables=["raw_data"])

    assert report.annotated == 0
    assert report.failed == [{"table_name": "raw_data", "column_name": "city"}]
    # No INSERT into column_metadata was attempted
    inserts = [c for c in cur.execute.call_args_list
               if "INSERT INTO data_details.column_metadata" in str(c)]
    assert inserts == []


def test_annotation_parse_failure_still_persisted():
    """LLM answered but with bad JSON — keep the low-confidence fallback behavior."""
    llm = MagicMock()
    llm.messages_create.return_value.content = [MagicMock(text="not json")]
    svc = MetadataAnnotationService(llm_client=llm)
    conn, _ = _mock_conn([], [{"column_name": "city"}], [])
    with patch("services.metadata_annotation.SeederRegistry") as mock_sr:
        mock_sr.return_value.manifest = {"description": "Test domain"}
        report = svc.run("test_domain", conn, tables=["raw_data"])

    assert report.annotated == 1
    assert report.failed == []


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
