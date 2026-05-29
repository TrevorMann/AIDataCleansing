"""Tests for DomainInitializer — table discovery, scoring, registry management."""
import json
from pathlib import Path
from unittest.mock import MagicMock

import pytest

from services.domain_initializer import DomainInitializer, SYSTEM_TABLES


def _make_registry(tmp_path: Path, domains: dict = None) -> Path:
    registry = {
        "_note": "test",
        "active_domain": "test",
        "domains": domains or {},
    }
    p = tmp_path / "domain_registry.json"
    p.write_text(json.dumps(registry))
    return p


def _mock_conn(table_names: list[str]):
    conn = MagicMock()
    cur = conn.cursor.return_value.__enter__.return_value
    cur.fetchall.return_value = [{"table_name": t} for t in table_names]
    return conn


class TestGetRegisteredTables:
    def test_returns_none_when_domain_not_in_registry(self, tmp_path):
        reg = _make_registry(tmp_path)
        di = DomainInitializer("sports_ticketing", registry_path=reg)
        assert di.get_registered_tables() is None

    def test_returns_none_when_domain_has_no_tables_key(self, tmp_path):
        reg = _make_registry(tmp_path, {"sports_ticketing": {"label": "x"}})
        di = DomainInitializer("sports_ticketing", registry_path=reg)
        assert di.get_registered_tables() is None

    def test_returns_tables_when_registered(self, tmp_path):
        reg = _make_registry(tmp_path, {"real_estate": {"tables": ["raw_data", "cleaned_data"]}})
        di = DomainInitializer("real_estate", registry_path=reg)
        assert di.get_registered_tables() == ["raw_data", "cleaned_data"]

    def test_returns_empty_list_when_tables_is_empty(self, tmp_path):
        reg = _make_registry(tmp_path, {"sports_ticketing": {"tables": []}})
        di = DomainInitializer("sports_ticketing", registry_path=reg)
        assert di.get_registered_tables() == []


class TestGetAllDbTables:
    def test_returns_table_names_from_db(self, tmp_path):
        reg = _make_registry(tmp_path)
        di = DomainInitializer("sports_ticketing", registry_path=reg)
        conn = _mock_conn(["events", "tickets", "customers"])
        tables = di.get_all_db_tables(conn)
        assert tables == ["events", "tickets", "customers"]

    def test_returns_empty_when_no_tables(self, tmp_path):
        reg = _make_registry(tmp_path)
        di = DomainInitializer("sports_ticketing", registry_path=reg)
        conn = _mock_conn([])
        assert di.get_all_db_tables(conn) == []


class TestScoreTables:
    def test_domain_keyword_tables_score_higher(self, tmp_path):
        reg = _make_registry(tmp_path)
        di = DomainInitializer("sports_ticketing", registry_path=reg)
        scored = di.score_tables(["events", "tickets", "flyway_history", "customers"])
        names = [t for t, _ in scored]
        assert names.index("events") < names.index("flyway_history")
        assert names.index("tickets") < names.index("flyway_history")

    def test_unrelated_table_scores_zero(self, tmp_path):
        reg = _make_registry(tmp_path)
        di = DomainInitializer("sports_ticketing", registry_path=reg)
        scored = dict(di.score_tables(["flyway_schema_history"]))
        assert scored["flyway_schema_history"] == 0

    def test_returns_all_tables_sorted_by_score_desc(self, tmp_path):
        reg = _make_registry(tmp_path)
        di = DomainInitializer("sports_ticketing", registry_path=reg)
        scored = di.score_tables(["events", "flyway_history"])
        scores = [s for _, s in scored]
        assert scores == sorted(scores, reverse=True)


class TestRegisterTables:
    def test_creates_domain_entry_in_registry(self, tmp_path):
        reg = _make_registry(tmp_path)
        di = DomainInitializer("sports_ticketing", registry_path=reg)
        di.register_tables(["events", "tickets"])
        data = json.loads(reg.read_text())
        assert data["domains"]["sports_ticketing"]["tables"] == ["events", "tickets"]

    def test_updates_existing_tables_list(self, tmp_path):
        reg = _make_registry(tmp_path, {"sports_ticketing": {"tables": ["events"]}})
        di = DomainInitializer("sports_ticketing", registry_path=reg)
        di.register_tables(["events", "tickets", "customers"])
        data = json.loads(reg.read_text())
        assert data["domains"]["sports_ticketing"]["tables"] == ["events", "tickets", "customers"]

    def test_preserves_other_domain_entries(self, tmp_path):
        reg = _make_registry(tmp_path, {"real_estate": {"tables": ["raw_data"]}})
        di = DomainInitializer("sports_ticketing", registry_path=reg)
        di.register_tables(["events"])
        data = json.loads(reg.read_text())
        assert data["domains"]["real_estate"]["tables"] == ["raw_data"]


class TestDiffTables:
    def test_returns_tables_in_db_not_in_registry(self, tmp_path):
        reg = _make_registry(tmp_path, {"sports_ticketing": {"tables": ["events", "tickets"]}})
        di = DomainInitializer("sports_ticketing", registry_path=reg)
        conn = _mock_conn(["events", "tickets", "venues", "spell_corrections"])
        new = di.diff_tables(conn)
        assert "venues" in new
        assert "spell_corrections" not in new
        assert "events" not in new
        assert "tickets" not in new

    def test_returns_empty_when_no_new_tables(self, tmp_path):
        reg = _make_registry(tmp_path, {"sports_ticketing": {"tables": ["events"]}})
        di = DomainInitializer("sports_ticketing", registry_path=reg)
        conn = _mock_conn(["events", "column_metadata"])
        assert di.diff_tables(conn) == []

    def test_system_tables_never_in_diff(self, tmp_path):
        reg = _make_registry(tmp_path, {"sports_ticketing": {"tables": []}})
        di = DomainInitializer("sports_ticketing", registry_path=reg)
        conn = _mock_conn(list(SYSTEM_TABLES))
        assert di.diff_tables(conn) == []
