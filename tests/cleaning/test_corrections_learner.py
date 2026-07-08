"""Tests for self-learning spell corrections (audit finding 3.2)."""
import importlib.util
import sys
from pathlib import Path
from unittest.mock import MagicMock

from cleaning.corrections_learner import (
    propose_corrections,
    record_learned_corrections,
)
from skills.registry import SkillRegistry

_ORCH_PATH = Path(__file__).resolve().parent.parent.parent / "cleaning" / "orchestrator_v2.py"
_spec = importlib.util.spec_from_file_location("cleaning.orchestrator_v2", _ORCH_PATH)
_orch_mod = importlib.util.module_from_spec(_spec)
sys.modules["cleaning.orchestrator_v2"] = _orch_mod
_spec.loader.exec_module(_orch_mod)
OrchestrationTeam = _orch_mod.OrchestrationTeam


# ── propose_corrections ───────────────────────────────────────────────────────

def test_small_edit_change_is_proposed():
    props = propose_corrections(
        {"city": "Torotno"}, {"city": "Toronto"}, ["city"]
    )
    assert props == [{"wrong": "torotno", "right": "toronto"}]


def test_semantic_replacement_is_not_proposed():
    """'Downtown' → 'Toronto' is a semantic fix, not a misspelling — skip."""
    assert propose_corrections(
        {"city": "Downtown"}, {"city": "Toronto"}, ["city"]
    ) == []


def test_case_only_change_and_digits_skipped():
    assert propose_corrections({"city": "toronto"}, {"city": "Toronto"}, ["city"]) == []
    assert propose_corrections({"city": "M5V 2T6"}, {"city": "M5V 2T7"}, ["city"]) == []


def test_untracked_fields_ignored():
    assert propose_corrections(
        {"city": "Torotno"}, {"city": "Toronto"}, ["municipality"]
    ) == []


# ── record_learned_corrections ────────────────────────────────────────────────

def test_record_inserts_with_learned_source():
    conn = MagicMock()
    cur = conn.cursor.return_value.__enter__.return_value
    cur.rowcount = 1
    n = record_learned_corrections(conn, "real_estate",
                                   [{"wrong": "torotno", "right": "toronto"}])
    assert n == 1
    sql, params = cur.execute.call_args[0]
    assert "ON CONFLICT (wrong, domain) DO NOTHING" in sql
    assert params[:4] == ("torotno", "real_estate", "toronto", "learned")
    conn.commit.assert_called_once()


def test_record_noop_without_conn_or_proposals():
    assert record_learned_corrections(None, "d", [{"wrong": "a", "right": "b"}]) == 0
    assert record_learned_corrections(MagicMock(), "d", []) == 0


# ── orchestrator integration ──────────────────────────────────────────────────

def _registry_with_spell_and_enricher(conn):
    registry = MagicMock(spec=SkillRegistry)
    registry.metadata = {}
    registry.runtime = {"pg_conn": conn}
    registry.domain = "real_estate"

    spell = MagicMock()
    spell.text_fields = ["city"]

    def get(name):
        return spell if name == "spell_checker" else None

    registry.get.side_effect = get
    return registry


def test_batch_learns_post_deterministic_change():
    conn = MagicMock()
    cur = conn.cursor.return_value.__enter__.return_value
    cur.rowcount = 1
    registry = _registry_with_spell_and_enricher(conn)
    team = OrchestrationTeam(registry)

    # Simulate an enrichment phase fixing the city after phase 1
    original = team.process_record

    def enriched(record):
        out, audit = original(record)
        if "_post_deterministic" in out:
            out["city"] = "Toronto"
        return out, audit

    team.process_record = enriched
    processed, audit = team.process_batch([{"id": 1, "city": "Torotno"}])

    assert "_post_deterministic" not in processed[0]
    assert any("Learned 1" in e.get("decision", "") for e in audit)
    params = cur.execute.call_args[0][1]
    assert params[0] == "torotno" and params[2] == "toronto"


def test_batch_does_not_learn_phase1_changes():
    """Value unchanged after the snapshot → nothing learned."""
    conn = MagicMock()
    registry = _registry_with_spell_and_enricher(conn)
    team = OrchestrationTeam(registry)
    processed, audit = team.process_batch([{"id": 1, "city": "Toronto"}])
    assert not any("Learned" in e.get("decision", "") for e in audit)
