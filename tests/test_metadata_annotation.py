"""Tests for MetadataAnnotationService and build_annotation_prompt."""
import json
from unittest.mock import MagicMock, patch

import pytest

from prompts.annotation import build_table_annotation_prompt


def test_build_table_annotation_prompt_contains_all_inputs():
    prompt = build_table_annotation_prompt(
        domain="real_estate",
        domain_description="Real estate property listings — Toronto/Canada focus",
        table_name="raw_data",
        columns=[{"name": "postal_code", "samples": ["M5V 2T6", "K1A 0A9"]},
                 {"name": "city", "samples": []}],
    )
    assert "real_estate" in prompt
    assert "Real estate property listings" in prompt
    assert "raw_data" in prompt
    assert "postal_code" in prompt
    assert "M5V 2T6" in prompt
    assert "table_description" in prompt


def test_build_table_annotation_prompt_empty_samples_says_none():
    prompt = build_table_annotation_prompt("test", "Test domain", "raw_data",
                                           [{"name": "ref_1", "samples": []}])
    assert "ref_1" in prompt
    assert "none available" in prompt


from services.metadata_annotation import AnnotationReport, MetadataAnnotationService


# Helper: build a mock conn whose cursor().fetchall() returns results in sequence
def _mock_conn(*fetchall_results):
    """Build a mock psycopg2 connection with cursor returning preset data."""
    conn = MagicMock()
    cur = conn.cursor.return_value.__enter__.return_value
    cur.fetchall.side_effect = list(fetchall_results)
    return conn, cur


# --- list_gaps ---

def test_list_gaps_returns_unannotated_columns():
    svc = MetadataAnnotationService(llm_client=None)
    conn, _ = _mock_conn(
        [{"table_name": "raw_data", "column_name": "postal_code"}],   # existing annotations
        [{"column_name": "id"}, {"column_name": "postal_code"}, {"column_name": "city"}],  # raw_data columns
        [],                                      # cleaned_data columns
    )
    gaps = svc.list_gaps("real_estate", conn, tables=["raw_data", "cleaned_data"])
    assert {"table_name": "raw_data", "column_name": "id"} in gaps
    assert {"table_name": "raw_data", "column_name": "city"} in gaps
    assert {"table_name": "raw_data", "column_name": "postal_code"} not in gaps


def test_list_gaps_empty_when_all_annotated():
    svc = MetadataAnnotationService(llm_client=None)
    conn, _ = _mock_conn(
        [{"table_name": "raw_data", "column_name": "city"}],   # existing
        [{"column_name": "city"}],              # raw_data columns
    )
    assert svc.list_gaps("real_estate", conn, tables=["raw_data"]) == []


# --- run ---

def test_run_annotates_gaps_and_returns_report():
    llm = MagicMock()
    llm.messages_create.return_value.content = [
        MagicMock(text='{"table_description": "Raw listings", "columns": '
                       '[{"column_name": "city", "description": "City name field", '
                       '"confidence": 0.90}]}')
    ]
    svc = MetadataAnnotationService(llm_client=llm)
    conn, cur = _mock_conn(
        [],            # no existing annotations
        [{"column_name": "city"}],   # raw_data columns
        [],            # sample values for city
    )
    with patch("services.metadata_annotation.SeederRegistry") as mock_sr:
        mock_sr.return_value.manifest = {"description": "Test domain"}
        report = svc.run("test_domain", conn, tables=["raw_data"])

    assert report.annotated == 1
    assert report.skipped == 0
    assert report.low_confidence == []
    cur.execute.assert_called()  # upsert was attempted


def test_run_skips_existing_when_not_forced():
    llm = MagicMock()
    svc = MetadataAnnotationService(llm_client=llm)
    conn, _ = _mock_conn(
        [{"table_name": "raw_data", "column_name": "city"}],   # already annotated
        [{"column_name": "city"}],
    )
    with patch("services.metadata_annotation.SeederRegistry") as mock_sr:
        mock_sr.return_value.manifest = {"description": "Test domain"}
        report = svc.run("test_domain", conn, force=False, tables=["raw_data"])

    assert report.annotated == 0
    assert report.skipped == 1
    llm.messages_create.assert_not_called()


def test_run_flags_low_confidence():
    llm = MagicMock()
    llm.messages_create.return_value.content = [
        MagicMock(text='{"columns": [{"column_name": "ref_1", '
                       '"description": "Unknown ref field", "confidence": 0.40}]}')
    ]
    svc = MetadataAnnotationService(llm_client=llm)
    conn, _ = _mock_conn(
        [],
        [{"column_name": "ref_1"}],
        [],  # samples
    )
    with patch("services.metadata_annotation.SeederRegistry") as mock_sr:
        mock_sr.return_value.manifest = {"description": "Test domain"}
        report = svc.run("test_domain", conn, tables=["raw_data"])

    assert len(report.low_confidence) == 1
    assert report.low_confidence[0]["column_name"] == "ref_1"
    assert report.low_confidence[0]["confidence"] == pytest.approx(0.40)


def test_run_handles_malformed_llm_response():
    llm = MagicMock()
    llm.messages_create.return_value.content = [
        MagicMock(text="not json at all")
    ]
    svc = MetadataAnnotationService(llm_client=llm)
    conn, _ = _mock_conn([], [{"column_name": "city"}], [])
    with patch("services.metadata_annotation.SeederRegistry") as mock_sr:
        mock_sr.return_value.manifest = {"description": "Test domain"}
        report = svc.run("test_domain", conn, tables=["raw_data"])

    assert report.annotated == 1
    assert report.low_confidence[0]["confidence"] < 0.70


# --- CLI tests ---

import sys
from unittest.mock import patch, MagicMock


def test_cli_dry_run_prints_gaps(capsys):
    """Dry run prints gaps without writing to DB."""
    import scripts.annotate_domain as cli_module

    with patch.object(cli_module, "get_db_connection", return_value=MagicMock()), \
         patch("services.domain_initializer.DomainInitializer.get_registered_tables",
               return_value=["raw_data"]), \
         patch("services.metadata_annotation.MetadataAnnotationService.list_gaps",
               return_value=[{"table_name": "raw_data", "column_name": "city"}]), \
         patch("sys.argv", ["annotate_domain.py", "--domain", "real_estate", "--dry-run"]):
        cli_module.main()

    captured = capsys.readouterr()
    assert "raw_data.city" in captured.out


def test_cli_dry_run_no_gaps_message(capsys):
    """Dry run prints no-gaps message when all columns annotated."""
    import scripts.annotate_domain as cli_module

    with patch.object(cli_module, "get_db_connection", return_value=MagicMock()), \
         patch("services.domain_initializer.DomainInitializer.get_registered_tables",
               return_value=["raw_data"]), \
         patch("services.metadata_annotation.MetadataAnnotationService.list_gaps",
               return_value=[]), \
         patch("sys.argv", ["annotate_domain.py", "--domain", "real_estate", "--dry-run"]):
        cli_module.main()

    captured = capsys.readouterr()
    assert "No annotation gaps" in captured.out


# --- OrchestrationTeam annotation gap warnings ---

import importlib.util
import logging
import sys
import types as _types
from pathlib import Path

# cleaning/__init__.py has legacy module-level imports (db_helpers, pre_cleaner, etc.)
# that are not present in this environment. Load orchestrator_v2 directly from its
# file path to bypass __init__.py entirely.
_ORCH_PATH = Path(__file__).resolve().parent.parent / "cleaning" / "orchestrator_v2.py"
_spec = importlib.util.spec_from_file_location("cleaning.orchestrator_v2", _ORCH_PATH)
_orch_mod = importlib.util.module_from_spec(_spec)
sys.modules["cleaning.orchestrator_v2"] = _orch_mod
_spec.loader.exec_module(_orch_mod)
OrchestrationTeam = _orch_mod.OrchestrationTeam

from skills.registry import SkillRegistry


def test_orchestration_team_warns_on_annotation_gaps(caplog):
    """OrchestrationTeam warns at init when domain columns lack annotations."""
    registry = MagicMock(spec=SkillRegistry)
    registry.get.return_value = None
    registry.metadata = {}
    registry.domain = "real_estate"

    mock_conn = MagicMock()
    cur = mock_conn.cursor.return_value.__enter__.return_value
    cur.fetchall.side_effect = [
        [],           # _get_existing_annotations: no annotations
        [{"column_name": "city"}],  # _get_table_columns raw_data
        [],           # _get_table_columns cleaned_data
    ]
    registry.runtime = {"pg_conn": mock_conn}

    with patch("services.domain_initializer.DomainInitializer.get_registered_tables",
               return_value=["raw_data", "cleaned_data"]), \
         caplog.at_level(logging.WARNING, logger="cleaning.orchestrator_v2"):
        OrchestrationTeam(registry)

    assert any("annotation" in msg.lower() for msg in caplog.messages)


def test_orchestration_team_no_warning_when_annotated(caplog):
    """No warning when all columns are annotated."""
    registry = MagicMock(spec=SkillRegistry)
    registry.get.return_value = None
    registry.metadata = {}
    registry.domain = "real_estate"

    mock_conn = MagicMock()
    cur = mock_conn.cursor.return_value.__enter__.return_value
    cur.fetchall.side_effect = [
        [{"table_name": "raw_data", "column_name": "city"}, {"table_name": "cleaned_data", "column_name": "city"}],  # existing
        [{"column_name": "city"}],                          # raw_data cols
        [{"column_name": "city"}],                          # cleaned_data cols
    ]
    registry.runtime = {"pg_conn": mock_conn}

    with patch("services.domain_initializer.DomainInitializer.get_registered_tables",
               return_value=["raw_data", "cleaned_data"]), \
         caplog.at_level(logging.WARNING, logger="cleaning.orchestrator_v2"):
        OrchestrationTeam(registry)

    assert not any("annotation" in msg.lower() for msg in caplog.messages)


# --- table-level annotation (audit finding 2.1) ---

def test_run_makes_one_llm_call_per_table_and_stores_table_row():
    llm = MagicMock()
    llm.messages_create.return_value.content = [MagicMock(text=json.dumps({
        "table_description": "One row per property listing.",
        "columns": [
            {"column_name": "city", "description": "Municipality of the listing", "confidence": 0.9},
            {"column_name": "price", "description": "Listing price in CAD", "confidence": 0.85},
        ],
    }))]
    svc = MetadataAnnotationService(llm_client=llm)
    conn, cur = _mock_conn(
        [],  # existing annotations
        [{"column_name": "city"}, {"column_name": "price"}],  # columns
        [], [],  # samples for city, price
    )
    with patch("services.metadata_annotation.SeederRegistry") as mock_sr:
        mock_sr.return_value.manifest = {"description": "Test domain"}
        report = svc.run("test_domain", conn, tables=["raw_data"])

    assert report.annotated == 2
    assert llm.messages_create.call_count == 1  # one call for the whole table
    upserted_cols = [c[0][1][2] for c in cur.execute.call_args_list
                     if "INSERT INTO data_details.column_metadata" in c[0][0]]
    assert MetadataAnnotationService.TABLE_ROW in upserted_cols


def test_run_call_failure_marks_all_table_columns_failed():
    llm = MagicMock()
    llm.messages_create.side_effect = RuntimeError("down")
    svc = MetadataAnnotationService(llm_client=llm)
    conn, cur = _mock_conn(
        [],
        [{"column_name": "a"}, {"column_name": "b"}],
        [], [],
    )
    with patch("services.metadata_annotation.SeederRegistry") as mock_sr:
        mock_sr.return_value.manifest = {"description": "Test domain"}
        report = svc.run("test_domain", conn, tables=["raw_data"])

    assert report.annotated == 0
    assert len(report.failed) == 2
    # nothing was persisted — no junk rows blocking re-annotation
    inserts = [c for c in cur.execute.call_args_list
               if "INSERT INTO data_details.column_metadata" in str(c)]
    assert inserts == []


def test_missing_column_in_response_gets_low_confidence_fallback():
    llm = MagicMock()
    llm.messages_create.return_value.content = [MagicMock(text=json.dumps({
        "columns": [{"column_name": "city", "description": "City", "confidence": 0.9}],
    }))]
    svc = MetadataAnnotationService(llm_client=llm)
    conn, _ = _mock_conn(
        [],
        [{"column_name": "city"}, {"column_name": "mystery"}],
        [], [],
    )
    with patch("services.metadata_annotation.SeederRegistry") as mock_sr:
        mock_sr.return_value.manifest = {"description": "Test domain"}
        report = svc.run("test_domain", conn, tables=["raw_data"])

    assert report.annotated == 2
    assert [lc["column_name"] for lc in report.low_confidence] == ["mystery"]


def test_get_sample_values_redacts_pii_column_without_querying_db():
    svc = MetadataAnnotationService(llm_client=MagicMock())
    conn = MagicMock()
    samples = svc._get_sample_values("raw_data", "email_address", conn)
    assert samples == ["<redacted>"]
    conn.cursor.assert_not_called()


def test_get_sample_values_orders_by_random_for_non_pii_column():
    svc = MetadataAnnotationService(llm_client=MagicMock())
    conn, cur = _mock_conn([{"city": "Toronto"}])
    svc._get_sample_values("raw_data", "city", conn)
    executed_sql = cur.execute.call_args[0][0].as_string(None)
    assert "ORDER BY random()" in executed_sql
