"""Tests for initialize_domain.py phase helpers."""
import json
import sys
from io import StringIO
from pathlib import Path
from unittest.mock import MagicMock, patch, call

import pytest

# Allow importing scripts as modules
sys.path.insert(0, str(Path(__file__).parent.parent.parent))


def _mock_conn(table_names: list[str]):
    conn = MagicMock()
    cur = conn.cursor.return_value.__enter__.return_value
    cur.fetchall.return_value = [{"table_name": t} for t in table_names]
    return conn


class TestPhaseHeader:
    def test_prints_double_border_and_phase_name(self, capsys):
        from scripts.initialize_domain import _phase_header
        _phase_header(1, "Schema Discovery")
        out = capsys.readouterr().out
        assert "═" in out
        assert "Phase 1" in out
        assert "Schema Discovery" in out


class TestPause:
    def test_default_yes_returns_true_on_enter(self):
        from scripts.initialize_domain import _pause
        with patch("builtins.input", return_value=""):
            assert _pause("Continue?", default_yes=True) is True

    def test_default_no_returns_false_on_enter(self):
        from scripts.initialize_domain import _pause
        with patch("builtins.input", return_value=""):
            assert _pause("Continue?", default_yes=False) is False

    def test_explicit_y_returns_true(self):
        from scripts.initialize_domain import _pause
        with patch("builtins.input", return_value="y"):
            assert _pause("Continue?") is True

    def test_explicit_n_returns_false(self):
        from scripts.initialize_domain import _pause
        with patch("builtins.input", return_value="n"):
            assert _pause("Continue?") is False


class TestPhase0:
    def test_skips_when_already_registered(self, tmp_path, capsys):
        from scripts.initialize_domain import phase0_register_tables
        from services.domain_initializer import DomainInitializer

        reg = tmp_path / "registry.json"
        reg.write_text(json.dumps({
            "domains": {"sports_ticketing": {"tables": ["events", "tickets"]}}
        }))
        di = DomainInitializer("sports_ticketing", registry_path=reg)
        conn = MagicMock()

        tables, _ = phase0_register_tables(di, conn)
        assert tables == ["events", "tickets"]
        assert "Using registered tables" in capsys.readouterr().out

    def test_presents_selection_when_unregistered(self, tmp_path, capsys):
        from scripts.initialize_domain import phase0_register_tables
        from services.domain_initializer import DomainInitializer

        reg = tmp_path / "registry.json"
        reg.write_text(json.dumps({"domains": {}}))
        di = DomainInitializer("sports_ticketing", registry_path=reg)
        conn = _mock_conn(["events", "tickets", "customers"])

        with patch("builtins.input", return_value="1,2,3"):
            tables, _ = phase0_register_tables(di, conn)

        assert set(tables) == {"events", "tickets", "customers"}
        data = json.loads(reg.read_text())
        assert "sports_ticketing" in data["domains"]

    def test_system_tables_flagged_in_output(self, tmp_path, capsys):
        from scripts.initialize_domain import phase0_register_tables
        from services.domain_initializer import DomainInitializer

        reg = tmp_path / "registry.json"
        reg.write_text(json.dumps({"domains": {}}))
        di = DomainInitializer("sports_ticketing", registry_path=reg)
        conn = _mock_conn(["events", "spell_corrections"])

        with patch("builtins.input", return_value="1"):
            phase0_register_tables(di, conn)

        out = capsys.readouterr().out
        assert "system table" in out

    def test_exits_when_no_selection_made(self, tmp_path):
        from scripts.initialize_domain import phase0_register_tables
        from services.domain_initializer import DomainInitializer

        reg = tmp_path / "registry.json"
        reg.write_text(json.dumps({"domains": {}}))
        di = DomainInitializer("sports_ticketing", registry_path=reg)
        conn = _mock_conn(["events"])

        with patch("builtins.input", return_value=""), pytest.raises(SystemExit):
            phase0_register_tables(di, conn)


class TestPhase1:
    def test_returns_schema_dict_keyed_by_table(self):
        from scripts.initialize_domain import phase1_schema_discovery

        conn = MagicMock()
        cur = conn.cursor.return_value.__enter__.return_value
        cur.fetchall.return_value = [
            {"name": "event_id", "type": "uuid", "notnull": True, "pk": True},
            {"name": "event_name", "type": "text", "notnull": False, "pk": False},
        ]
        schema = phase1_schema_discovery(["events"], conn)
        assert "events" in schema
        assert len(schema["events"]) == 2
        assert schema["events"][0]["name"] == "event_id"

    def test_prints_table_and_column_summary(self, capsys):
        from scripts.initialize_domain import phase1_schema_discovery

        conn = MagicMock()
        cur = conn.cursor.return_value.__enter__.return_value
        cur.fetchall.return_value = [
            {"name": "event_id", "type": "uuid", "notnull": True, "pk": True},
        ]
        phase1_schema_discovery(["events"], conn)
        out = capsys.readouterr().out
        assert "events" in out
        assert "event_id" in out
        assert "uuid" in out


class TestPhase2:
    def test_calls_annotation_service_with_declared_tables(self):
        conn = MagicMock()
        with patch("scripts.initialize_domain.MetadataAnnotationService") as mock_svc_cls, \
             patch("scripts.initialize_domain._build_llm_client", return_value=MagicMock()):
            # Import inside patch context
            from scripts.initialize_domain import phase2_annotation

            mock_svc = mock_svc_cls.return_value
            mock_svc.run.return_value = MagicMock(annotated=3, skipped=0, low_confidence=[])
            phase2_annotation("sports_ticketing", ["events", "tickets"], conn)

        mock_svc.run.assert_called_once()
        call_args = mock_svc.run.call_args
        # First positional arg is domain, second is conn, tables is passed as kwarg
        assert call_args[0][0] == "sports_ticketing"
        assert call_args[1]["tables"] == ["events", "tickets"]

    def test_prints_low_confidence_warning(self, capsys):
        conn = MagicMock()
        with patch("scripts.initialize_domain.MetadataAnnotationService") as mock_svc_cls, \
             patch("scripts.initialize_domain._build_llm_client", return_value=MagicMock()):
            # Import inside patch context
            from scripts.initialize_domain import phase2_annotation

            mock_svc = mock_svc_cls.return_value
            mock_svc.run.return_value = MagicMock(
                annotated=2,
                skipped=0,
                low_confidence=[{"table_name": "customers", "column_name": "postal_code", "confidence": 0.55}],
            )
            phase2_annotation("sports_ticketing", ["customers"], conn)

        out = capsys.readouterr().out
        assert "low-confidence" in out.lower() or "⚠" in out
        assert "postal_code" in out

    def test_prints_phase_header(self, capsys):
        conn = MagicMock()
        with patch("scripts.initialize_domain.MetadataAnnotationService") as mock_svc_cls, \
             patch("scripts.initialize_domain._build_llm_client", return_value=MagicMock()):
            # Import inside patch context
            from scripts.initialize_domain import phase2_annotation

            mock_svc = mock_svc_cls.return_value
            mock_svc.run.return_value = MagicMock(annotated=1, skipped=0, low_confidence=[])
            phase2_annotation("sports_ticketing", ["events"], conn)

        out = capsys.readouterr().out
        assert "Phase 2" in out or "Annotation" in out


class TestSampleTextColumns:
    def test_returns_samples_for_text_columns(self):
        from scripts.initialize_domain import _sample_text_columns

        schema = {
            "events": [
                {"name": "event_name", "type": "text",  "notnull": False, "pk": False},
                {"name": "event_id",   "type": "uuid",  "notnull": True,  "pk": True},
            ]
        }
        conn = MagicMock()
        cur = conn.cursor.return_value.__enter__.return_value
        cur.fetchall.return_value = [
            {"val": "Leafs vs Sens"},
            {"val": "Raptors vs Celtics"},
        ]
        samples = _sample_text_columns(schema, conn)
        assert "events.event_name" in samples
        assert "events.event_id" not in samples  # uuid, not text

    def test_skips_non_text_columns(self):
        from scripts.initialize_domain import _sample_text_columns

        schema = {
            "tickets": [
                {"name": "price",      "type": "numeric",     "notnull": False, "pk": False},
                {"name": "event_date", "type": "timestamptz", "notnull": False, "pk": False},
            ]
        }
        conn = MagicMock()
        samples = _sample_text_columns(schema, conn)
        assert samples == {}

    def test_handles_db_error_gracefully(self):
        from scripts.initialize_domain import _sample_text_columns

        schema = {
            "events": [{"name": "event_name", "type": "text", "notnull": False, "pk": False}]
        }
        conn = MagicMock()
        conn.cursor.return_value.__enter__.return_value.execute.side_effect = Exception("DB error")
        # Should not raise — just return empty or partial
        samples = _sample_text_columns(schema, conn)
        assert "events.event_name" not in samples or samples.get("events.event_name") == []


class TestLoadAnnotations:
    def test_returns_table_dot_column_keyed_dict(self):
        from scripts.initialize_domain import _load_annotations

        conn = MagicMock()
        cur = conn.cursor.return_value.__enter__.return_value
        cur.fetchall.return_value = [
            {"table_name": "events", "column_name": "event_name", "description": "Event name"},
            {"table_name": "customers", "column_name": "city", "description": "City name"},
        ]
        annotations = _load_annotations("sports_ticketing", conn)
        assert annotations["events.event_name"] == "Event name"
        assert annotations["customers.city"] == "City name"

    def test_returns_empty_dict_on_db_error(self):
        from scripts.initialize_domain import _load_annotations

        conn = MagicMock()
        conn.cursor.return_value.__enter__.return_value.execute.side_effect = Exception("DB error")
        result = _load_annotations("sports_ticketing", conn)
        assert result == {}


class TestPhase3:
    def _make_schema(self):
        return {
            "events": [
                {"name": "event_name", "type": "text", "notnull": False, "pk": False},
                {"name": "home_team",  "type": "text", "notnull": False, "pk": False},
            ]
        }

    def test_warns_when_no_text_data_found(self, capsys):
        from scripts.initialize_domain import phase3_seed_research

        schema = self._make_schema()
        conn = MagicMock()

        with patch("scripts.initialize_domain._sample_text_columns", return_value={}), \
             patch("scripts.initialize_domain._load_annotations", return_value={}), \
             patch("scripts.initialize_domain.DomainResearcher") as mock_dr, \
             patch("builtins.input", return_value=""):
            mock_dr.return_value.get_filtered_questions.return_value = []
            mock_dr.return_value.research_with_schema.return_value = MagicMock(
                spell_corrections=[], query_packs=[], column_descriptions=[]
            )
            mock_dr.return_value.write_seeds.return_value = []
            with patch("scripts.initialize_domain._build_llm_client",
                       return_value=MagicMock()):
                phase3_seed_research("sports_ticketing", schema, conn)

        out = capsys.readouterr().out
        assert "no data" in out.lower() or "skipped" in out.lower() or "empty" in out.lower() or "⚠" in out

    def test_calls_research_with_schema_when_data_present(self):
        from scripts.initialize_domain import phase3_seed_research

        schema = self._make_schema()
        conn = MagicMock()
        samples = {"events.event_name": ["Leafs vs Sens"], "events.home_team": ["Leafs"]}

        with patch("scripts.initialize_domain._sample_text_columns", return_value=samples), \
             patch("scripts.initialize_domain._load_annotations", return_value={}), \
             patch("scripts.initialize_domain.DomainResearcher") as mock_dr, \
             patch("scripts.initialize_domain._build_llm_client",
                   return_value=MagicMock()), \
             patch("scripts.initialize_domain.SeederRegistry"), \
             patch("builtins.input", side_effect=["", "", "", "", "", "", "", "y"]):
            mock_bundle = MagicMock()
            mock_bundle.spell_corrections = []
            mock_bundle.query_packs = []
            mock_bundle.column_descriptions = []
            mock_dr.return_value.research_with_schema.return_value = mock_bundle
            mock_dr.return_value.get_filtered_questions.return_value = []
            mock_dr.return_value.write_seeds.return_value = []
            phase3_seed_research("sports_ticketing", schema, conn)

        mock_dr.return_value.research_with_schema.assert_called_once()


class TestCmdAddTable:
    def test_reports_no_new_tables_when_all_registered(self, tmp_path, capsys):
        from scripts.initialize_domain import cmd_add_table
        from services.domain_initializer import DomainInitializer

        reg = tmp_path / "r.json"
        reg.write_text(json.dumps({"domains": {"sports_ticketing": {"tables": ["events"]}}}))
        di = DomainInitializer("sports_ticketing", registry_path=reg)
        conn = _mock_conn(["events", "column_metadata"])  # column_metadata is system table

        cmd_add_table("sports_ticketing", di, conn)
        assert "No new tables" in capsys.readouterr().out

    def test_shows_new_tables_and_registers_selection(self, tmp_path, capsys):
        from scripts.initialize_domain import cmd_add_table
        from services.domain_initializer import DomainInitializer

        reg = tmp_path / "r.json"
        reg.write_text(json.dumps({"domains": {"sports_ticketing": {"tables": ["events"]}}}))
        di = DomainInitializer("sports_ticketing", registry_path=reg)
        conn = _mock_conn(["events", "venues", "promotions"])

        with patch("scripts.initialize_domain.phase2_annotation"), \
             patch("builtins.input", side_effect=["1", "n"]):  # select venues, no seed refresh
            cmd_add_table("sports_ticketing", di, conn)

        data = json.loads(reg.read_text())
        assert "venues" in data["domains"]["sports_ticketing"]["tables"]
        assert "events" in data["domains"]["sports_ticketing"]["tables"]

    def test_annotates_new_table_after_registration(self, tmp_path):
        from scripts.initialize_domain import cmd_add_table
        from services.domain_initializer import DomainInitializer

        reg = tmp_path / "r.json"
        reg.write_text(json.dumps({"domains": {"sports_ticketing": {"tables": ["events"]}}}))
        di = DomainInitializer("sports_ticketing", registry_path=reg)
        conn = _mock_conn(["events", "venues"])

        with patch("scripts.initialize_domain.phase2_annotation") as mock_p2, \
             patch("builtins.input", side_effect=["1", "n"]):
            cmd_add_table("sports_ticketing", di, conn)

        mock_p2.assert_called_once()
        # Tables passed to phase2 should include the newly added table
        call_tables = mock_p2.call_args[0][1]
        assert "venues" in call_tables


class TestCmdRefreshSeeds:
    def test_exits_if_domain_not_registered(self, tmp_path, capsys):
        from scripts.initialize_domain import cmd_refresh_seeds
        from services.domain_initializer import DomainInitializer

        reg = tmp_path / "r.json"
        reg.write_text(json.dumps({"domains": {}}))
        di = DomainInitializer("sports_ticketing", registry_path=reg)
        conn = MagicMock()

        with pytest.raises(SystemExit):
            cmd_refresh_seeds("sports_ticketing", di, conn)

        assert "not registered" in capsys.readouterr().out

    def test_calls_phase1_and_phase3_only(self, tmp_path):
        from scripts.initialize_domain import cmd_refresh_seeds
        from services.domain_initializer import DomainInitializer

        reg = tmp_path / "r.json"
        reg.write_text(json.dumps({"domains": {"sports_ticketing": {"tables": ["events"]}}}))
        di = DomainInitializer("sports_ticketing", registry_path=reg)
        conn = MagicMock()

        with patch("scripts.initialize_domain.phase1_schema_discovery") as mock_p1, \
             patch("scripts.initialize_domain.phase3_seed_research") as mock_p3:
            mock_p1.return_value = {"events": []}
            cmd_refresh_seeds("sports_ticketing", di, conn)

        mock_p1.assert_called_once_with(["events"], conn, "public")
        mock_p3.assert_called_once()
