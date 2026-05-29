"""Tests for DomainInitializer — registry management, table scoring, diff.

Pure logic + a fake DB connection; no real DB or psycopg required.
"""

import json
from pathlib import Path

import pytest

from services.domain_initializer import DomainInitializer, SYSTEM_TABLES


# ── Fake DB connection ─────────────────────────────────────────────────────────

class _FakeCursor:
    """Cursor stub that returns preset rows. Supports context-manager use."""

    def __init__(self, rows):
        self._rows = rows

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False

    def execute(self, *args, **kwargs):
        pass

    def fetchall(self):
        return self._rows


class _FakePgConn:
    """Postgres-style connection: rows are dicts keyed by column name."""

    def __init__(self, table_names):
        self._rows = [{"table_name": t} for t in table_names]

    def cursor(self):
        return _FakeCursor(self._rows)


# ── Registry fixtures ────────────────────────────────────────────────────────────

@pytest.fixture
def registry_file(tmp_path):
    """A throwaway domain_registry.json with one rich entry (real_estate)."""
    path = tmp_path / "domain_registry.json"
    path.write_text(json.dumps({
        "active_domain": "real_estate",
        "domains": {
            "real_estate": {
                "label": "Real estate",
                "prompt_module": "prompts.domains.real_estate",
                "tables": ["raw_data", "cleaned_data"],
            }
        }
    }, indent=2))
    return path


def _read(path: Path) -> dict:
    return json.loads(path.read_text())


# ── register / get / unregister ──────────────────────────────────────────────────

class TestRegistry:
    def test_get_registered_tables_none_when_domain_absent(self, registry_file):
        di = DomainInitializer("sports_ticketing", registry_path=registry_file)
        assert di.get_registered_tables() is None

    def test_get_registered_tables_returns_existing(self, registry_file):
        di = DomainInitializer("real_estate", registry_path=registry_file)
        assert di.get_registered_tables() == ["raw_data", "cleaned_data"]

    def test_register_creates_new_domain_entry(self, registry_file):
        di = DomainInitializer("sports_ticketing", registry_path=registry_file)
        di.register_tables(["events", "tickets"])
        assert _read(registry_file)["domains"]["sports_ticketing"]["tables"] == ["events", "tickets"]

    def test_register_overwrites_existing(self, registry_file):
        di = DomainInitializer("real_estate", registry_path=registry_file)
        di.register_tables(["listings"])
        assert di.get_registered_tables() == ["listings"]

    def test_register_preserves_other_domains(self, registry_file):
        di = DomainInitializer("sports_ticketing", registry_path=registry_file)
        di.register_tables(["events"])
        assert "real_estate" in _read(registry_file)["domains"]


class TestUnregister:
    def test_unregister_removes_tables_only_from_rich_entry(self, registry_file):
        di = DomainInitializer("real_estate", registry_path=registry_file)
        assert di.unregister_tables() is True
        entry = _read(registry_file)["domains"]["real_estate"]
        assert "tables" not in entry
        # Rich keys survive — entry is not deleted.
        assert entry["prompt_module"] == "prompts.domains.real_estate"

    def test_unregister_removes_empty_entry_entirely(self, registry_file):
        di = DomainInitializer("sports_ticketing", registry_path=registry_file)
        di.register_tables(["events", "tickets"])
        assert di.unregister_tables() is True
        assert "sports_ticketing" not in _read(registry_file)["domains"]

    def test_unregister_returns_false_when_absent(self, registry_file):
        di = DomainInitializer("nonexistent", registry_path=registry_file)
        assert di.unregister_tables() is False

    def test_unregister_returns_false_when_no_tables_key(self, registry_file):
        # Domain entry exists but has no `tables` key.
        data = _read(registry_file)
        data["domains"]["bare"] = {"label": "no tables here"}
        registry_file.write_text(json.dumps(data))
        di = DomainInitializer("bare", registry_path=registry_file)
        assert di.unregister_tables() is False
        # Entry is kept because it still has other keys.
        assert "bare" in _read(registry_file)["domains"]


# ── table scoring ────────────────────────────────────────────────────────────────

class TestScoreTables:
    def test_domain_entity_tables_outrank_unrelated(self, registry_file):
        di = DomainInitializer("sports_ticketing", registry_path=registry_file)
        scored = di.score_tables(["events", "tickets", "audit_log", "kv_store"])
        ranked = [t for t, _ in scored]
        assert ranked.index("events") < ranked.index("kv_store")
        assert ranked.index("tickets") < ranked.index("kv_store")

    def test_plural_table_matches_singular_entity_word(self, registry_file):
        di = DomainInitializer("sports_ticketing", registry_path=registry_file)
        scored = dict(di.score_tables(["tickets", "random"]))
        # "ticket" is an entity word; "tickets" must score via singular fold.
        assert scored["tickets"] > scored["random"]

    def test_domain_name_tokens_contribute_to_score(self, registry_file):
        # Even without a curated word list, the domain name tokens are matched.
        di = DomainInitializer("medical_billing", registry_path=registry_file)
        scored = dict(di.score_tables(["billing", "unrelated"]))
        assert scored["billing"] > scored["unrelated"]


# ── DB-backed table discovery / diff ──────────────────────────────────────────────

class TestTableDiscovery:
    def test_get_all_db_tables_postgres_path(self, registry_file):
        di = DomainInitializer("sports_ticketing", registry_path=registry_file)
        conn = _FakePgConn(["events", "tickets", "column_metadata"])
        assert di.get_all_db_tables(conn) == ["events", "tickets", "column_metadata"]

    def test_diff_excludes_system_and_registered(self, registry_file):
        di = DomainInitializer("sports_ticketing", registry_path=registry_file)
        di.register_tables(["events"])
        conn = _FakePgConn(["events", "tickets", "venues", "column_metadata", "spell_corrections"])
        diff = di.diff_tables(conn)
        assert diff == ["tickets", "venues"]
        # sanity: system tables really were filtered
        assert not (set(diff) & SYSTEM_TABLES)

    def test_diff_empty_when_all_known(self, registry_file):
        di = DomainInitializer("sports_ticketing", registry_path=registry_file)
        di.register_tables(["events", "tickets"])
        conn = _FakePgConn(["events", "tickets", "plan_cache"])
        assert di.diff_tables(conn) == []
