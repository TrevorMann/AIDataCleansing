# Domain Initialization Flow — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use `superpowers:subagent-driven-development` (recommended) or `superpowers:executing-plans` to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.
>
> **Note:** User has requested no git commits during this session. Skip all commit steps.

**Goal:** Build a unified `initialize_domain.py` orchestrator that walks through 4 phases (table registration → schema discovery → annotation → seed research), with an `add_table` subcommand for schema evolution and `--refresh-seeds` for re-running seed generation.

**Architecture:** A thin orchestrator script coordinates 4 sequential phases with printed progress headers and pause points. A new `DomainInitializer` service owns Phase 0 (table discovery, keyword scoring, registry diff/write). `MetadataAnnotationService` is fixed to require explicit table lists (hardcoded tables removed). `DomainResearcher` gains a `research_with_schema()` method that grounds the LLM in actual schema + annotations + sampled data.

**Tech Stack:** Python 3.12, psycopg3 (dict_row), pytest, unittest.mock, pathlib, json, yaml

---

## File Map

### Created
| File | Purpose |
|------|---------|
| `scripts/initialize_domain.py` | Orchestrator: phases 0-3, `add_table` subcommand, `--refresh-seeds` |
| `services/domain_initializer.py` | Phase 0: table discovery, keyword scoring, system-table filter, registry diff/write |
| `tests/services/test_domain_initializer.py` | Tests for DomainInitializer |
| `tests/scripts/test_initialize_domain.py` | Tests for phase helpers |

### Modified
| File | Change |
|------|--------|
| `data/domain_registry.json` | Add `"tables"` to `real_estate` entry |
| `services/metadata_annotation.py` | Remove `DEFAULT_TABLES`; `tables` param becomes required |
| `scripts/annotate_domain.py` | Load tables from registry; fail clearly if unregistered |
| `tests/test_metadata_annotation.py` | Update CLI tests to mock `DomainInitializer` |
| `seeders/domain_researcher.py` | Add `research_with_schema()` + `get_filtered_questions()` |
| `tests/test_research_domain.py` | Add tests for new methods |

---

## Task 1: Seed `tables` field in domain_registry.json

**Files:**
- Modify: `data/domain_registry.json`

- [ ] **Step 1: Add `tables` to real_estate and stub sports_ticketing**

Open `data/domain_registry.json` and replace its contents with:

```json
{
  "_note": "Tracks which domains are initialized. Updated automatically by 'scripts/domain.py scaffold'. Edit directly to switch active_domain.",
  "active_domain": "real_estate",
  "domains": {
    "real_estate": {
      "initialized_at": "2026-05-05",
      "label": "Real estate listings — addresses, postal codes, neighbourhoods",
      "sub_category_dimension": "country",
      "sub_categories": ["CA", "USA", "NL", "MX", "JP"],
      "prompt_module": "prompts.domains.real_estate",
      "skills_path": "skills/real_estate/skills.yaml",
      "seeders_path": "seeders/real_estate/manifest.yaml",
      "tables": ["raw_data", "cleaned_data"]
    }
  }
}
```

Note: `sports_ticketing` is intentionally absent — Phase 0 will create it interactively.

- [ ] **Step 2: Verify JSON is valid**

```bash
python -c "import json; json.load(open('data/domain_registry.json')); print('OK')"
```
Expected: `OK`

---

## Task 2: Build `DomainInitializer` service

**Files:**
- Create: `services/domain_initializer.py`
- Create: `tests/services/__init__.py`
- Create: `tests/services/test_domain_initializer.py`

- [ ] **Step 1: Write failing tests**

Create `tests/services/__init__.py` (empty).

Create `tests/services/test_domain_initializer.py`:

```python
"""Tests for DomainInitializer — table discovery, scoring, registry management."""
import json
import tempfile
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
        # events and tickets should rank above flyway_history
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
        # venues is new; spell_corrections is a system table → excluded
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
```

- [ ] **Step 2: Run tests to verify they fail**

```bash
cd /mnt/f/AI_learning_project && python -m pytest tests/services/test_domain_initializer.py -v 2>&1 | head -30
```
Expected: `ImportError` or `ModuleNotFoundError` for `services.domain_initializer`

- [ ] **Step 3: Implement `services/domain_initializer.py`**

```python
"""Phase 0 domain initialization — table discovery, keyword scoring, registry management."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Optional

SYSTEM_TABLES: frozenset[str] = frozenset({
    "column_metadata",
    "spell_corrections",
    "query_pattern_memory",
    "plan_cache",
    "municipality_lookup_cache",
    "source_registry",
})

# Per-domain entity keywords for scoring candidate tables when DB has > 15 tables.
DOMAIN_ENTITY_WORDS: dict[str, list[str]] = {
    "sports_ticketing": [
        "event", "ticket", "customer", "fan", "venue", "team",
        "seat", "section", "purchase", "account", "booking",
        "price", "sport", "game", "match", "player",
    ],
    "real_estate": [
        "property", "listing", "address", "postal", "municipality",
        "province", "city", "agent", "broker", "price", "mls",
    ],
    "_generic": ["record", "data", "entry", "item", "entity", "profile"],
}

_DEFAULT_REGISTRY_PATH = Path(__file__).parent.parent / "data" / "domain_registry.json"


class DomainInitializer:
    """Manages domain table registration in domain_registry.json."""

    def __init__(self, domain: str, registry_path: Optional[Path] = None):
        self.domain = domain
        self.registry_path = registry_path or _DEFAULT_REGISTRY_PATH

    # ── Registry read/write ────────────────────────────────────────────────────

    def _load(self) -> dict:
        with self.registry_path.open() as f:
            return json.load(f)

    def _save(self, data: dict) -> None:
        with self.registry_path.open("w") as f:
            json.dump(data, f, indent=2)

    def get_registered_tables(self) -> Optional[list[str]]:
        """Return registered tables for this domain, or None if not registered."""
        data = self._load()
        domain_entry = data.get("domains", {}).get(self.domain)
        if domain_entry is None:
            return None
        return domain_entry.get("tables")  # None if key absent, [] if empty

    def register_tables(self, tables: list[str]) -> None:
        """Write (or overwrite) the tables list for this domain in the registry."""
        data = self._load()
        data.setdefault("domains", {}).setdefault(self.domain, {})["tables"] = tables
        self._save(data)

    # ── DB introspection ───────────────────────────────────────────────────────

    def get_all_db_tables(self, conn) -> list[str]:
        """Return all user tables currently in the DB (both backends)."""
        try:
            # PostgreSQL path
            with conn.cursor() as cur:
                cur.execute(
                    """
                    SELECT table_name
                    FROM information_schema.tables
                    WHERE table_schema = 'public' AND table_type = 'BASE TABLE'
                    ORDER BY table_name
                    """
                )
                return [row["table_name"] for row in cur.fetchall()]
        except Exception:
            # SQLite fallback
            with conn.cursor() as cur:
                cur.execute(
                    "SELECT name FROM sqlite_master WHERE type='table' ORDER BY name"
                )
                return [row[0] for row in cur.fetchall()]

    def diff_tables(self, conn) -> list[str]:
        """Return tables in DB that are not registered and not system tables."""
        registered = set(self.get_registered_tables() or [])
        all_tables = set(self.get_all_db_tables(conn))
        return sorted(all_tables - registered - SYSTEM_TABLES)

    # ── Keyword scoring ────────────────────────────────────────────────────────

    def score_tables(self, tables: list[str]) -> list[tuple[str, int]]:
        """Score tables by keyword overlap with domain entity words. Descending order."""
        words = set(
            DOMAIN_ENTITY_WORDS.get(self.domain, [])
            + DOMAIN_ENTITY_WORDS["_generic"]
            + self.domain.replace("_", " ").split()
        )
        result = []
        for table in tables:
            tokens = set(table.replace("_", " ").split())
            score = len(tokens & words)
            result.append((table, score))
        return sorted(result, key=lambda x: -x[1])
```

- [ ] **Step 4: Run tests to verify they pass**

```bash
cd /mnt/f/AI_learning_project && python -m pytest tests/services/test_domain_initializer.py -v
```
Expected: all green

---

## Task 3: Fix `MetadataAnnotationService` — drop `DEFAULT_TABLES`

**Files:**
- Modify: `services/metadata_annotation.py`
- Modify: `tests/test_metadata_annotation.py`

- [ ] **Step 1: Confirm existing tests still reference `tables=` kwarg**

```bash
grep -n "tables=" tests/test_metadata_annotation.py
```
Expected: several hits — the tests already pass `tables=` explicitly. Only the CLI tests need updating.

- [ ] **Step 2: Remove `DEFAULT_TABLES` and make `tables` required**

In `services/metadata_annotation.py`, make these exact changes:

Remove the class attribute:
```python
# DELETE this line:
DEFAULT_TABLES = ["raw_data", "cleaned_data"]
```

Change `list_gaps` signature from:
```python
def list_gaps(self, domain: str, conn, tables: list[str] = None) -> list[dict]:
    tables = tables or self.DEFAULT_TABLES
```
to:
```python
def list_gaps(self, domain: str, conn, tables: list[str]) -> list[dict]:
```

Change `run` signature from:
```python
def run(
    self,
    domain: str,
    conn,
    force: bool = False,
    tables: list[str] = None,
) -> AnnotationReport:
    ...
    tables = tables or self.DEFAULT_TABLES
```
to:
```python
def run(
    self,
    domain: str,
    conn,
    tables: list[str],
    force: bool = False,
) -> AnnotationReport:
```

Note: `tables` moves before `force` and loses its default. Update all callers.

- [ ] **Step 3: Update CLI tests to mock `DomainInitializer`**

In `tests/test_metadata_annotation.py`, update both CLI tests:

```python
def test_cli_dry_run_prints_gaps(capsys):
    """Dry run prints gaps without writing to DB."""
    import scripts.annotate_domain as cli_module

    with patch.object(cli_module, "get_db_connection", return_value=MagicMock()), \
         patch("scripts.annotate_domain.DomainInitializer") as mock_di, \
         patch("services.metadata_annotation.MetadataAnnotationService.list_gaps",
               return_value=[{"table_name": "raw_data", "column_name": "city"}]), \
         patch("sys.argv", ["annotate_domain.py", "--domain", "real_estate", "--dry-run"]):
        mock_di.return_value.get_registered_tables.return_value = ["raw_data", "cleaned_data"]
        cli_module.main()

    captured = capsys.readouterr()
    assert "raw_data.city" in captured.out


def test_cli_dry_run_no_gaps_message(capsys):
    """Dry run prints no-gaps message when all columns annotated."""
    import scripts.annotate_domain as cli_module

    with patch.object(cli_module, "get_db_connection", return_value=MagicMock()), \
         patch("scripts.annotate_domain.DomainInitializer") as mock_di, \
         patch("services.metadata_annotation.MetadataAnnotationService.list_gaps",
               return_value=[]), \
         patch("sys.argv", ["annotate_domain.py", "--domain", "real_estate", "--dry-run"]):
        mock_di.return_value.get_registered_tables.return_value = ["raw_data", "cleaned_data"]
        cli_module.main()

    captured = capsys.readouterr()
    assert "No annotation gaps" in captured.out
```

Add one new test below those two:

```python
def test_cli_exits_when_domain_not_registered(capsys):
    """annotate_domain.py exits with clear message if domain not registered."""
    import scripts.annotate_domain as cli_module

    with patch.object(cli_module, "get_db_connection", return_value=MagicMock()), \
         patch("scripts.annotate_domain.DomainInitializer") as mock_di, \
         patch("sys.argv", ["annotate_domain.py", "--domain", "unregistered", "--dry-run"]):
        mock_di.return_value.get_registered_tables.return_value = None
        with pytest.raises(SystemExit):
            cli_module.main()

    captured = capsys.readouterr()
    assert "not registered" in captured.out or "not registered" in captured.err
```

- [ ] **Step 4: Run tests to confirm they fail before updating `annotate_domain.py`**

```bash
cd /mnt/f/AI_learning_project && python -m pytest tests/test_metadata_annotation.py -v -k "cli" 2>&1 | tail -20
```
Expected: failures on the 3 CLI tests

- [ ] **Step 5: Update `scripts/annotate_domain.py`**

Replace the entire file:

```python
#!/usr/bin/env python3
"""CLI: generate LLM annotations for domain columns in column_metadata."""

import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from db.pg_init import get_db_connection
from services.domain_initializer import DomainInitializer
from services.metadata_annotation import MetadataAnnotationService


def _build_llm_client():
    from cleaning.llm_client import build_clients
    return build_clients().fast


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Annotate domain columns with LLM-generated descriptions."
    )
    parser.add_argument("--domain", required=True, help="Domain name (e.g. real_estate)")
    parser.add_argument("--force", action="store_true",
                        help="Overwrite existing LLM-generated annotations")
    parser.add_argument("--dry-run", action="store_true",
                        help="Show unannotated columns without writing")
    args = parser.parse_args()

    # Load registered tables — fail clearly if domain not initialized yet
    initializer = DomainInitializer(args.domain)
    tables = initializer.get_registered_tables()
    if not tables:
        print(
            f"Domain '{args.domain}' not registered or has no tables. "
            f"Run: python scripts/initialize_domain.py --domain {args.domain}"
        )
        sys.exit(1)

    conn = get_db_connection("")

    if args.dry_run:
        svc = MetadataAnnotationService(llm_client=None)
        gaps = svc.list_gaps(args.domain, conn, tables=tables)
        if not gaps:
            print(f"No annotation gaps found for domain '{args.domain}'.")
            return
        print(f"Annotation gaps for '{args.domain}' ({len(gaps)} columns):")
        for g in gaps:
            print(f"  {g['table_name']}.{g['column_name']}")
        return

    svc = MetadataAnnotationService(llm_client=_build_llm_client())
    print(f"Annotating {args.domain}...")
    report = svc.run(args.domain, conn, tables=tables, force=args.force)

    print(f"\nDone: {report.annotated} annotated, {report.skipped} skipped", end="")
    if report.low_confidence:
        print(f", {len(report.low_confidence)} low-confidence")
        print("\nLow-confidence columns (review recommended):")
        for lc in report.low_confidence:
            print(f"  {lc['table_name']}.{lc['column_name']}  confidence={lc['confidence']:.2f}")
    else:
        print()


if __name__ == "__main__":
    main()
```

- [ ] **Step 6: Run all metadata annotation tests**

```bash
cd /mnt/f/AI_learning_project && python -m pytest tests/test_metadata_annotation.py -v
```
Expected: all green

---

## Task 4: Build `initialize_domain.py` — skeleton, helpers, Phase 0 + Phase 1

**Files:**
- Create: `scripts/initialize_domain.py`
- Create: `tests/scripts/__init__.py`
- Create: `tests/scripts/test_initialize_domain.py`

- [ ] **Step 1: Write failing tests for Phase 0 and Phase 1 helpers**

Create `tests/scripts/__init__.py` (empty).

Create `tests/scripts/test_initialize_domain.py`:

```python
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

        tables = phase0_register_tables(di, conn)
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
            tables = phase0_register_tables(di, conn)

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
```

- [ ] **Step 2: Run tests to verify they fail**

```bash
cd /mnt/f/AI_learning_project && python -m pytest tests/scripts/test_initialize_domain.py -v 2>&1 | head -20
```
Expected: `ImportError` — `scripts.initialize_domain` does not exist yet

- [ ] **Step 3: Implement `scripts/initialize_domain.py` — skeleton + Phase 0 + Phase 1**

```python
#!/usr/bin/env python3
"""
Unified domain initialization orchestrator.

Usage:
  python scripts/initialize_domain.py --domain sports_ticketing
  python scripts/initialize_domain.py --domain sports_ticketing add_table
  python scripts/initialize_domain.py --domain sports_ticketing --refresh-seeds
"""

import argparse
import sys
from pathlib import Path
from typing import Optional

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from db.connection import get_connection, get_pg_dsn, get_backend
from services.domain_initializer import DomainInitializer, SYSTEM_TABLES


# ── Progress output helpers ───────────────────────────────────────────────────

BORDER = "═" * 50

def _phase_header(n: int, name: str) -> None:
    print(f"\n{BORDER}")
    print(f"  Phase {n} — {name}")
    print(f"{BORDER}")

def _section_header(name: str) -> None:
    print(f"\n{BORDER}")
    print(f"  {name}")
    print(f"{BORDER}")

def _pause(prompt: str, default_yes: bool = True) -> bool:
    suffix = "[Y/n]" if default_yes else "[y/N]"
    ans = input(f"\n{prompt} {suffix}: ").strip().lower()
    if not ans:
        return default_yes
    return ans.startswith("y")


# ── Phase 0: Table registration ───────────────────────────────────────────────

def phase0_register_tables(initializer: DomainInitializer, conn) -> list[str]:
    _phase_header(0, "Table Registration")

    existing = initializer.get_registered_tables()
    if existing:
        print(f"Using registered tables: {', '.join(existing)}")
        return existing

    all_tables = initializer.get_all_db_tables(conn)
    non_system = [t for t in all_tables if t not in SYSTEM_TABLES]

    # For large DBs: score and present top candidates
    if len(non_system) > 15:
        print(f"Found {len(all_tables)} tables. Showing top candidates for '{initializer.domain}'...\n")
        scored = initializer.score_tables(non_system)
        candidates = [t for t, _ in scored[:10]]
    else:
        candidates = non_system

    print(f"Found {len(all_tables)} tables in database.\n")
    print(f"Select tables that belong to '{initializer.domain}':")

    for i, table in enumerate(candidates, 1):
        print(f"  [{i}] {table}")

    # Show system tables at end as informational
    for table in all_tables:
        if table in SYSTEM_TABLES:
            print(f"  [ ] {table:<30}  ← system table, likely skip")

    raw = input("\nEnter numbers (comma-separated): ").strip()
    if not raw:
        print("No tables selected. Exiting.")
        sys.exit(0)

    try:
        indices = [int(x.strip()) - 1 for x in raw.split(",")]
        selected = [candidates[i] for i in indices if 0 <= i < len(candidates)]
    except (ValueError, IndexError):
        print("Invalid selection. Exiting.")
        sys.exit(1)

    if not selected:
        print("No valid tables selected. Exiting.")
        sys.exit(0)

    initializer.register_tables(selected)
    print(f"\nRegistered: {', '.join(selected)} → domain_registry.json")
    return selected


# ── Phase 1: Schema discovery ─────────────────────────────────────────────────

def _get_table_schema(table: str, conn) -> list[dict]:
    """Query column metadata for a single table. Works on both backends."""
    backend = get_backend()
    if backend == "postgres":
        with conn.cursor() as cur:
            cur.execute(
                """
                SELECT
                    c.column_name   AS name,
                    c.data_type     AS type,
                    (c.is_nullable = 'NO') AS notnull,
                    EXISTS (
                        SELECT 1
                        FROM information_schema.table_constraints tc
                        JOIN information_schema.key_column_usage kcu
                          ON tc.constraint_name = kcu.constraint_name
                         AND tc.table_schema    = kcu.table_schema
                        WHERE tc.constraint_type = 'PRIMARY KEY'
                          AND tc.table_schema    = c.table_schema
                          AND tc.table_name      = c.table_name
                          AND kcu.column_name    = c.column_name
                    ) AS pk
                FROM information_schema.columns c
                WHERE c.table_schema = 'public' AND c.table_name = %s
                ORDER BY c.ordinal_position
                """,
                (table,),
            )
            return [dict(row) for row in cur.fetchall()]
    else:  # SQLite
        with conn.cursor() as cur:
            cur.execute(f"PRAGMA table_info({table})")
            return [
                {"name": r[1], "type": r[2], "notnull": bool(r[3]), "pk": bool(r[5])}
                for r in cur.fetchall()
            ]


def phase1_schema_discovery(tables: list[str], conn) -> dict[str, list[dict]]:
    _phase_header(1, "Schema Discovery")
    print(f"Scanning {len(tables)} tables...\n")

    schema: dict[str, list[dict]] = {}
    for table in tables:
        columns = _get_table_schema(table, conn)
        schema[table] = columns
        print(f"  {table} ({len(columns)} columns)")
        for col in columns:
            pk_flag = "  PK" if col.get("pk") else ""
            nn_flag = "  NOT NULL" if col.get("notnull") else ""
            print(f"    {col['name']:<28} {col['type']:<20}{nn_flag}{pk_flag}")

    return schema


# ── main ──────────────────────────────────────────────────────────────────────

def main() -> None:
    parser = argparse.ArgumentParser(
        description="Initialize a domain: register tables, annotate schema, generate seeds."
    )
    parser.add_argument("--domain", required=True, help="Domain name (e.g. sports_ticketing)")
    parser.add_argument("--force", action="store_true",
                        help="Re-annotate columns that already have annotations")
    parser.add_argument("--refresh-seeds", action="store_true",
                        help="Re-run Phase 3 seed research only (skip phases 0-2)")
    parser.add_argument("subcommand", nargs="?", choices=["add_table"],
                        help="add_table: register new DB tables added since last init")
    args = parser.parse_args()

    conn = get_connection(get_pg_dsn())
    initializer = DomainInitializer(args.domain)

    if args.subcommand == "add_table":
        cmd_add_table(args.domain, initializer, conn)
        return

    if args.refresh_seeds:
        cmd_refresh_seeds(args.domain, initializer, conn)
        return

    # Full initialization flow
    tables = phase0_register_tables(initializer, conn)

    if not _pause("Schema loaded. Continue to annotation?"):
        print("Stopped after Phase 0.")
        return

    schema = phase1_schema_discovery(tables, conn)

    if not _pause("Schema discovered. Continue to annotation?"):
        print("Stopped after Phase 1.")
        return

    phase2_annotation(args.domain, tables, conn, force=args.force)

    if not _pause("Annotation complete. Continue to seed research?"):
        print("Stopped after Phase 2.")
        return

    phase3_seed_research(args.domain, schema, conn)

    _section_header(f"Domain '{args.domain}' initialization complete.")


def cmd_add_table(domain: str, initializer: DomainInitializer, conn) -> None:
    pass  # implemented in Task 9


def cmd_refresh_seeds(domain: str, initializer: DomainInitializer, conn) -> None:
    pass  # implemented in Task 10


def phase2_annotation(domain: str, tables: list[str], conn, force: bool = False) -> None:
    pass  # implemented in Task 5


def phase3_seed_research(domain: str, schema: dict, conn) -> None:
    pass  # implemented in Task 8


if __name__ == "__main__":
    main()
```

- [ ] **Step 4: Run tests to verify they pass**

```bash
cd /mnt/f/AI_learning_project && python -m pytest tests/scripts/test_initialize_domain.py -v
```
Expected: all green

---

## Task 5: Phase 2 — annotation wired into orchestrator

**Files:**
- Modify: `scripts/initialize_domain.py` — implement `phase2_annotation()`
- Modify: `tests/scripts/test_initialize_domain.py` — add Phase 2 tests

- [ ] **Step 1: Add Phase 2 tests**

Append to `tests/scripts/test_initialize_domain.py`:

```python
class TestPhase2:
    def test_calls_annotation_service_with_declared_tables(self, tmp_path):
        from scripts.initialize_domain import phase2_annotation

        conn = MagicMock()
        with patch("scripts.initialize_domain.MetadataAnnotationService") as mock_svc_cls, \
             patch("scripts.initialize_domain._build_llm_client", return_value=MagicMock()):
            mock_svc = mock_svc_cls.return_value
            mock_svc.run.return_value = MagicMock(annotated=3, skipped=0, low_confidence=[])
            phase2_annotation("sports_ticketing", ["events", "tickets"], conn)

        mock_svc.run.assert_called_once()
        call_kwargs = mock_svc.run.call_args
        assert call_kwargs[0][0] == "sports_ticketing"
        assert call_kwargs[1]["tables"] == ["events", "tickets"]

    def test_prints_low_confidence_warning(self, capsys):
        from scripts.initialize_domain import phase2_annotation

        conn = MagicMock()
        with patch("scripts.initialize_domain.MetadataAnnotationService") as mock_svc_cls, \
             patch("scripts.initialize_domain._build_llm_client", return_value=MagicMock()):
            mock_svc = mock_svc_cls.return_value
            mock_svc.run.return_value = MagicMock(
                annotated=2,
                skipped=0,
                low_confidence=[{"table_name": "customers", "column_name": "postal_code", "confidence": 0.55}],
            )
            phase2_annotation("sports_ticketing", ["customers"], conn)

        out = capsys.readouterr().out
        assert "low confidence" in out.lower() or "⚠" in out
        assert "postal_code" in out

    def test_prints_per_column_progress(self, capsys):
        from scripts.initialize_domain import phase2_annotation

        conn = MagicMock()
        # Patch the service to call a side effect that prints progress
        with patch("scripts.initialize_domain.MetadataAnnotationService") as mock_svc_cls, \
             patch("scripts.initialize_domain._build_llm_client", return_value=MagicMock()):
            mock_svc = mock_svc_cls.return_value
            mock_svc.run.return_value = MagicMock(annotated=1, skipped=0, low_confidence=[])
            phase2_annotation("sports_ticketing", ["events"], conn)

        # Phase header should be printed
        out = capsys.readouterr().out
        assert "Phase 2" in out or "Annotation" in out
```

- [ ] **Step 2: Run tests to verify they fail**

```bash
cd /mnt/f/AI_learning_project && python -m pytest tests/scripts/test_initialize_domain.py::TestPhase2 -v 2>&1 | tail -15
```
Expected: failures (phase2_annotation is a no-op stub)

- [ ] **Step 3: Implement `phase2_annotation()` in `scripts/initialize_domain.py`**

Replace the stub `phase2_annotation` and add the needed import at the top of the file:

At top of file, add import:
```python
from services.metadata_annotation import MetadataAnnotationService
```

Add helper (before `main`):
```python
def _build_llm_client():
    from cleaning.llm_client import build_clients
    return build_clients().fast
```

Replace the stub:
```python
def phase2_annotation(domain: str, tables: list[str], conn, force: bool = False) -> None:
    _phase_header(2, "Annotation")
    print(f"Annotating columns across {len(tables)} table(s)...\n")

    llm = _build_llm_client()
    svc = MetadataAnnotationService(llm_client=llm)

    # Wrap run() to print per-column progress by monkey-patching _annotate_column
    original_annotate = svc._annotate_column

    def _annotating_with_progress(d, dd, table, column, conn_inner):
        print(f"  {table}.{column:<30} ... ", end="", flush=True)
        result = original_annotate(d, dd, table, column, conn_inner)
        conf = result.get("confidence", 0)
        marker = "⚠ low confidence" if conf < 0.70 else "done"
        print(f"{marker} (confidence={conf:.2f})")
        return result

    svc._annotate_column = _annotating_with_progress

    report = svc.run(domain, conn, tables=tables, force=force)

    print(f"\nDone: {report.annotated} annotated, {report.skipped} skipped", end="")
    if report.low_confidence:
        print(f", {len(report.low_confidence)} low-confidence")
        print("\nLow-confidence columns (review recommended):")
        for lc in report.low_confidence:
            print(f"  {lc['table_name']}.{lc['column_name']}  confidence={lc['confidence']:.2f}")
    else:
        print()
```

- [ ] **Step 4: Run tests**

```bash
cd /mnt/f/AI_learning_project && python -m pytest tests/scripts/test_initialize_domain.py::TestPhase2 -v
```
Expected: all green

---

## Task 6: `DomainResearcher.research_with_schema()` + schema-filtered Q&A

**Files:**
- Modify: `seeders/domain_researcher.py`
- Modify: `tests/test_research_domain.py`

- [ ] **Step 1: Write failing tests**

Append to `tests/test_research_domain.py`:

```python
import json as _json

_SCHEMA = {
    "events": [
        {"name": "event_id",       "type": "uuid",         "notnull": True,  "pk": True},
        {"name": "event_name",     "type": "text",         "notnull": False, "pk": False},
        {"name": "home_team",      "type": "text",         "notnull": False, "pk": False},
        {"name": "start_datetime", "type": "timestamptz",  "notnull": False, "pk": False},
    ],
    "customers": [
        {"name": "customer_id",    "type": "uuid",         "notnull": True,  "pk": True},
        {"name": "postal_code",    "type": "text",         "notnull": False, "pk": False},
        {"name": "city",           "type": "text",         "notnull": False, "pk": False},
    ],
}
_ANNOTATIONS = {
    "events.event_name":     "Name of the sports event",
    "events.home_team":      "Home team name",
    "customers.postal_code": "Customer postal/zip code",
}
_SAMPLES = {
    "events.home_team":      ["Leafs", "Raptors", "Blue Jays"],
    "events.event_name":     ["Leafs vs Sens", "Raptors vs Celtics"],
    "customers.postal_code": [],
}


class TestGetFilteredQuestions:
    def test_always_includes_gap_types_question(self):
        r = DomainResearcher(domain="sports_ticketing")
        questions = r.get_filtered_questions(_SCHEMA)
        keys = {q.key for q in questions}
        assert "gap_types" in keys

    def test_always_includes_trusted_sources_question(self):
        r = DomainResearcher(domain="sports_ticketing")
        questions = r.get_filtered_questions(_SCHEMA)
        keys = {q.key for q in questions}
        assert "trusted_sources" in keys

    def test_includes_team_aliases_when_team_column_exists(self):
        r = DomainResearcher(domain="sports_ticketing")
        questions = r.get_filtered_questions(_SCHEMA)
        keys = {q.key for q in questions}
        assert "team_aliases" in keys

    def test_includes_postal_format_when_postal_column_exists(self):
        r = DomainResearcher(domain="sports_ticketing")
        questions = r.get_filtered_questions(_SCHEMA)
        keys = {q.key for q in questions}
        assert "postal_format" in keys

    def test_includes_datetime_format_when_timestamp_column_exists(self):
        r = DomainResearcher(domain="sports_ticketing")
        questions = r.get_filtered_questions(_SCHEMA)
        keys = {q.key for q in questions}
        assert "datetime_format" in keys

    def test_skips_postal_when_no_postal_column(self):
        r = DomainResearcher(domain="sports_ticketing")
        schema_no_postal = {
            "events": [{"name": "event_name", "type": "text", "notnull": False, "pk": False}]
        }
        questions = r.get_filtered_questions(schema_no_postal)
        keys = {q.key for q in questions}
        assert "postal_format" not in keys

    def test_skips_team_aliases_when_no_team_column(self):
        r = DomainResearcher(domain="sports_ticketing")
        schema_no_team = {
            "customers": [{"name": "email", "type": "text", "notnull": False, "pk": False}]
        }
        questions = r.get_filtered_questions(schema_no_team)
        keys = {q.key for q in questions}
        assert "team_aliases" not in keys


class TestBuildSchemaPrompt:
    def test_prompt_includes_schema_summary(self):
        r = DomainResearcher(domain="sports_ticketing")
        prompt = r.build_schema_prompt(_SCHEMA, _ANNOTATIONS, _SAMPLES, {})
        assert "events" in prompt
        assert "home_team" in prompt

    def test_prompt_includes_annotation_descriptions(self):
        r = DomainResearcher(domain="sports_ticketing")
        prompt = r.build_schema_prompt(_SCHEMA, _ANNOTATIONS, _SAMPLES, {})
        assert "Name of the sports event" in prompt

    def test_prompt_includes_data_samples(self):
        r = DomainResearcher(domain="sports_ticketing")
        prompt = r.build_schema_prompt(_SCHEMA, _ANNOTATIONS, _SAMPLES, {})
        assert "Leafs" in prompt or "Raptors" in prompt

    def test_prompt_notes_empty_sample_columns(self):
        r = DomainResearcher(domain="sports_ticketing")
        prompt = r.build_schema_prompt(_SCHEMA, _ANNOTATIONS, _SAMPLES, {})
        # postal_code has 0 samples — should be noted
        assert "postal_code" in prompt


class TestResearchWithSchema:
    def test_returns_research_bundle(self):
        r = DomainResearcher(domain="sports_ticketing")
        answers = {
            "gap_types": "unknown_team, unknown_venue",
            "trusted_sources": "nhl.com, ticketmaster.com",
            "industry_context": "",
        }
        mock_client = MagicMock()
        mock_response = MagicMock()
        mock_response.content = [MagicMock(text=_VALID_LLM_RESPONSE)]
        mock_client.messages.create.return_value = mock_response

        bundle = r.research_with_schema(
            answers=answers,
            schema=_SCHEMA,
            annotations=_ANNOTATIONS,
            data_samples=_SAMPLES,
            llm_client=mock_client,
            model="claude-test",
        )
        assert isinstance(bundle, ResearchBundle)
        mock_client.messages.create.assert_called_once()

    def test_schema_context_appears_in_llm_prompt(self):
        r = DomainResearcher(domain="sports_ticketing")
        answers = {"gap_types": "x", "trusted_sources": "y", "industry_context": ""}
        mock_client = MagicMock()
        mock_response = MagicMock()
        mock_response.content = [MagicMock(text=_VALID_LLM_RESPONSE)]
        mock_client.messages.create.return_value = mock_response

        r.research_with_schema(
            answers=answers, schema=_SCHEMA, annotations=_ANNOTATIONS,
            data_samples=_SAMPLES, llm_client=mock_client, model="test",
        )
        call_args = mock_client.messages.create.call_args
        messages = call_args[1].get("messages") or call_args[0][1]
        content = str(messages)
        assert "home_team" in content or "events" in content
```

- [ ] **Step 2: Run tests to verify they fail**

```bash
cd /mnt/f/AI_learning_project && python -m pytest tests/test_research_domain.py -k "Schema or FilteredQ or ResearchWithSchema or SchemaPrompt" -v 2>&1 | tail -20
```
Expected: `AttributeError` — `get_filtered_questions`, `build_schema_prompt`, `research_with_schema` don't exist yet

- [ ] **Step 3: Implement new methods in `seeders/domain_researcher.py`**

Add these imports at the top of `seeders/domain_researcher.py` (if not already present):
```python
import textwrap
```

Add these constants after `_QUESTIONS`:

```python
# ── schema-filtered question pool ─────────────────────────────────────────────

_Q_ENTITY_DESCRIPTION = Question(
    key="entity_description",
    prompt="What kind of records does this domain clean?",
    hint="e.g. sports event tickets, customer profiles, purchase transactions",
)
_Q_GAP_TYPES = Question(
    key="gap_types",
    prompt="What data quality gaps typically require a web search to resolve?",
    hint="e.g. unknown_team, unknown_venue, event_time_mismatch",
)
_Q_TRUSTED_SOURCES = Question(
    key="trusted_sources",
    prompt="Which authoritative websites should be searched for this domain? (comma-separated)",
    hint="e.g. nhl.com, nba.com, ticketmaster.com, wikipedia.org",
)
_Q_INDUSTRY_CONTEXT = Question(
    key="industry_context",
    prompt="Any additional domain-specific context the LLM should know?",
    hint="e.g. team name abbreviations, venue aliases — leave blank to skip",
)
_Q_TEXT_FIELDS = Question(
    key="text_fields",
    prompt="Which text fields most commonly have spelling or capitalization errors?",
    hint="already detected from schema — add any context about error patterns",
)
_Q_TEAM_ALIASES = Question(
    key="team_aliases",
    prompt="What team name aliases and abbreviations are common in this domain?",
    hint="e.g. Leafs=Toronto Maple Leafs, Habs=Montreal Canadiens, Sens=Ottawa Senators",
)
_Q_POSTAL_FORMAT = Question(
    key="postal_format",
    prompt="What postal/zip code formats are used, and which countries?",
    hint="e.g. Canadian FSA (A1A 1A1), US ZIP (12345), both",
)
_Q_DATETIME_FORMAT = Question(
    key="datetime_format",
    prompt="What timezone context applies to date/time columns?",
    hint="e.g. all times in ET, mixed timezones, UTC stored locally converted",
)

_TEXT_TYPES = frozenset({"text", "character varying", "varchar", "char", "character"})
_TIMESTAMP_TYPES = frozenset({
    "timestamp", "timestamptz", "timestamp with time zone",
    "timestamp without time zone", "date",
})
_TEAM_WORDS = frozenset({"team", "player", "athlete", "club"})
_POSTAL_WORDS = frozenset({"postal", "zip", "postcode", "zipcode"})
```

Add these methods to the `DomainResearcher` class:

```python
def get_filtered_questions(self, schema: Dict[str, List[Dict]]) -> List[Question]:
    """Return Q&A questions relevant to the columns present in schema."""
    all_col_names: set[str] = set()
    all_col_types: set[str] = set()
    for cols in schema.values():
        for col in cols:
            all_col_names.add(col["name"].lower())
            all_col_types.add(col["type"].lower())

    questions = [_Q_ENTITY_DESCRIPTION, _Q_GAP_TYPES, _Q_TRUSTED_SOURCES]

    if all_col_types & _TEXT_TYPES:
        questions.append(_Q_TEXT_FIELDS)

    if any(word in col for col in all_col_names for word in _TEAM_WORDS):
        questions.append(_Q_TEAM_ALIASES)

    if any(word in col for col in all_col_names for word in _POSTAL_WORDS):
        questions.append(_Q_POSTAL_FORMAT)

    if all_col_types & _TIMESTAMP_TYPES:
        questions.append(_Q_DATETIME_FORMAT)

    questions.append(_Q_INDUSTRY_CONTEXT)
    return questions

def build_schema_prompt(
    self,
    schema: Dict[str, List[Dict]],
    annotations: Dict[str, str],
    data_samples: Dict[str, List],
    answers: Dict[str, str],
) -> str:
    """Build LLM prompt grounded in schema, annotations, and actual data samples."""
    # Schema summary
    schema_lines = []
    for table, cols in schema.items():
        schema_lines.append(f"\nTable: {table}")
        for col in cols:
            ann = annotations.get(f"{table}.{col['name']}", "")
            ann_note = f"  — {ann}" if ann else ""
            sample_key = f"{table}.{col['name']}"
            samples = data_samples.get(sample_key, [])
            if samples:
                sample_note = f"  [samples: {', '.join(str(s) for s in samples[:5])}]"
            else:
                sample_note = "  [no data yet]"
            schema_lines.append(
                f"  {col['name']} ({col['type']}){ann_note}{sample_note}"
            )
    schema_block = "\n".join(schema_lines)

    # Q&A answers
    answers_text = "\n".join(
        f"  {k}: {v}" for k, v in answers.items() if v
    )

    return textwrap.dedent(f"""
        You are a data quality expert helping initialize a new data cleaning domain.

        Domain: {self.domain}

        === ACTUAL DATABASE SCHEMA (use these exact column names) ===
        {schema_block}

        === USER CONTEXT ===
        {answers_text}

        Generate seed content for this domain. Use ONLY the column names shown above.
        Respond with a single JSON object (no markdown, no explanation outside JSON)
        with exactly these keys:

        {{
          "spell_corrections": [
            {{"wrong": "misspelled", "right": "correct", "confidence": 0.95}},
            ...  // 15-25 evidence-based corrections for text columns that have data samples above
                 // SKIP columns with [no data yet] — do not guess
          ],
          "query_packs": [
            {{
              "gap_type": "gap_type_key",
              "seed_queries": [
                "query template using {{field_name}} placeholders matching schema above",
                ...
              ]
            }},
            ...
          ],
          "column_descriptions": [
            {{
              "column_name": "exact_column_name_from_schema",
              "description": "what this field contains",
              "example_values": ["example1", "example2"],
              "data_type": "text|date|phone|email|numeric|code"
            }},
            ...
          ]
        }}

        Rules:
        - spell_corrections: only for columns that have data samples shown above
        - column names in column_descriptions must exactly match names in the schema above
        - gap_type keys must be valid Python identifiers (lowercase, underscores)
        - {{field_name}} placeholders in seed_queries must match column names from schema
        - confidence values must be 0.0-1.0
        - data_type must be exactly one of: text, date, phone, email, numeric, code
    """).strip()

def research_with_schema(
    self,
    answers: Dict[str, str],
    schema: Dict[str, List[Dict]],
    annotations: Dict[str, str],
    data_samples: Dict[str, List],
    llm_client: Any,
    model: str,
) -> "ResearchBundle":
    """Research with schema context — grounded in actual columns and data samples."""
    prompt = self.build_schema_prompt(schema, annotations, data_samples, answers)
    response = llm_client.messages.create(
        model=model,
        max_tokens=4096,
        messages=[{"role": "user", "content": prompt}],
    )
    text_block = next(
        (block for block in response.content if hasattr(block, "text")), None
    )
    if text_block is None:
        raise ValueError(
            f"No text block in LLM response. "
            f"Block types: {[type(b).__name__ for b in response.content]}"
        )
    return self.parse_llm_response(text_block.text)
```

- [ ] **Step 4: Run tests**

```bash
cd /mnt/f/AI_learning_project && python -m pytest tests/test_research_domain.py -v
```
Expected: all green (new tests pass, existing tests unchanged)

---

## Task 7: Phase 3 — data sampling + seed research wired into orchestrator

**Files:**
- Modify: `scripts/initialize_domain.py` — implement `phase3_seed_research()`
- Modify: `tests/scripts/test_initialize_domain.py` — add Phase 3 tests

- [ ] **Step 1: Write failing tests**

Append to `tests/scripts/test_initialize_domain.py`:

```python
class TestSampleTextColumns:
    def test_returns_samples_for_text_columns(self):
        from scripts.initialize_domain import _sample_text_columns

        schema = {
            "events": [
                {"name": "event_name", "type": "text", "notnull": False, "pk": False},
                {"name": "event_id",   "type": "uuid", "notnull": True,  "pk": True},
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
                {"name": "price",      "type": "numeric",  "notnull": False, "pk": False},
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
        # Should not raise
        samples = _sample_text_columns(schema, conn)
        assert "events.event_name" not in samples or samples["events.event_name"] == []


class TestPhase3:
    def _make_schema(self):
        return {
            "events": [
                {"name": "event_name", "type": "text",   "notnull": False, "pk": False},
                {"name": "home_team",  "type": "text",   "notnull": False, "pk": False},
            ]
        }

    def test_skips_spell_corrections_when_all_columns_empty(self, capsys, tmp_path):
        from scripts.initialize_domain import phase3_seed_research

        schema = self._make_schema()
        conn = MagicMock()

        with patch("scripts.initialize_domain._sample_text_columns", return_value={}), \
             patch("scripts.initialize_domain._load_annotations", return_value={}), \
             patch("builtins.input", return_value=""):
            phase3_seed_research("sports_ticketing", schema, conn)

        out = capsys.readouterr().out
        assert "no data" in out.lower() or "skipped" in out.lower() or "empty" in out.lower()

    def test_calls_research_with_schema_when_data_present(self, tmp_path):
        from scripts.initialize_domain import phase3_seed_research

        schema = self._make_schema()
        conn = MagicMock()
        samples = {"events.event_name": ["Leafs vs Sens"], "events.home_team": ["Leafs"]}

        with patch("scripts.initialize_domain._sample_text_columns", return_value=samples), \
             patch("scripts.initialize_domain._load_annotations", return_value={}), \
             patch("scripts.initialize_domain.create_client") as mock_cc, \
             patch("scripts.initialize_domain.DomainResearcher") as mock_dr, \
             patch("builtins.input", side_effect=["", "", "", "", "", "", "", "y"]):
            mock_cc.return_value = (MagicMock(), "anthropic", "claude-haiku-4-5-20251001")
            mock_bundle = MagicMock()
            mock_bundle.spell_corrections = []
            mock_bundle.query_packs = []
            mock_bundle.column_descriptions = []
            mock_dr.return_value.research_with_schema.return_value = mock_bundle
            mock_dr.return_value.get_filtered_questions.return_value = []
            mock_dr.return_value.write_seeds.return_value = []

            phase3_seed_research("sports_ticketing", schema, conn)

        mock_dr.return_value.research_with_schema.assert_called_once()
```

- [ ] **Step 2: Run to verify failure**

```bash
cd /mnt/f/AI_learning_project && python -m pytest tests/scripts/test_initialize_domain.py::TestSampleTextColumns tests/scripts/test_initialize_domain.py::TestPhase3 -v 2>&1 | tail -15
```
Expected: failures — `_sample_text_columns` and `_load_annotations` don't exist, `phase3_seed_research` is a stub

- [ ] **Step 3: Implement `phase3_seed_research()` and helpers in `scripts/initialize_domain.py`**

Add these imports at the top:
```python
from pathlib import Path as _Path
from llm_client_factory import create_client
from seeders.domain_researcher import DomainResearcher
from seeders.registry import SeederRegistry
```

Add helpers before `phase3_seed_research`:

```python
_TEXT_COLUMN_TYPES = frozenset({
    "text", "character varying", "varchar", "char", "character"
})

def _sample_text_columns(
    schema: dict[str, list[dict]],
    conn,
    n: int = 50,
) -> dict[str, list]:
    """Sample up to n non-null values from each text column. Skips on DB error."""
    samples: dict[str, list] = {}
    for table, cols in schema.items():
        for col in cols:
            if col["type"].lower() not in _TEXT_COLUMN_TYPES:
                continue
            key = f"{table}.{col['name']}"
            try:
                with conn.cursor() as cur:
                    cur.execute(
                        f"SELECT {col['name']} AS val FROM {table} "
                        f"WHERE {col['name']} IS NOT NULL "
                        f"  AND {col['name']} != '' "
                        f"LIMIT %s",
                        (n,),
                    )
                    samples[key] = [row["val"] for row in cur.fetchall()]
            except Exception:
                pass  # Don't fail initialization if a sample query fails
    return samples


def _load_annotations(domain: str, conn) -> dict[str, str]:
    """Load column annotations from column_metadata. Returns {table.col: description}."""
    try:
        with conn.cursor() as cur:
            cur.execute(
                "SELECT table_name, column_name, description "
                "FROM column_metadata WHERE domain = %s AND description IS NOT NULL",
                (domain,),
            )
            return {
                f"{row['table_name']}.{row['column_name']}": row["description"]
                for row in cur.fetchall()
            }
    except Exception:
        return {}
```

Replace the stub `phase3_seed_research`:

```python
def phase3_seed_research(domain: str, schema: dict, conn) -> None:
    _phase_header(3, "Seed Research")

    print("Loading schema and annotations...")
    annotations = _load_annotations(domain, conn)
    print(f"  Annotations loaded: {len(annotations)} descriptions")

    print("\nSampling data from text columns...")
    samples = _sample_text_columns(schema, conn)

    has_data = any(len(v) > 0 for v in samples.values())

    for key, vals in samples.items():
        if vals:
            print(f"  {key:<40} → {len(vals)} samples found")
        else:
            print(f"  {key:<40} → 0 samples  ← will skip spell corrections for this column")

    empty_text_cols = [k for k, v in samples.items() if not v]
    if not has_data:
        print(
            "\n⚠  No data found in text columns. "
            "Spell correction generation skipped.\n"
            f"Re-run with --refresh-seeds after data is ingested."
        )

    # Build schema-filtered Q&A
    researcher = DomainResearcher(domain=domain)
    questions = researcher.get_filtered_questions(schema)
    if not has_data:
        questions = [q for q in questions if q.key != "text_fields"]

    print(f"\nAsking {len(questions)} question(s) tailored to your schema...\n")
    answers: dict[str, str] = {}
    for q in questions:
        if q.hint:
            print(f"  hint: {q.hint}")
        answers[q.key] = input(f"{q.prompt}\n> ").strip()

    print("\nConnecting to LLM to generate seed content...")
    try:
        client, backend, model = create_client()
    except EnvironmentError as e:
        print(f"\nERROR: {e}")
        sys.exit(1)

    bundle = researcher.research_with_schema(
        answers=answers,
        schema=schema,
        annotations=annotations,
        data_samples=samples,
        llm_client=client,
        model=model,
    )

    # Preview
    print("\n" + "─" * 50)
    print("PREVIEW — generated seed content")
    print("─" * 50)
    print(f"  Spell corrections: {len(bundle.spell_corrections)}"
          + (" (evidence-based)" if has_data else " (skipped — no data)"))
    print(f"  Query packs:       {len(bundle.query_packs)} gap types")
    print(f"  Column metadata:   {len(bundle.column_descriptions)} columns")
    print("─" * 50)

    confirm = input("\nWrite seed files? [y/N] ").strip().lower()
    if confirm != "y":
        print("Aborted. No files written.")
        return

    output_dir = _Path("data/seeds") / domain
    output_dir.mkdir(parents=True, exist_ok=True)
    written = researcher.write_seeds(bundle, output_dir=output_dir, dry_run=False, force=True)

    for path in written:
        print(f"  Writing {path}... done")

    print("\nLoading seeds to database...")
    try:
        registry = SeederRegistry(domain)
        results = registry.run_all(conn)
        succeeded = sum(1 for v in results.values() if v is not None and v >= 0)
        failed = sum(1 for v in results.values() if v is None)
        print(f"  {succeeded} seeder(s) succeeded, {failed} failed")
    except Exception as e:
        print(f"  ⚠ Seeder run error: {e}")
```

```python
def phase3_seed_research(domain: str, schema: dict, conn) -> None:
    _phase_header(3, "Seed Research")

    print("Loading schema and annotations...")
    annotations = _load_annotations(domain, conn)
    print(f"  Annotations loaded: {len(annotations)} descriptions")

    print("\nSampling data from text columns...")
    samples = _sample_text_columns(schema, conn)
    has_data = any(len(v) > 0 for v in samples.values())

    for key, vals in samples.items():
        count = len(vals)
        if count:
            print(f"  {key:<40} → {count} samples found")
        else:
            print(f"  {key:<40} → 0 samples  ← will skip spell corrections for this column")

    if not has_data:
        print(
            "\n⚠  No data found in text columns. Spell correction generation skipped.\n"
            "   Re-run with --refresh-seeds after data is ingested."
        )

    researcher = DomainResearcher(domain=domain)
    questions = researcher.get_filtered_questions(schema)
    if not has_data:
        questions = [q for q in questions if q.key != "text_fields"]

    print(f"\nAsking {len(questions)} question(s) tailored to your schema...\n")
    answers: dict[str, str] = {}
    for q in questions:
        if q.hint:
            print(f"  hint: {q.hint}")
        answers[q.key] = input(f"{q.prompt}\n> ").strip()

    print("\nConnecting to LLM to generate seed content...")
    try:
        client, _backend, model = create_client()
    except EnvironmentError as e:
        print(f"\nERROR: {e}")
        sys.exit(1)

    bundle = researcher.research_with_schema(
        answers=answers,
        schema=schema,
        annotations=annotations,
        data_samples=samples,
        llm_client=client,
        model=model,
    )

    print("\n" + "─" * 50)
    print("PREVIEW — generated seed content")
    print("─" * 50)
    print(f"  Spell corrections: {len(bundle.spell_corrections)}"
          + (" (evidence-based)" if has_data else " (skipped — no data)"))
    print(f"  Query packs:       {len(bundle.query_packs)} gap types")
    print(f"  Column metadata:   {len(bundle.column_descriptions)} columns")
    print("─" * 50)

    if input("\nWrite seed files? [y/N] ").strip().lower() != "y":
        print("Aborted.")
        return

    output_dir = _Path("data/seeds") / domain
    output_dir.mkdir(parents=True, exist_ok=True)
    written = researcher.write_seeds(bundle, output_dir=output_dir, dry_run=False, force=True)
    for path in written:
        print(f"  Writing {path}... done")

    print("\nLoading seeds to database...")
    try:
        registry = SeederRegistry(domain)
        results = registry.run_all(conn)
        succeeded = sum(1 for v in results.values() if v is not None and v >= 0)
        failed = sum(1 for v in results.values() if v is None)
        print(f"  {succeeded} seeder(s) succeeded, {failed} failed")
    except Exception as e:
        print(f"  ⚠ Seeder run error: {e}")
```

- [ ] **Step 4: Run tests**

```bash
cd /mnt/f/AI_learning_project && python -m pytest tests/scripts/test_initialize_domain.py -v
```
Expected: all green

---

## Task 8: `add_table` subcommand

**Files:**
- Modify: `scripts/initialize_domain.py` — implement `cmd_add_table()`
- Modify: `tests/scripts/test_initialize_domain.py` — add add_table tests

- [ ] **Step 1: Write failing tests**

Append to `tests/scripts/test_initialize_domain.py`:

```python
class TestCmdAddTable:
    def test_reports_no_new_tables_when_all_registered(self, tmp_path, capsys):
        from scripts.initialize_domain import cmd_add_table
        from services.domain_initializer import DomainInitializer
        import json

        reg = tmp_path / "r.json"
        reg.write_text(json.dumps({"domains": {"sports_ticketing": {"tables": ["events"]}}}))
        di = DomainInitializer("sports_ticketing", registry_path=reg)
        conn = _mock_conn(["events", "column_metadata"])  # column_metadata is system table

        cmd_add_table("sports_ticketing", di, conn)
        assert "No new tables" in capsys.readouterr().out

    def test_shows_new_tables_and_registers_selection(self, tmp_path, capsys):
        from scripts.initialize_domain import cmd_add_table
        from services.domain_initializer import DomainInitializer
        import json

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
        import json

        reg = tmp_path / "r.json"
        reg.write_text(json.dumps({"domains": {"sports_ticketing": {"tables": ["events"]}}}))
        di = DomainInitializer("sports_ticketing", registry_path=reg)
        conn = _mock_conn(["events", "venues"])

        with patch("scripts.initialize_domain.phase2_annotation") as mock_p2, \
             patch("builtins.input", side_effect=["1", "n"]):
            cmd_add_table("sports_ticketing", di, conn)

        mock_p2.assert_called_once()
        # tables passed to phase2 should include new table
        call_tables = mock_p2.call_args[0][1]
        assert "venues" in call_tables
```

- [ ] **Step 2: Run to verify failure**

```bash
cd /mnt/f/AI_learning_project && python -m pytest tests/scripts/test_initialize_domain.py::TestCmdAddTable -v 2>&1 | tail -10
```
Expected: failures (cmd_add_table is a stub)

- [ ] **Step 3: Implement `cmd_add_table()` in `scripts/initialize_domain.py`**

Replace the stub:

```python
def cmd_add_table(domain: str, initializer: DomainInitializer, conn) -> None:
    _section_header(f"add_table — {domain}")

    registered = initializer.get_registered_tables() or []
    print(f"Currently registered: {', '.join(registered) or 'none'}")
    print("Scanning database for new tables...\n")

    new_tables = initializer.diff_tables(conn)

    if not new_tables:
        print("No new tables found. Domain is up to date.")
        return

    # Same selection UX as Phase 0
    candidates = new_tables
    if len(candidates) > 15:
        scored = initializer.score_tables(candidates)
        candidates = [t for t, _ in scored[:10]]

    print(f"New tables found (not yet registered):\n")
    for i, table in enumerate(candidates, 1):
        print(f"  [{i}] {table}")

    raw = input("\nEnter numbers to add (comma-separated, Enter to skip all): ").strip()
    if not raw:
        print("No tables added.")
        return

    try:
        indices = [int(x.strip()) - 1 for x in raw.split(",")]
        selected = [candidates[i] for i in indices if 0 <= i < len(candidates)]
    except (ValueError, IndexError):
        print("Invalid selection.")
        return

    if not selected:
        print("No valid tables selected.")
        return

    updated = registered + selected
    initializer.register_tables(updated)
    print(f"\nAdded: {', '.join(selected)} → domain_registry.json")

    # Annotate new tables + any unannotated columns in existing tables
    print()
    phase2_annotation(domain, selected, conn)

    # Prompt for seed refresh
    if _pause("Seeds were generated from previous schema. Refresh seeds with new table context?",
              default_yes=False):
        # Load full registered schema for seed refresh
        all_tables = initializer.get_registered_tables() or []
        schema = phase1_schema_discovery(all_tables, conn)
        phase3_seed_research(domain, schema, conn)
```

- [ ] **Step 4: Run tests**

```bash
cd /mnt/f/AI_learning_project && python -m pytest tests/scripts/test_initialize_domain.py::TestCmdAddTable -v
```
Expected: all green

---

## Task 9: `--refresh-seeds` flag

**Files:**
- Modify: `scripts/initialize_domain.py` — implement `cmd_refresh_seeds()`
- Modify: `tests/scripts/test_initialize_domain.py` — add refresh-seeds tests

- [ ] **Step 1: Write failing tests**

Append to `tests/scripts/test_initialize_domain.py`:

```python
class TestCmdRefreshSeeds:
    def test_exits_if_domain_not_registered(self, tmp_path, capsys):
        from scripts.initialize_domain import cmd_refresh_seeds
        from services.domain_initializer import DomainInitializer
        import json

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
        import json

        reg = tmp_path / "r.json"
        reg.write_text(json.dumps({"domains": {"sports_ticketing": {"tables": ["events"]}}}))
        di = DomainInitializer("sports_ticketing", registry_path=reg)
        conn = MagicMock()

        with patch("scripts.initialize_domain.phase1_schema_discovery") as mock_p1, \
             patch("scripts.initialize_domain.phase3_seed_research") as mock_p3:
            mock_p1.return_value = {"events": []}
            cmd_refresh_seeds("sports_ticketing", di, conn)

        mock_p1.assert_called_once_with(["events"], conn)
        mock_p3.assert_called_once()
        # phase2 must NOT have been called
```

- [ ] **Step 2: Run to verify failure**

```bash
cd /mnt/f/AI_learning_project && python -m pytest tests/scripts/test_initialize_domain.py::TestCmdRefreshSeeds -v 2>&1 | tail -10
```
Expected: failures (stub)

- [ ] **Step 3: Implement `cmd_refresh_seeds()` in `scripts/initialize_domain.py`**

Replace the stub:

```python
def cmd_refresh_seeds(domain: str, initializer: DomainInitializer, conn) -> None:
    _section_header(f"Refresh Seeds — {domain}")

    tables = initializer.get_registered_tables()
    if not tables:
        print(
            f"Domain '{domain}' not registered. "
            f"Run: python scripts/initialize_domain.py --domain {domain}"
        )
        sys.exit(1)

    print(f"Using registered tables: {', '.join(tables)}")
    print("Skipping phases 0-2 (annotation unchanged). Running Phase 3 only.\n")

    schema = phase1_schema_discovery(tables, conn)
    phase3_seed_research(domain, schema, conn)
```

- [ ] **Step 4: Run all tests**

```bash
cd /mnt/f/AI_learning_project && python -m pytest tests/services/test_domain_initializer.py tests/scripts/test_initialize_domain.py tests/test_metadata_annotation.py tests/test_research_domain.py -v
```
Expected: all green

---

## Task 10: Full suite + smoke test

**Files:**
- No new files

- [ ] **Step 1: Run the full test suite**

```bash
cd /mnt/f/AI_learning_project && python -m pytest tests/ -v --tb=short 2>&1 | tail -40
```
Expected: all existing tests green; new tests green; no regressions

- [ ] **Step 2: Smoke-test the CLI help output**

```bash
cd /mnt/f/AI_learning_project && python scripts/initialize_domain.py --help
```
Expected:
```
usage: initialize_domain.py [-h] --domain DOMAIN [--force] [--refresh-seeds] [{add_table}]
...
```

```bash
cd /mnt/f/AI_learning_project && python scripts/annotate_domain.py --help
```
Expected: usage printed without errors

- [ ] **Step 3: Verify annotate_domain.py fails clearly for unregistered domain**

```bash
cd /mnt/f/AI_learning_project && python scripts/annotate_domain.py --domain nonexistent_domain --dry-run
```
Expected:
```
Domain 'nonexistent_domain' not registered or has no tables. Run: python scripts/initialize_domain.py --domain nonexistent_domain
```

- [ ] **Step 4: Verify registry JSON is still valid after all changes**

```bash
cd /mnt/f/AI_learning_project && python -c "
import json
data = json.load(open('data/domain_registry.json'))
print('domains:', list(data['domains'].keys()))
print('real_estate tables:', data['domains']['real_estate']['tables'])
"
```
Expected:
```
domains: ['real_estate']
real_estate tables: ['raw_data', 'cleaned_data']
```
