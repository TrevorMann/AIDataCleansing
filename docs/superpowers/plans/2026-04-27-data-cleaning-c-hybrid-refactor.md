# Data Cleaning C-Hybrid Refactor — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Refactor `multi_turn_conversation.py` and supporting modules into a `cleaning/` subpackage with per-record country routing, an escalation sub-agent for hard cases, a queryable `flags` table, tiered LLM clients, and a programmatic-first public API. Implementation follows the C-Hybrid spec at `docs/superpowers/specs/2026-04-27-data-cleaning-c-hybrid-refactor-design.md`.

**Architecture:** New code lives in a `cleaning/` subpackage. Pre-cleaning is pure Python; per-country research runs through one shared `CleaningAgent` per country group; hard cases escalate to a dedicated `EscalationAgent` using a stronger model tier. Flags are persisted to a separate table so unresolved records are queryable. The model client is env-driven across three named tiers (`fast`/`standard`/`deep`) so the same code runs on `gpt-oss-20b:free`, Haiku via OpenRouter, and native Anthropic without changes.

**Tech Stack:** Python 3.12, SQLite (via stdlib `sqlite3`), Anthropic SDK pointed at OpenRouter or `api.anthropic.com`, Tavily Search API, pytest for tests, `unittest.mock` for mocking the LLM and Tavily boundaries.

**Reference docs:**
- Spec: `docs/superpowers/specs/2026-04-27-data-cleaning-c-hybrid-refactor-design.md`
- Forward-looking migration spec: `docs/superpowers/specs/2026-04-27-data-cleaning-a-migration-design.md`

**Conventions used in this plan:**
- Run pytest as `python3 -m pytest` from the project root (the user's venv has pytest).
- All tests live under `tests/cleaning/` mirroring the source layout.
- Each task ends in a commit. Use the message templates shown — they keep history readable for the spec review.
- "Use the test-driven-development skill" means: write the failing test first, see it fail for the right reason, then write minimal code to make it pass.

---

## File Structure

**New files (created):**

| Path | Responsibility |
|---|---|
| `cleaning/__init__.py` | Public API exports |
| `cleaning/types.py` | Shared dataclasses: `SearchHit`, `CleaningOutput`, `CleaningRunReport`, `FlagSeverity` |
| `cleaning/flags.py` | `FlagType` enum, `Flag` dataclass, `persist_flags()`, `query_unresolved_flags()` |
| `cleaning/cache.py` | `WebSearchCache` (thread-safe) + the Tavily call function |
| `cleaning/llm_client.py` | `LLMClient`, `Clients` dataclass, `build_clients()`, `LLMUnavailableError` |
| `cleaning/agent.py` | `CleaningAgent` class, `needs_escalation()` predicate |
| `cleaning/escalation.py` | `EscalationAgent` class |
| `cleaning/orchestrator.py` | `run_cleaning_workflow()` + helpers (`interpret_query`, `fetch_records`, `group_by_country`, `merge_results`, `persist_outputs`, `detect_country_filter`) |
| `cleaning/conversation.py` | `AdHocConversation` class, CRUD tool definitions, `_build_table_properties`, `_column_names` |
| `cleaning/pre_cleaner.py` | Moved from project root; no logic changes |
| `tests/cleaning/__init__.py` | Empty marker |
| `tests/cleaning/conftest.py` | Shared fixtures: `tmp_db`, `mock_llm`, `mock_tavily` |
| `tests/cleaning/test_pre_cleaner.py` | Unit tests for pre-cleaner |
| `tests/cleaning/test_flags.py` | Unit tests for `flags.py` |
| `tests/cleaning/test_cache.py` | Unit tests for `WebSearchCache` |
| `tests/cleaning/test_llm_client.py` | Unit tests for `LLMClient` + `build_clients` |
| `tests/cleaning/test_types.py` | Smoke tests for dataclass shapes |
| `tests/cleaning/test_agent.py` | Unit tests for `CleaningAgent` + `needs_escalation` |
| `tests/cleaning/test_escalation.py` | Unit tests for `EscalationAgent` |
| `tests/cleaning/test_orchestrator.py` | Integration tests for `run_cleaning_workflow` |
| `tests/cleaning/test_conversation.py` | Unit tests for `AdHocConversation` |

**Modified files:**

| Path | Change |
|---|---|
| `database.py` | Add `flags` table to `init_db()` |
| `db_helpers.py` | Add `insert_flag`, `update_flag_resolution`, `query_flags` |
| `guardrails.py` | Make `VALID_COUNTRIES` the single source of truth for canonical codes (no functional change yet, mark as canonical via comment) |
| `multi_turn_conversation.py` | Shrink to ~120 lines: REPL wrapper that calls `cleaning.run_cleaning_workflow` and `cleaning.AdHocConversation` |
| `pyproject.toml` or `requirements.txt` | (If exists) — no changes; no new dependencies |

**Files deleted:**

| Path | Reason |
|---|---|
| `pre_cleaner.py` (root) | Moved to `cleaning/pre_cleaner.py` |
| `data_cleaning_agent.py` | Useful parts moved into `cleaning/orchestrator.py` |
| `data_cleaning/clean_data_workflow.py` | Legacy menu-based path, superseded |
| `data_cleaning/` (directory if empty) | After above deletion |
| `debug_api.py`, `test_direct.py`, `test_sdk.py` | Ad-hoc debug scripts |
| `debug_output.txt` | Debug artifact (already gitignored, may not be tracked) |

**Files unchanged:**
- `config.py`, `schema_discovery.py`, `setup_sample_data.py`, `validate_data_quality.py`
- `prompts/` (the country-specific prompts in `canada.py`, `usa.py`, etc., and `research.py`)
- `tests/test_integration.py`, `tests/test_schema_discovery.py` (still pass after the refactor)
- `playground/`, `scripts/`, `data/`

---

## Phase 1 — Foundation

### Task 1: Create the `cleaning/` subpackage skeleton

**Files:**
- Create: `cleaning/__init__.py`
- Create: `tests/cleaning/__init__.py`
- Create: `tests/cleaning/conftest.py`

This task lays down the package skeleton and shared test fixtures so subsequent tasks have a place to put files and a consistent fixture vocabulary.

- [ ] **Step 1: Create empty package markers**

```bash
mkdir -p cleaning tests/cleaning
```

Create `cleaning/__init__.py` with this placeholder content (will be filled in Task 13):

```python
"""Data cleaning subpackage. Public API exports added in Task 13."""
```

Create `tests/cleaning/__init__.py` empty (just `touch tests/cleaning/__init__.py`).

- [ ] **Step 2: Create the shared test fixtures file**

Create `tests/cleaning/conftest.py`:

```python
"""Shared fixtures for cleaning/ tests."""
import os
import tempfile
from unittest.mock import MagicMock
import pytest


@pytest.fixture
def tmp_db():
    """Yield a path to a fresh, isolated SQLite DB initialized with the full schema."""
    from database import init_db
    with tempfile.TemporaryDirectory() as tmpdir:
        path = os.path.join(tmpdir, "test.db")
        init_db(path)
        yield path


@pytest.fixture
def mock_llm():
    """Return a MagicMock standing in for cleaning.llm_client.LLMClient.

    Default behavior: messages_create returns a MagicMock with stop_reason='end_turn'
    and content=[]; tests override .messages_create.side_effect or .return_value as needed.
    """
    client = MagicMock()
    client.model = "mock-model"
    client.supports_cache_control = False
    response = MagicMock()
    response.stop_reason = "end_turn"
    response.content = []
    client.messages_create.return_value = response
    return client


@pytest.fixture
def mock_tavily(monkeypatch):
    """Patch the underlying Tavily call so WebSearchCache misses don't hit the network.

    Tests can mutate the returned MagicMock's return_value or side_effect.
    Returns the mock so tests can assert call counts.
    """
    from cleaning import cache
    fake = MagicMock(return_value="MOCKED TAVILY RESULT")
    monkeypatch.setattr(cache, "_tavily_call", fake)
    return fake
```

- [ ] **Step 3: Verify pytest can discover the new directory**

Run: `python3 -m pytest tests/cleaning/ --collect-only`
Expected: exit 0 with `0 tests collected` (no test files yet, but discovery should succeed without errors).

- [ ] **Step 4: Commit**

```bash
git add cleaning/ tests/cleaning/
git commit -m "scaffold cleaning/ subpackage and shared test fixtures

Empty __init__.py marker, plus tests/cleaning/conftest.py providing
tmp_db, mock_llm, and mock_tavily fixtures used by every subsequent
test module."
```

---

### Task 2: Move `pre_cleaner.py` into `cleaning/`

**Files:**
- Create: `cleaning/pre_cleaner.py` (copied from root)
- Delete: `pre_cleaner.py` (root, after import audit)
- Modify: any file that imports from `pre_cleaner` at the root (audit and update)

This is a structural move. The pre-cleaner logic does not change.

- [ ] **Step 1: Audit all imports of `pre_cleaner` so nothing is missed**

Run: `grep -rn "from pre_cleaner\|import pre_cleaner" --include="*.py" .`

Record the output. Expect to find references in:
- `data_cleaning_agent.py` (will be deleted in Task 16, but update it now to keep tests green during the move)
- Possibly `tests/` files

- [ ] **Step 2: Copy the file and update imports**

```bash
git mv pre_cleaner.py cleaning/pre_cleaner.py
```

For every file listed in step 1, replace `from pre_cleaner import ...` with `from cleaning.pre_cleaner import ...` and `import pre_cleaner` with `import cleaning.pre_cleaner as pre_cleaner`.

- [ ] **Step 3: Verify imports still resolve and existing tests still pass**

Run: `python3 -m pytest tests/ -v`
Expected: All existing tests pass. Any failure means a missed import — fix it before continuing.

- [ ] **Step 4: Create initial pre-cleaner test file (port one existing case to confirm wiring)**

Create `tests/cleaning/test_pre_cleaner.py`:

```python
"""Unit tests for cleaning.pre_cleaner. Pure-Python, no fixtures needed."""
from cleaning.pre_cleaner import (
    get_country_code, clean_name, clean_city, clean_address,
    expand_country, expand_state_province, normalize_postal,
    format_phone, needs_research, pre_clean_record,
)


def test_get_country_code_from_full_name():
    assert get_country_code("Canada") == "CA"
    assert get_country_code("United States") == "USA"
    assert get_country_code("Holland") == "NL"


def test_get_country_code_from_abbrev():
    assert get_country_code("CA") == "CA"
    assert get_country_code("USA") == "USA"


def test_get_country_code_unknown():
    assert get_country_code("Atlantis") is None
    assert get_country_code("") is None
    assert get_country_code(None) is None


def test_clean_name_titlecase():
    assert clean_name("john doe") == "John Doe"
    assert clean_name("  alice   smith  ") == "Alice   Smith"


def test_normalize_postal_canada():
    assert normalize_postal("M6H1E7", "CA") == "M6H 1E7"
    assert normalize_postal("m6h 1e7", "CA") == "M6H 1E7"


def test_format_phone_north_america():
    assert format_phone("4165550123", "CA") == "(416) 555-0123"
    assert format_phone("1-416-555-0123", "USA") == "(416) 555-0123"


def test_needs_research_missing_municipality():
    assert needs_research({"municipality": "", "postal_code": "M6H 1E7"}) is True
    assert needs_research({"municipality": "N/A", "postal_code": "M6H 1E7"}) is True


def test_needs_research_complete_record():
    assert needs_research({"municipality": "Toronto", "postal_code": "M6H 1E7"}) is False


def test_pre_clean_record_full_run():
    raw = {
        "name": "john doe", "city": "toronto", "address": "25 Muir St.",
        "country": "CA", "state_province": "ON", "phone": "4165550123",
        "postal_code": "M6H1E7", "municipality": "",
    }
    cleaned = pre_clean_record(raw)
    assert cleaned["name"] == "John Doe"
    assert cleaned["city"] == "Toronto"
    assert cleaned["address"] == "25 Muir Street"
    assert cleaned["country"] == "Canada"
    assert cleaned["state_province"] == "Ontario"
    assert cleaned["phone"] == "(416) 555-0123"
    assert cleaned["postal_code"] == "M6H 1E7"
    assert cleaned["_pre_clean_changes"]  # non-empty list
```

- [ ] **Step 5: Run new tests, verify they pass**

Run: `python3 -m pytest tests/cleaning/test_pre_cleaner.py -v`
Expected: All tests pass.

- [ ] **Step 6: Commit**

```bash
git add cleaning/pre_cleaner.py tests/cleaning/test_pre_cleaner.py data_cleaning_agent.py
git commit -m "move pre_cleaner.py into cleaning/ subpackage

Pure structural move plus initial test coverage. data_cleaning_agent.py
import updated; will be deleted in Task 16. No behavior change."
```

---

### Task 3: Add `flags` table to `database.py` and CRUD to `db_helpers.py`

**Files:**
- Modify: `database.py:11-84` (add `flags` table to `init_db`)
- Modify: `db_helpers.py` (append flag CRUD functions)
- Modify: `tests/cleaning/test_pre_cleaner.py` (no — leave alone)
- Create: `tests/cleaning/test_db_flags.py`

Use the test-driven-development skill — write the test first.

- [ ] **Step 1: Write the failing test for `flags` table existence**

Create `tests/cleaning/test_db_flags.py`:

```python
"""Tests for the flags table schema and db_helpers CRUD."""
import sqlite3


def test_flags_table_exists(tmp_db):
    conn = sqlite3.connect(tmp_db)
    rows = conn.execute(
        "SELECT name FROM sqlite_master WHERE type='table' AND name='flags'"
    ).fetchall()
    conn.close()
    assert len(rows) == 1


def test_flags_table_columns(tmp_db):
    conn = sqlite3.connect(tmp_db)
    cols = {r[1] for r in conn.execute("PRAGMA table_info(flags)").fetchall()}
    conn.close()
    expected = {
        'id', 'raw_data_id', 'cleaned_data_id', 'flag_type', 'severity',
        'reason', 'raised_by', 'raised_at', 'resolved_at', 'resolved_by',
        'resolution_note',
    }
    assert expected.issubset(cols)


def test_unresolved_index_exists(tmp_db):
    conn = sqlite3.connect(tmp_db)
    rows = conn.execute(
        "SELECT name FROM sqlite_master WHERE type='index' AND name='idx_flags_unresolved'"
    ).fetchall()
    conn.close()
    assert len(rows) == 1


def test_insert_flag_returns_id(tmp_db):
    from db_helpers import insert_raw_data, insert_flag
    raw_id = insert_raw_data(tmp_db, name="x", country="CA")
    flag_id = insert_flag(
        tmp_db,
        raw_data_id=raw_id, cleaned_data_id=None,
        flag_type="postal_unresolved", severity="NEEDS_REVIEW",
        reason="postal could not be verified", raised_by="agent:CA",
    )
    assert isinstance(flag_id, int) and flag_id > 0


def test_query_flags_unresolved_only(tmp_db):
    from db_helpers import insert_raw_data, insert_flag, update_flag_resolution, query_flags
    raw_id = insert_raw_data(tmp_db, name="x", country="CA")
    f1 = insert_flag(tmp_db, raw_data_id=raw_id, flag_type="t", severity="WARN",
                     reason="r1", raised_by="agent:CA")
    f2 = insert_flag(tmp_db, raw_data_id=raw_id, flag_type="t", severity="WARN",
                     reason="r2", raised_by="agent:CA")
    update_flag_resolution(tmp_db, f1, resolved_by="trevor", note="manual fix")

    unresolved = query_flags(tmp_db, only_unresolved=True)
    assert len(unresolved) == 1
    assert unresolved[0]['id'] == f2

    all_flags = query_flags(tmp_db, only_unresolved=False)
    assert len(all_flags) == 2
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `python3 -m pytest tests/cleaning/test_db_flags.py -v`
Expected: FAIL — `flags` table does not exist; `insert_flag` not importable.

- [ ] **Step 3: Add the `flags` table to `database.py`**

Inside `init_db()` in `database.py`, after the `audit_log` block and before `column_metadata`, add:

```python
    # flags table — queryable record of unresolved or noteworthy issues per record.
    # Schema documented in: docs/superpowers/specs/2026-04-27-data-cleaning-c-hybrid-refactor-design.md §5.3
    cursor.execute('''
    CREATE TABLE IF NOT EXISTS flags (
        id              INTEGER PRIMARY KEY AUTOINCREMENT,
        raw_data_id     INTEGER NOT NULL,
        cleaned_data_id INTEGER,
        flag_type       TEXT NOT NULL,
        severity        TEXT NOT NULL,
        reason          TEXT NOT NULL,
        raised_by       TEXT NOT NULL,
        raised_at       TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
        resolved_at     TIMESTAMP,
        resolved_by     TEXT,
        resolution_note TEXT,
        FOREIGN KEY (raw_data_id) REFERENCES raw_data(id),
        FOREIGN KEY (cleaned_data_id) REFERENCES cleaned_data(id)
    )
    ''')
    cursor.execute('''
    CREATE INDEX IF NOT EXISTS idx_flags_unresolved ON flags(resolved_at) WHERE resolved_at IS NULL
    ''')
```

- [ ] **Step 4: Add CRUD helpers to `db_helpers.py`**

Append to `db_helpers.py`:

```python
def insert_flag(
    db_path: str,
    raw_data_id: int,
    flag_type: str,
    severity: str,
    reason: str,
    raised_by: str,
    cleaned_data_id: Optional[int] = None,
) -> int:
    """Insert a flag. Returns the row ID."""
    conn = get_db_connection(db_path)
    try:
        cursor = conn.cursor()
        cursor.execute('''
        INSERT INTO flags (raw_data_id, cleaned_data_id, flag_type, severity, reason, raised_by)
        VALUES (?, ?, ?, ?, ?, ?)
        ''', (raw_data_id, cleaned_data_id, flag_type, severity, reason, raised_by))
        conn.commit()
        return cursor.lastrowid
    finally:
        conn.close()


def update_flag_resolution(
    db_path: str,
    flag_id: int,
    resolved_by: str,
    note: Optional[str] = None,
) -> bool:
    """Mark a flag as resolved. Returns True if a row was updated."""
    conn = get_db_connection(db_path)
    try:
        cursor = conn.cursor()
        cursor.execute('''
        UPDATE flags SET resolved_at = CURRENT_TIMESTAMP, resolved_by = ?, resolution_note = ?
        WHERE id = ? AND resolved_at IS NULL
        ''', (resolved_by, note, flag_id))
        conn.commit()
        return cursor.rowcount > 0
    finally:
        conn.close()


def query_flags(
    db_path: str,
    only_unresolved: bool = True,
    raw_data_id: Optional[int] = None,
    flag_type: Optional[str] = None,
    limit: int = 100,
) -> List[Dict]:
    """Query flags. Defaults to unresolved only."""
    where = []
    params: list = []
    if only_unresolved:
        where.append("resolved_at IS NULL")
    if raw_data_id is not None:
        where.append("raw_data_id = ?")
        params.append(raw_data_id)
    if flag_type is not None:
        where.append("flag_type = ?")
        params.append(flag_type)

    sql = "SELECT * FROM flags"
    if where:
        sql += " WHERE " + " AND ".join(where)
    sql += " ORDER BY raised_at DESC LIMIT ?"
    params.append(limit)

    conn = get_db_connection(db_path)
    try:
        cursor = conn.cursor()
        cursor.execute(sql, params)
        return [dict(r) for r in cursor.fetchall()]
    finally:
        conn.close()
```

- [ ] **Step 5: Run tests to verify they pass**

Run: `python3 -m pytest tests/cleaning/test_db_flags.py tests/test_integration.py -v`
Expected: All pass. Existing integration test still works (no regression).

- [ ] **Step 6: Commit**

```bash
git add database.py db_helpers.py tests/cleaning/test_db_flags.py
git commit -m "add flags table + CRUD helpers

Schema per spec §5.3: separate table allows multiple flags per record
and supports unresolved-queue queries via the partial index on
resolved_at. CRUD: insert_flag, update_flag_resolution, query_flags."
```

---

### Task 4: Build `cleaning/flags.py`

**Files:**
- Create: `cleaning/flags.py`
- Create: `tests/cleaning/test_flags.py`

This module provides the typed Python surface (`FlagType` enum, `Flag` dataclass, persistence helpers) that the rest of the package uses.

- [ ] **Step 1: Write the failing tests**

Create `tests/cleaning/test_flags.py`:

```python
"""Tests for cleaning.flags."""
from cleaning.flags import (
    FlagType, FlagSeverity, Flag, persist_flags, query_unresolved_flags,
)


def test_flagtype_values():
    assert FlagType.UNKNOWN_COUNTRY.value == "unknown_country"
    assert FlagType.CROSS_REGION_MISMATCH.value == "cross_region_mismatch"
    assert FlagType.POSTAL_UNRESOLVED.value == "postal_unresolved"
    assert FlagType.POSTAL_AMBIGUOUS.value == "postal_ambiguous"
    assert FlagType.MUNICIPALITY_UNRESOLVED.value == "municipality_unresolved"
    assert FlagType.LOW_CONFIDENCE_RESEARCH.value == "low_confidence_research"
    assert FlagType.GUARDRAIL_BLOCKED.value == "guardrail_blocked"
    assert FlagType.RESOLVED_AFTER_ESCALATION.value == "resolved_after_escalation"


def test_flagseverity_values():
    assert FlagSeverity.INFO.value == "INFO"
    assert FlagSeverity.WARN.value == "WARN"
    assert FlagSeverity.NEEDS_REVIEW.value == "NEEDS_REVIEW"
    assert FlagSeverity.BLOCKED.value == "BLOCKED"


def test_flag_dataclass_construction():
    f = Flag(
        flag_type=FlagType.POSTAL_UNRESOLVED,
        severity=FlagSeverity.NEEDS_REVIEW,
        reason="could not verify M6H against street address",
        raised_by="agent:CA",
    )
    assert f.flag_type is FlagType.POSTAL_UNRESOLVED
    assert f.cleaned_data_id is None  # optional


def test_persist_flags_writes_each_flag(tmp_db):
    from db_helpers import insert_raw_data
    raw_id = insert_raw_data(tmp_db, name="x", country="CA")
    flags = [
        Flag(FlagType.POSTAL_UNRESOLVED, FlagSeverity.NEEDS_REVIEW, "r1", "agent:CA"),
        Flag(FlagType.MUNICIPALITY_UNRESOLVED, FlagSeverity.NEEDS_REVIEW, "r2", "agent:CA"),
    ]
    ids = persist_flags(tmp_db, raw_data_id=raw_id, cleaned_data_id=None, flags=flags)
    assert len(ids) == 2
    assert all(isinstance(i, int) for i in ids)


def test_query_unresolved_flags(tmp_db):
    from db_helpers import insert_raw_data
    raw_id = insert_raw_data(tmp_db, name="x", country="CA")
    persist_flags(tmp_db, raw_data_id=raw_id, cleaned_data_id=None, flags=[
        Flag(FlagType.POSTAL_UNRESOLVED, FlagSeverity.NEEDS_REVIEW, "r", "agent:CA"),
    ])
    results = query_unresolved_flags(tmp_db)
    assert len(results) == 1
    assert results[0]["flag_type"] == "postal_unresolved"
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `python3 -m pytest tests/cleaning/test_flags.py -v`
Expected: FAIL — `cleaning.flags` does not exist.

- [ ] **Step 3: Implement `cleaning/flags.py`**

```python
"""Typed flag types and persistence helpers.

Flags surface unresolved or noteworthy issues raised during cleaning. They live
in their own table (see database.py and spec §5.3) so analytics queries like
"how many cross-region mismatches this week" are trivial.
"""
from dataclasses import dataclass
from enum import Enum
from typing import Optional

from db_helpers import insert_flag, query_flags


class FlagType(str, Enum):
    UNKNOWN_COUNTRY            = "unknown_country"
    CROSS_REGION_MISMATCH      = "cross_region_mismatch"
    POSTAL_UNRESOLVED          = "postal_unresolved"
    POSTAL_AMBIGUOUS           = "postal_ambiguous"
    MUNICIPALITY_UNRESOLVED    = "municipality_unresolved"
    LOW_CONFIDENCE_RESEARCH    = "low_confidence_research"
    GUARDRAIL_BLOCKED          = "guardrail_blocked"
    RESOLVED_AFTER_ESCALATION  = "resolved_after_escalation"


class FlagSeverity(str, Enum):
    INFO         = "INFO"
    WARN         = "WARN"
    NEEDS_REVIEW = "NEEDS_REVIEW"
    BLOCKED      = "BLOCKED"


@dataclass
class Flag:
    flag_type: FlagType
    severity: FlagSeverity
    reason: str
    raised_by: str
    cleaned_data_id: Optional[int] = None


def persist_flags(
    db_path: str,
    *,
    raw_data_id: int,
    cleaned_data_id: Optional[int],
    flags: list[Flag],
) -> list[int]:
    """Persist a list of flags. Returns their new IDs in order."""
    ids = []
    for f in flags:
        cdi = f.cleaned_data_id if f.cleaned_data_id is not None else cleaned_data_id
        ids.append(insert_flag(
            db_path,
            raw_data_id=raw_data_id,
            cleaned_data_id=cdi,
            flag_type=f.flag_type.value,
            severity=f.severity.value,
            reason=f.reason,
            raised_by=f.raised_by,
        ))
    return ids


def query_unresolved_flags(db_path: str, limit: int = 100) -> list[dict]:
    """Convenience wrapper around db_helpers.query_flags(only_unresolved=True)."""
    return query_flags(db_path, only_unresolved=True, limit=limit)
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `python3 -m pytest tests/cleaning/test_flags.py -v`
Expected: All pass.

- [ ] **Step 5: Commit**

```bash
git add cleaning/flags.py tests/cleaning/test_flags.py
git commit -m "add cleaning.flags: FlagType enum, Flag dataclass, persist_flags

Typed Python surface over the flags table. FlagType enum values match
the strings stored in flags.flag_type column. persist_flags allows the
caller to pass a default cleaned_data_id that individual flags can
override (used when one record has flags raised at different lifecycle
stages)."
```

---

## Phase 2 — Infrastructure

### Task 5: Build `cleaning/types.py`

**Files:**
- Create: `cleaning/types.py`
- Create: `tests/cleaning/test_types.py`

Shared dataclasses used across `agent.py`, `escalation.py`, `orchestrator.py`. Tiny — but having them in one place keeps cyclic imports impossible.

- [ ] **Step 1: Write the failing tests**

Create `tests/cleaning/test_types.py`:

```python
"""Smoke tests for cleaning.types dataclasses."""
from cleaning.types import SearchHit, CleaningOutput, CleaningRunReport


def test_search_hit_construction():
    hit = SearchHit(query="M6H Toronto postal", result="...long result...")
    assert hit.query == "M6H Toronto postal"


def test_cleaning_output_defaults():
    out = CleaningOutput(cleaned_record={"id": 1})
    assert out.cleaned_record == {"id": 1}
    assert out.flags == []
    assert out.search_log == []


def test_cleaning_run_report_construction():
    rep = CleaningRunReport(
        records_processed=10, cleaned_count=8, flagged_count=2,
        flags_by_type={"postal_unresolved": 1, "municipality_unresolved": 1},
        cache_stats={"hits": 5, "misses": 3, "queries_cached": 3},
        timing={"interpret": 0.1, "fetch": 0.05, "pre_clean": 0.2,
                "research": 8.0, "persist": 0.3},
        flag_summary=[],
        errors=[],
        summary_text="ok",
    )
    assert rep.records_processed == 10
    assert "postal_unresolved" in rep.flags_by_type
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `python3 -m pytest tests/cleaning/test_types.py -v`
Expected: FAIL — `cleaning.types` does not exist.

- [ ] **Step 3: Implement `cleaning/types.py`**

```python
"""Shared dataclasses for the cleaning subpackage.

These types form the data contracts between CleaningAgent, EscalationAgent,
and the orchestrator. Keeping them here prevents cyclic imports.
"""
from dataclasses import dataclass, field
from typing import Any

from cleaning.flags import Flag


@dataclass
class SearchHit:
    """One web search executed during research, preserved for escalation reuse."""
    query: str
    result: str


@dataclass
class CleaningOutput:
    """Per-record output from CleaningAgent.process() or EscalationAgent.investigate()."""
    cleaned_record: dict[str, Any]
    flags: list[Flag] = field(default_factory=list)
    search_log: list[SearchHit] = field(default_factory=list)


@dataclass
class CleaningRunReport:
    """End-of-run summary from run_cleaning_workflow()."""
    records_processed: int
    cleaned_count: int
    flagged_count: int
    flags_by_type: dict[str, int]
    cache_stats: dict[str, int]
    timing: dict[str, float]
    flag_summary: list[dict]
    errors: list[dict]
    summary_text: str
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `python3 -m pytest tests/cleaning/test_types.py -v`
Expected: All pass.

- [ ] **Step 5: Commit**

```bash
git add cleaning/types.py tests/cleaning/test_types.py
git commit -m "add cleaning.types: SearchHit, CleaningOutput, CleaningRunReport

Shared dataclasses held in their own module to prevent cyclic imports
between agent.py, escalation.py, and orchestrator.py."
```

---

### Task 6: Build `cleaning/cache.py`

**Files:**
- Create: `cleaning/cache.py`
- Create: `tests/cleaning/test_cache.py`

The web search cache + the underlying Tavily call. Thread-safe from day one (requirement from spec §5.4 + A migration spec §6.1).

- [ ] **Step 1: Write the failing tests**

Create `tests/cleaning/test_cache.py`:

```python
"""Tests for cleaning.cache.WebSearchCache."""
from unittest.mock import MagicMock
import threading
import pytest


def test_normalization_collapses_whitespace_and_case():
    from cleaning.cache import WebSearchCache
    c = WebSearchCache()
    c.put("M6H Toronto Postal", "result1")
    assert c.get("m6h  toronto  postal ") == "result1"


def test_get_returns_none_on_miss():
    from cleaning.cache import WebSearchCache
    c = WebSearchCache()
    assert c.get("never seen") is None


def test_stats_tracks_hits_misses_and_queries(mock_tavily):
    from cleaning.cache import WebSearchCache
    c = WebSearchCache()
    mock_tavily.return_value = "X"
    c.web_search_cached("foo")  # miss
    c.web_search_cached("foo")  # hit
    c.web_search_cached("bar")  # miss
    stats = c.stats()
    assert stats == {"hits": 1, "misses": 2, "queries_cached": 2}


def test_errors_are_not_cached(mock_tavily):
    from cleaning.cache import WebSearchCache
    c = WebSearchCache()
    mock_tavily.return_value = "Web search failed: boom. Query: q"
    c.web_search_cached("q")
    assert c.get("q") is None  # error response not cached
    mock_tavily.return_value = "real result"
    assert c.web_search_cached("q") == "real result"


def test_thread_safety_no_lost_writes(mock_tavily):
    """50 threads all put-and-get; verify no exception and final state is consistent."""
    from cleaning.cache import WebSearchCache
    mock_tavily.side_effect = lambda q, max_results=5: f"r:{q}"
    c = WebSearchCache()
    errors = []
    def worker(i):
        try:
            for j in range(20):
                c.web_search_cached(f"q{i}_{j}")
        except Exception as e:
            errors.append(e)
    threads = [threading.Thread(target=worker, args=(i,)) for i in range(50)]
    for t in threads: t.start()
    for t in threads: t.join()
    assert errors == []
    assert c.stats()["queries_cached"] == 50 * 20
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `python3 -m pytest tests/cleaning/test_cache.py -v`
Expected: FAIL — `cleaning.cache` does not exist.

- [ ] **Step 3: Implement `cleaning/cache.py`**

```python
"""Cached wrapper around the Tavily search API.

Shared across all CleaningAgents and the EscalationAgent within one workflow run.
Thread-safe from day one so the future A migration (parallel agents) does not
require touching this file. See spec §5.4 and A migration spec §6.1.
"""
import json
import os
import re
import threading
import urllib.parse
import urllib.request


_NORMALIZE_RE = re.compile(r"\s+")


def _normalize_query(query: str) -> str:
    """lowercase, collapse whitespace, strip trailing punctuation."""
    q = _NORMALIZE_RE.sub(" ", query.lower().strip())
    return q.rstrip(".,;:!?")


def _is_error_result(result: str) -> bool:
    """Tavily error strings start with 'Web search failed' or 'Error'."""
    return result.startswith("Web search failed") or result.startswith("Error:")


def _tavily_call(query: str, max_results: int = 5) -> str:
    """Hit the Tavily Search API. Returns formatted result string or error string."""
    api_key = os.getenv("TAVILY_API_KEY")
    if not api_key:
        return "Error: TAVILY_API_KEY not set in environment."

    try:
        payload = json.dumps({
            "api_key": api_key,
            "query": query,
            "max_results": max_results,
            "search_depth": "basic",
            "include_answer": True,
        }).encode("utf-8")

        req = urllib.request.Request(
            "https://api.tavily.com/search",
            data=payload,
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        with urllib.request.urlopen(req, timeout=10) as resp:
            data = json.loads(resp.read().decode())

        parts = []
        if data.get("answer"):
            parts.append(f"Summary: {data['answer']}\n")
        for i, r in enumerate(data.get("results", [])[:max_results], 1):
            parts.append(
                f"{i}. {r.get('title', 'No title')}\n"
                f"   {r.get('content', '')[:300]}\n"
                f"   URL: {r.get('url', '')}"
            )
        return "\n".join(parts) if parts else f"No results found for: {query}"
    except Exception as e:
        return f"Web search failed: {e}. Query: {query}"


class WebSearchCache:
    def __init__(self) -> None:
        self._store: dict[str, str] = {}
        self._lock = threading.Lock()
        self._hits = 0
        self._misses = 0

    def get(self, query: str) -> str | None:
        key = _normalize_query(query)
        with self._lock:
            return self._store.get(key)

    def put(self, query: str, result: str) -> None:
        if _is_error_result(result):
            return
        key = _normalize_query(query)
        with self._lock:
            self._store[key] = result

    def web_search_cached(self, query: str, max_results: int = 5) -> str:
        cached = self.get(query)
        if cached is not None:
            with self._lock:
                self._hits += 1
            return cached
        with self._lock:
            self._misses += 1
        result = _tavily_call(query, max_results)
        self.put(query, result)
        return result

    def stats(self) -> dict[str, int]:
        with self._lock:
            return {
                "hits": self._hits,
                "misses": self._misses,
                "queries_cached": len(self._store),
            }
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `python3 -m pytest tests/cleaning/test_cache.py -v`
Expected: All 5 tests pass.

- [ ] **Step 5: Commit**

```bash
git add cleaning/cache.py tests/cleaning/test_cache.py
git commit -m "add cleaning.cache: WebSearchCache with thread-safe lock + tavily call

Dedupes Tavily calls within a workflow run via normalized-query keys.
Threading.Lock guards mutation from day one so the A migration does
not need to touch this file. Errors are not cached so failures retry
on the next request."
```

---

### Task 7: Build `cleaning/llm_client.py`

**Files:**
- Create: `cleaning/llm_client.py`
- Create: `tests/cleaning/test_llm_client.py`

Single env-driven configuration surface for all LLM calls. Three named tiers (`fast`/`standard`/`deep`); each is an `LLMClient` that wraps the Anthropic SDK and adds `cache_control` blocks when supported.

- [ ] **Step 1: Write the failing tests**

Create `tests/cleaning/test_llm_client.py`:

```python
"""Tests for cleaning.llm_client."""
from unittest.mock import MagicMock, patch
import pytest


def test_build_clients_default_all_tiers_use_default_backend(monkeypatch):
    from cleaning.llm_client import build_clients
    monkeypatch.setenv("LLM_BACKEND_DEFAULT", "gpt-oss")
    monkeypatch.setenv("OPENROUTER_API_KEY", "fake")
    monkeypatch.delenv("LLM_BACKEND_FAST", raising=False)
    monkeypatch.delenv("LLM_BACKEND_STANDARD", raising=False)
    monkeypatch.delenv("LLM_BACKEND_DEEP", raising=False)
    clients = build_clients()
    assert clients.fast.model == "openai/gpt-oss-20b:free"
    assert clients.standard.model == "openai/gpt-oss-20b:free"
    assert clients.deep.model == "openai/gpt-oss-20b:free"
    assert clients.fast.supports_cache_control is False


def test_build_clients_per_tier_override(monkeypatch):
    from cleaning.llm_client import build_clients
    monkeypatch.setenv("LLM_BACKEND_DEFAULT", "gpt-oss")
    monkeypatch.setenv("LLM_BACKEND_DEEP", "anthropic-sonnet")
    monkeypatch.setenv("OPENROUTER_API_KEY", "fake")
    monkeypatch.setenv("ANTHROPIC_API_KEY", "fake")
    clients = build_clients()
    assert clients.fast.model == "openai/gpt-oss-20b:free"
    assert clients.deep.model == "claude-sonnet-4-6"
    assert clients.deep.supports_cache_control is True


def test_unknown_backend_raises(monkeypatch):
    from cleaning.llm_client import build_clients
    monkeypatch.setenv("LLM_BACKEND_DEFAULT", "made-up")
    monkeypatch.setenv("OPENROUTER_API_KEY", "fake")
    with pytest.raises(ValueError, match="Unknown LLM backend"):
        build_clients()


def test_messages_create_calls_sdk_with_args():
    from cleaning.llm_client import LLMClient
    sdk = MagicMock()
    sdk.messages.create.return_value = "ok"
    client = LLMClient(sdk=sdk, model="m", supports_cache_control=False, base_url=None)
    result = client.messages_create(
        system="sys", messages=[{"role": "user", "content": "hi"}], tools=[],
    )
    assert result == "ok"
    args, kwargs = sdk.messages.create.call_args
    assert kwargs["model"] == "m"
    assert kwargs["max_tokens"] == 2048
    # Without cache support, system is passed as a plain string.
    assert kwargs["system"] == "sys"


def test_messages_create_adds_cache_control_when_supported():
    from cleaning.llm_client import LLMClient
    sdk = MagicMock()
    sdk.messages.create.return_value = "ok"
    client = LLMClient(sdk=sdk, model="m", supports_cache_control=True, base_url=None)
    client.messages_create(system="sys", messages=[], tools=[{"name": "t"}])
    _, kwargs = sdk.messages.create.call_args
    assert isinstance(kwargs["system"], list)
    assert kwargs["system"][0]["cache_control"] == {"type": "ephemeral"}
    assert kwargs["tools"][-1].get("cache_control") == {"type": "ephemeral"}


def test_messages_create_retries_then_raises():
    from cleaning.llm_client import LLMClient, LLMUnavailableError
    sdk = MagicMock()
    sdk.messages.create.side_effect = ConnectionError("boom")
    client = LLMClient(sdk=sdk, model="m", supports_cache_control=False, base_url=None)
    with pytest.raises(LLMUnavailableError):
        client.messages_create(system="s", messages=[], tools=[])
    assert sdk.messages.create.call_count == 3
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `python3 -m pytest tests/cleaning/test_llm_client.py -v`
Expected: FAIL — module not present.

- [ ] **Step 3: Implement `cleaning/llm_client.py`**

```python
"""Tiered, env-driven LLM client wrapper around the Anthropic SDK.

Three tiers — fast / standard / deep — each independently configurable via env.
The same code runs against gpt-oss-20b:free (OpenRouter), Haiku 4.5 (OpenRouter
or native), and Sonnet/Opus (native) without changes; only env vars differ.

cache_control={"type":"ephemeral"} is added at this layer (on system + tools
blocks) when the backend supports it, so callers don't need to know about caching.
"""
from __future__ import annotations

import logging
import os
import time
from dataclasses import dataclass
from typing import Any

from anthropic import Anthropic


logger = logging.getLogger(__name__)


# Backend token → (model_id, base_url, supports_cache_control)
# When base_url is None the SDK uses the default api.anthropic.com endpoint.
_BACKEND_TABLE = {
    "gpt-oss":          ("openai/gpt-oss-20b:free",       "https://openrouter.ai/api", False),
    "haiku-or":         ("anthropic/claude-haiku-4.5",    "https://openrouter.ai/api", True),
    "anthropic-haiku":  ("claude-haiku-4-5-20251001",     None,                        True),
    "anthropic-sonnet": ("claude-sonnet-4-6",             None,                        True),
    "anthropic-opus":   ("claude-opus-4-7",               None,                        True),
}


_CACHE_THRESHOLD_TOKENS = 4096  # Haiku 4.5 floor; see spec §5.5


class LLMUnavailableError(RuntimeError):
    """Raised when an LLM call fails after retries."""


@dataclass
class LLMClient:
    sdk: Anthropic
    model: str
    supports_cache_control: bool
    base_url: str | None

    def messages_create(
        self,
        *,
        system: str,
        messages: list[dict],
        tools: list[dict],
        max_tokens: int = 2048,
    ) -> Any:
        """Single LLM call surface. Retries 3× with backoff on transient errors."""
        sys_arg, tools_arg = self._apply_cache_control(system, tools)

        last_exc: Exception | None = None
        for attempt in range(3):
            try:
                return self.sdk.messages.create(
                    model=self.model,
                    max_tokens=max_tokens,
                    system=sys_arg,
                    messages=messages,
                    tools=tools_arg,
                )
            except (ConnectionError, TimeoutError) as e:
                last_exc = e
                if attempt < 2:
                    time.sleep(0.5 * (2 ** attempt))
        raise LLMUnavailableError(f"LLM call failed after 3 attempts: {last_exc}")

    def _apply_cache_control(
        self, system: str, tools: list[dict]
    ) -> tuple[Any, list[dict]]:
        """Add cache_control breakpoints to system + final tool when supported."""
        if not self.supports_cache_control:
            return system, tools

        system_blocks = [{"type": "text", "text": system,
                          "cache_control": {"type": "ephemeral"}}]
        if tools:
            tools_with_cache = list(tools)
            last = dict(tools_with_cache[-1])
            last["cache_control"] = {"type": "ephemeral"}
            tools_with_cache[-1] = last
        else:
            tools_with_cache = tools
        return system_blocks, tools_with_cache


@dataclass
class Clients:
    fast:     LLMClient
    standard: LLMClient
    deep:     LLMClient


def _build_one(backend_token: str) -> LLMClient:
    if backend_token not in _BACKEND_TABLE:
        raise ValueError(f"Unknown LLM backend: {backend_token!r}. "
                         f"Valid: {sorted(_BACKEND_TABLE)}")
    model, base_url, cache = _BACKEND_TABLE[backend_token]

    api_key = (os.getenv("OPENROUTER_API_KEY")
               if base_url == "https://openrouter.ai/api"
               else os.getenv("ANTHROPIC_API_KEY"))
    if not api_key:
        env_var = ("OPENROUTER_API_KEY" if base_url == "https://openrouter.ai/api"
                   else "ANTHROPIC_API_KEY")
        raise ValueError(f"{env_var} not set; required for backend {backend_token!r}")

    sdk = Anthropic(base_url=base_url, api_key=api_key) if base_url else Anthropic(api_key=api_key)
    return LLMClient(sdk=sdk, model=model, supports_cache_control=cache, base_url=base_url)


def build_clients() -> Clients:
    """Construct the tiered client bundle from env vars."""
    default = os.getenv("LLM_BACKEND_DEFAULT", "gpt-oss")
    fast     = os.getenv("LLM_BACKEND_FAST", default)
    standard = os.getenv("LLM_BACKEND_STANDARD", default)
    deep     = os.getenv("LLM_BACKEND_DEEP", default)
    return Clients(
        fast=_build_one(fast),
        standard=_build_one(standard),
        deep=_build_one(deep),
    )


def warn_if_under_cache_threshold(client: LLMClient, system: str,
                                  tools: list[dict], tier_name: str) -> None:
    """Startup-time check: warn if cached payload won't reach the 4096-token floor.

    Uses len(text)//4 as a fast token estimate; precise tokenization is not
    worth a tiktoken dependency for a startup warning.
    """
    if not client.supports_cache_control:
        return
    payload = system + str(tools)
    estimated_tokens = len(payload) // 4
    if estimated_tokens < _CACHE_THRESHOLD_TOKENS:
        logger.warning(
            "system+tools for tier %s estimated at ~%d tokens (<%d) — "
            "caching will not engage on Haiku 4.5",
            tier_name, estimated_tokens, _CACHE_THRESHOLD_TOKENS,
        )
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `python3 -m pytest tests/cleaning/test_llm_client.py -v`
Expected: All 6 tests pass.

- [ ] **Step 5: Commit**

```bash
git add cleaning/llm_client.py tests/cleaning/test_llm_client.py
git commit -m "add cleaning.llm_client: tiered Clients + LLMClient with cache_control

Three named tiers (fast/standard/deep) each independently configurable
via LLM_BACKEND_* env vars. cache_control blocks added at the client
layer on system + tools when supports_cache_control=True. Retries 3×
with exponential backoff on transient connection errors; raises
LLMUnavailableError on persistent failure."
```

---

## Phase 3 — Agents

### Task 8: Implement `needs_escalation` predicate in `cleaning/agent.py`

**Files:**
- Create: `cleaning/agent.py` (predicate only for now; class added in Task 9)
- Create: `tests/cleaning/test_agent.py` (predicate tests only for now)

Splitting this off keeps the predicate testable in isolation and forces a clean separation between "decide what's wrong" and "act on it."

- [ ] **Step 1: Write the failing tests**

Create `tests/cleaning/test_agent.py`:

```python
"""Tests for cleaning.agent.

Includes needs_escalation predicate (this task) and CleaningAgent (Task 9).
"""
from cleaning.flags import FlagType
from cleaning.types import CleaningOutput


def _output_with(record: dict) -> CleaningOutput:
    return CleaningOutput(cleaned_record=record)


def test_needs_escalation_unknown_country():
    from cleaning.agent import needs_escalation
    out = _output_with({"country": "", "postal_code": "M5V 1A1", "municipality": "Toronto"})
    assert FlagType.UNKNOWN_COUNTRY in needs_escalation(out)


def test_needs_escalation_postal_unresolved():
    from cleaning.agent import needs_escalation
    out = _output_with({"country": "Canada", "postal_code": "N/A", "municipality": "Toronto"})
    assert FlagType.POSTAL_UNRESOLVED in needs_escalation(out)


def test_needs_escalation_postal_ambiguous():
    from cleaning.agent import needs_escalation
    out = _output_with({"country": "Canada", "postal_code": "M6H ?", "municipality": "Toronto"})
    assert FlagType.POSTAL_AMBIGUOUS in needs_escalation(out)


def test_needs_escalation_municipality_unresolved():
    from cleaning.agent import needs_escalation
    out = _output_with({"country": "Canada", "postal_code": "M6H 1E7", "municipality": "N/A"})
    assert FlagType.MUNICIPALITY_UNRESOLVED in needs_escalation(out)


def test_needs_escalation_low_confidence_in_notes():
    from cleaning.agent import needs_escalation
    out = _output_with({
        "country": "Canada", "postal_code": "M6H 1E7", "municipality": "Toronto",
        "validation_notes": "Confidence: LOW; could not verify",
    })
    assert FlagType.LOW_CONFIDENCE_RESEARCH in needs_escalation(out)


def test_needs_escalation_clean_record_returns_empty():
    from cleaning.agent import needs_escalation
    out = _output_with({
        "country": "Canada", "postal_code": "M6H 1E7", "municipality": "Toronto",
        "validation_notes": "Confidence: HIGH",
    })
    assert needs_escalation(out) == []


def test_needs_escalation_returns_multiple_when_applicable():
    from cleaning.agent import needs_escalation
    out = _output_with({
        "country": "Canada", "postal_code": "N/A", "municipality": "N/A",
    })
    flags = needs_escalation(out)
    assert FlagType.POSTAL_UNRESOLVED in flags
    assert FlagType.MUNICIPALITY_UNRESOLVED in flags
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `python3 -m pytest tests/cleaning/test_agent.py -v`
Expected: FAIL — `cleaning.agent.needs_escalation` does not exist.

- [ ] **Step 3: Implement the predicate in `cleaning/agent.py`**

Create `cleaning/agent.py`:

```python
"""Per-country research agent + escalation predicate.

CleaningAgent class is added in Task 9 of the implementation plan.
"""
from __future__ import annotations

from cleaning.flags import FlagType
from cleaning.types import CleaningOutput


_VALID_COUNTRIES_FULL = {"Canada", "United States", "Netherlands", "Mexico", "Japan"}
_VALID_COUNTRIES_CODE = {"CA", "USA", "NL", "MX", "JP"}


def needs_escalation(output: CleaningOutput) -> list[FlagType]:
    """Decide which (if any) flags should trigger an escalation pass for this record.

    Pure function over the record + validation_notes. Returns a list of FlagType —
    empty means the record is fully resolved and does not need escalation.
    """
    rec = output.cleaned_record
    flags: list[FlagType] = []

    country = (rec.get("country") or "").strip()
    if (not country
        or country not in _VALID_COUNTRIES_FULL
           and country.upper() not in _VALID_COUNTRIES_CODE):
        flags.append(FlagType.UNKNOWN_COUNTRY)

    postal = (rec.get("postal_code") or "").strip()
    if not postal or postal.upper() == "N/A":
        flags.append(FlagType.POSTAL_UNRESOLVED)
    elif postal.endswith("?"):
        flags.append(FlagType.POSTAL_AMBIGUOUS)

    muni = (rec.get("municipality") or "").strip()
    if not muni or muni.upper() == "N/A":
        flags.append(FlagType.MUNICIPALITY_UNRESOLVED)

    notes = (rec.get("validation_notes") or "").upper()
    if "LOW" in notes and "CONFIDENCE" in notes:
        flags.append(FlagType.LOW_CONFIDENCE_RESEARCH)

    # TODO: add CROSS_REGION_MISMATCH detection (e.g. Canadian postal first letter
    # doesn't match province) when a postal-pattern library is available. The
    # FlagType value is already defined; implement as a follow-up task once a
    # lightweight CA/USA/NL/MX/JP postal-format validator is chosen.

    return flags
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `python3 -m pytest tests/cleaning/test_agent.py -v`
Expected: 7 tests pass.

- [ ] **Step 5: Commit**

```bash
git add cleaning/agent.py tests/cleaning/test_agent.py
git commit -m "add cleaning.agent.needs_escalation predicate

Pure function: takes a CleaningOutput, returns the list of FlagTypes
that should escalate. Tested in isolation against the canonical
unresolved/ambiguous/low-confidence cases per spec §5.2."
```

---

### Task 9: Implement `CleaningAgent` class

**Files:**
- Modify: `cleaning/agent.py` (add the class)
- Modify: `tests/cleaning/test_agent.py` (add CleaningAgent tests)

This is the migration boundary. Country is fixed at construction time. Each instance owns its own `messages` and `search_log`; never reaches into shared state. The agent calls `self.escalator.investigate(...)` internally when `needs_escalation` returns non-empty for any record (per spec §6 and patched §6 step 5).

- [ ] **Step 1: Write the failing tests for CleaningAgent**

Append to `tests/cleaning/test_agent.py`:

```python
# ---- CleaningAgent tests ----

import pytest
from unittest.mock import MagicMock


def _mock_response(text=None, tool_calls=None):
    """Build a fake Anthropic-style response."""
    resp = MagicMock()
    resp.content = []
    if text is not None:
        block = MagicMock()
        block.type = "text"
        block.text = text
        del block.name  # so "hasattr(b, 'name')" tests don't pass for text blocks
        resp.content.append(block)
    if tool_calls:
        for name, inp, tid in tool_calls:
            block = MagicMock()
            block.type = "tool_use"
            block.name = name
            block.input = inp
            block.id = tid
            resp.content.append(block)
    resp.stop_reason = "tool_use" if tool_calls else "end_turn"
    return resp


def test_cleaning_agent_no_research_needed_returns_pre_cleaned(mock_llm):
    """If a record arrives with all fields resolved, agent returns it untouched."""
    from cleaning.agent import CleaningAgent
    from cleaning.cache import WebSearchCache
    escalator = MagicMock()
    escalator.investigate.return_value = None  # not called

    agent = CleaningAgent(
        country_code="CA",
        system_prompt="sys",
        research_prompt_builder=lambda c, t: "research please",
        tools=[{"name": "web_search"}],
        llm_client=mock_llm,
        web_cache=WebSearchCache(),
        escalator=escalator,
    )
    # Model returns a 4-column table parsed by orchestrator.parse_research_response
    # but here we test the simpler path: pre-cleaned record needs no research
    mock_llm.messages_create.return_value = _mock_response(
        text="| ID | Postal Code | Municipality | Validation Notes |\n"
             "| 1 | M6H 1E7 | The Annex | Confidence: HIGH |"
    )
    record = {"id": 1, "country": "Canada", "postal_code": "M6H 1E7", "municipality": "The Annex"}
    outputs = agent.process([record])
    assert len(outputs) == 1
    assert outputs[0].cleaned_record["id"] == 1
    escalator.investigate.assert_not_called()


def test_cleaning_agent_calls_web_search_via_cache(mock_llm, mock_tavily):
    """Tool-use loop dispatches web_search through the cache."""
    from cleaning.agent import CleaningAgent
    from cleaning.cache import WebSearchCache
    cache = WebSearchCache()
    escalator = MagicMock()
    mock_tavily.return_value = "search result"

    # First response: model calls web_search; second response: model finishes with table.
    mock_llm.messages_create.side_effect = [
        _mock_response(tool_calls=[("web_search", {"query": "M6H neighbourhood"}, "t1")]),
        _mock_response(text="| ID | Postal Code | Municipality | Validation Notes |\n"
                            "| 7 | M6H 1E7 | The Annex | HIGH |"),
    ]
    agent = CleaningAgent(
        country_code="CA", system_prompt="sys",
        research_prompt_builder=lambda c, t: "research",
        tools=[{"name": "web_search"}],
        llm_client=mock_llm, web_cache=cache, escalator=escalator,
    )
    record = {"id": 7, "country": "Canada", "postal_code": "M6H", "municipality": "N/A",
              "address": "25 Muir Ave", "city": "Toronto", "state_province": "Ontario"}
    outputs = agent.process([record])
    assert len(outputs) == 1
    mock_tavily.assert_called_once()
    assert cache.stats()["misses"] == 1


def test_cleaning_agent_escalates_unresolved_record(mock_llm):
    """A record that comes back with N/A municipality triggers escalator.investigate."""
    from cleaning.agent import CleaningAgent
    from cleaning.cache import WebSearchCache
    from cleaning.types import CleaningOutput
    from cleaning.flags import Flag, FlagType, FlagSeverity

    escalator = MagicMock()
    escalator.investigate.return_value = CleaningOutput(
        cleaned_record={"id": 9, "country": "Canada", "postal_code": "M6H 1E7",
                        "municipality": "The Annex", "validation_notes": "resolved"},
        flags=[Flag(FlagType.RESOLVED_AFTER_ESCALATION, FlagSeverity.INFO,
                    "agent escalated and resolved", "escalator")],
    )
    mock_llm.messages_create.return_value = _mock_response(
        text="| ID | Postal Code | Municipality | Validation Notes |\n"
             "| 9 | N/A | N/A | LOW could not resolve |"
    )
    agent = CleaningAgent(
        country_code="CA", system_prompt="sys",
        research_prompt_builder=lambda c, t: "research",
        tools=[{"name": "web_search"}],
        llm_client=mock_llm, web_cache=WebSearchCache(), escalator=escalator,
    )
    record = {"id": 9, "country": "Canada", "postal_code": "M6H", "municipality": "N/A",
              "address": "25 Muir Ave", "city": "Toronto", "state_province": "Ontario"}
    outputs = agent.process([record])
    escalator.investigate.assert_called_once()
    assert outputs[0].cleaned_record["municipality"] == "The Annex"
    assert any(f.flag_type == FlagType.RESOLVED_AFTER_ESCALATION for f in outputs[0].flags)


def test_cleaning_agent_max_rounds_rescue(mock_llm):
    """If model loops past max_rounds, agent falls back to force-final-output."""
    from cleaning.agent import CleaningAgent
    from cleaning.cache import WebSearchCache

    # Model keeps calling tools forever until force-final.
    mock_llm.messages_create.side_effect = (
        [_mock_response(tool_calls=[("web_search", {"query": "x"}, f"t{i}")])
         for i in range(5)]
        + [_mock_response(text="| ID | Postal Code | Municipality | Validation Notes |\n"
                               "| 1 | M6H 1E7 | The Annex | LOW |")]
    )
    agent = CleaningAgent(
        country_code="CA", system_prompt="sys",
        research_prompt_builder=lambda c, t: "research",
        tools=[{"name": "web_search"}],
        llm_client=mock_llm, web_cache=WebSearchCache(),
        escalator=MagicMock(), max_rounds=3,
    )
    record = {"id": 1, "country": "Canada", "postal_code": "M6H", "municipality": "N/A"}
    outputs = agent.process([record])
    # Expect 3 rounds + 1 final force = 4 calls total
    assert mock_llm.messages_create.call_count == 4
    assert outputs  # got an output despite the rescue
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `python3 -m pytest tests/cleaning/test_agent.py -v`
Expected: 7 predicate tests pass; 4 new CleaningAgent tests fail (`CleaningAgent` not defined).

- [ ] **Step 3: Implement `CleaningAgent` class**

Append to `cleaning/agent.py`:

```python
import logging
from typing import Callable

from cleaning.cache import WebSearchCache
from cleaning.escalation import EscalationAgent  # Task 10 creates this
from cleaning.flags import Flag, FlagSeverity
from cleaning.llm_client import LLMClient
from cleaning.types import SearchHit


logger = logging.getLogger(__name__)


class CleaningAgent:
    """Country-fixed research agent. See spec §5.1 and migration spec §5.

    Migration boundary invariants enforced here:
      1. country_code is fixed at construction; .process() never inspects records to
         decide "what country am I serving?".
      2. messages, _search_log, and counters are per-instance; nothing shared.
      3. .process() returns CleaningOutputs; never writes to the DB.
    """

    def __init__(
        self,
        country_code: str,
        system_prompt: str,
        research_prompt_builder: Callable[[str, str], str],
        tools: list[dict],
        llm_client: LLMClient,
        web_cache: WebSearchCache,
        escalator: "EscalationAgent",
        max_rounds: int = 20,
    ):
        self.country_code = country_code
        self.system_prompt = system_prompt
        self.research_prompt_builder = research_prompt_builder
        self.tools = tools
        self.llm = llm_client
        self.cache = web_cache
        self.escalator = escalator
        self.max_rounds = max_rounds
        self._search_log: list[SearchHit] = []

    # ------------------------------------------------------------------ public

    def process(self, records: list[dict]) -> list[CleaningOutput]:
        """Run the research loop for one country's batch, escalate hard cases."""
        if not records:
            return []

        research_table = self._format_research_batch(records)
        prompt = self.research_prompt_builder(self.country_code, research_table)
        raw_response = self._run_research_loop(prompt)
        parsed = self._parse_research_table(raw_response)

        outputs: list[CleaningOutput] = []
        for rec in records:
            merged = dict(rec)
            update = parsed.get(rec["id"], {})
            for k in ("postal_code", "municipality", "validation_notes"):
                if update.get(k):
                    merged[k] = update[k]
            out = CleaningOutput(
                cleaned_record=merged,
                flags=[],
                search_log=list(self._search_log),
            )
            flag_hints = needs_escalation(out)
            if flag_hints:
                escalated = self.escalator.investigate(
                    record=merged,
                    country_code=self.country_code,
                    flag_hints=flag_hints,
                    prior_search_log=self._search_log,
                )
                if escalated is not None:
                    out = escalated
            outputs.append(out)
        return outputs

    # ------------------------------------------------------------------ internal

    def _run_research_loop(self, prompt: str) -> str:
        """Tool-use loop with rescue path. Returns the final text response."""
        messages: list[dict] = [{"role": "user", "content": prompt}]

        for round_num in range(self.max_rounds):
            resp = self.llm.messages_create(
                system=self.system_prompt,
                messages=messages,
                tools=self.tools,
            )
            tool_calls = [b for b in resp.content
                          if hasattr(b, "type") and b.type == "tool_use"]
            if not tool_calls:
                for b in resp.content:
                    if hasattr(b, "text"):
                        return b.text
                return ""

            messages.append({"role": "assistant", "content": resp.content})
            tool_results = []
            for tc in tool_calls:
                result = self._execute_tool(tc.name, tc.input)
                tool_results.append({
                    "type": "tool_result", "tool_use_id": tc.id, "content": result,
                })
            messages.append({"role": "user", "content": tool_results})

        # Rescue path — force final output
        logger.warning("CleaningAgent[%s]: hit max_rounds=%d, forcing final output",
                       self.country_code, self.max_rounds)
        messages.append({"role": "user", "content":
            "Research complete. Now return ONLY the table: "
            "| ID | Postal Code | Municipality | Validation Notes |"})
        final = self.llm.messages_create(
            system=self.system_prompt, messages=messages, tools=self.tools,
        )
        for b in final.content:
            if hasattr(b, "text"):
                return b.text
        return ""

    def _execute_tool(self, name: str, args: dict) -> str:
        if name == "web_search":
            query = args.get("query", "")
            result = self.cache.web_search_cached(query, args.get("max_results", 5))
            self._search_log.append(SearchHit(query=query, result=result))
            return result
        return f"Unknown tool: {name}"

    def _format_research_batch(self, records: list[dict]) -> str:
        """Same shape used by the research prompt — see spec §5.1 _format_research_batch."""
        import re
        headers = ["ID", "Name", "Address", "City", "Postal Code", "State/Prov", "Country", "Issue"]
        rows = ["| " + " | ".join(headers) + " |",
                "|" + "|".join(["---"] * len(headers)) + "|"]
        for r in records:
            postal = (r.get("postal_code") or "").strip()
            muni = (r.get("municipality") or "").strip()
            postal_chars = re.sub(r"[\s\-]", "", postal)
            issues = []
            if not muni or muni.upper() == "N/A":
                issues.append("municipality missing")
            if not postal_chars or len(postal_chars) < 5:
                issues.append("postal incomplete" if postal_chars else "postal missing")
            rows.append("| " + " | ".join([
                str(r["id"]), r.get("name", ""), r.get("address", ""), r.get("city", ""),
                postal or "N/A", r.get("state_province", ""), r.get("country", ""),
                "; ".join(issues),
            ]) + " |")
        return "\n".join(rows)

    def _parse_research_table(self, response: str) -> dict[int, dict]:
        """Parse 4-column research table response. Same logic as spec §5.1."""
        results: dict[int, dict] = {}
        for line in response.strip().split("\n"):
            if not line.strip().startswith("|") or "---" in line:
                continue
            parts = [p.strip() for p in line.split("|")[1:-1]]
            if len(parts) < 3:
                continue
            try:
                rid = int(parts[0])
            except ValueError:
                continue
            results[rid] = {
                "postal_code": parts[1] if parts[1] not in ("N/A", "") else None,
                "municipality": parts[2] if parts[2] not in ("N/A", "") else None,
                "validation_notes": parts[3] if len(parts) > 3 else "",
            }
        return results
```

- [ ] **Step 4: Run tests to verify all agent tests pass**

Run: `python3 -m pytest tests/cleaning/test_agent.py -v`
Expected: All 11 tests pass (7 predicate + 4 class).

> Note: this task imports `cleaning.escalation.EscalationAgent` which does not yet exist. The import is only at type-check time. **If running this task before Task 10, change the import to `from typing import TYPE_CHECKING` guard:**
>
> ```python
> from typing import TYPE_CHECKING
> if TYPE_CHECKING:
>     from cleaning.escalation import EscalationAgent
> ```
>
> Then quote the type in the constructor signature: `escalator: "EscalationAgent"`. The provided code already uses the string-quoted form, so the actual import only matters if the test instantiates `CleaningAgent` directly with a real `EscalationAgent`. The tests use `MagicMock` for escalator, so the import can stay forward-referenced. **Recommended: skip the `from cleaning.escalation import EscalationAgent` line entirely until Task 10.**

- [ ] **Step 5: Commit**

```bash
git add cleaning/agent.py tests/cleaning/test_agent.py
git commit -m "add CleaningAgent: country-fixed research agent with escalation hand-off

Implements the migration-boundary invariants from spec §5.1 and migration
spec §5: country fixed at construction, no shared state, never writes to DB.
Tool-use loop dispatches web_search through the shared cache and records
hits in self._search_log for hand-off to the escalator. Calls
self.escalator.investigate() inline whenever needs_escalation returns
flags for a record."
```

---

### Task 10: Implement `EscalationAgent`

**Files:**
- Create: `cleaning/escalation.py`
- Create: `tests/cleaning/test_escalation.py`

The hard-case investigator. Per-record (not per-batch). Receives the parent's `prior_search_log` so it doesn't redo searches. Uses `clients.deep` (the strongest tier).

- [ ] **Step 1: Write the failing tests**

Create `tests/cleaning/test_escalation.py`:

```python
"""Tests for cleaning.escalation.EscalationAgent."""
from unittest.mock import MagicMock


def _mock_response(text=None, tool_calls=None):
    resp = MagicMock()
    resp.content = []
    if text is not None:
        block = MagicMock()
        block.type = "text"
        block.text = text
        del block.name
        resp.content.append(block)
    if tool_calls:
        for name, inp, tid in tool_calls:
            block = MagicMock()
            block.type = "tool_use"
            block.name = name
            block.input = inp
            block.id = tid
            resp.content.append(block)
    resp.stop_reason = "tool_use" if tool_calls else "end_turn"
    return resp


def test_escalation_unknown_country_resolves(mock_llm):
    """Escalator receives a record with no country, resolves it via web search."""
    from cleaning.escalation import EscalationAgent
    from cleaning.cache import WebSearchCache
    from cleaning.flags import FlagType
    mock_llm.messages_create.return_value = _mock_response(
        text='{"country": "Canada", "postal_code": "M6H 1E7", '
             '"municipality": "The Annex", "validation_notes": "resolved by escalation"}'
    )
    esc = EscalationAgent(llm_client=mock_llm, web_cache=WebSearchCache(),
                          tools=[{"name": "web_search"}])
    record = {"id": 5, "country": "", "postal_code": "M6H 1E7",
              "municipality": "The Annex", "address": "25 Muir Ave", "city": "Toronto"}
    out = esc.investigate(
        record=record, country_code=None,
        flag_hints=[FlagType.UNKNOWN_COUNTRY], prior_search_log=[],
    )
    assert out.cleaned_record["country"] == "Canada"
    # Successful resolution adds RESOLVED_AFTER_ESCALATION
    assert any(f.flag_type == FlagType.RESOLVED_AFTER_ESCALATION for f in out.flags)


def test_escalation_failure_persists_flags(mock_llm):
    """If escalation cannot resolve, the original hint flags are preserved."""
    from cleaning.escalation import EscalationAgent
    from cleaning.cache import WebSearchCache
    from cleaning.flags import FlagType
    mock_llm.messages_create.return_value = _mock_response(
        text='{"country": "Canada", "postal_code": "N/A", "municipality": "N/A", '
             '"validation_notes": "could not resolve"}'
    )
    esc = EscalationAgent(llm_client=mock_llm, web_cache=WebSearchCache(),
                          tools=[{"name": "web_search"}])
    record = {"id": 5, "country": "Canada", "postal_code": "N/A",
              "municipality": "N/A", "address": "x", "city": "y"}
    out = esc.investigate(
        record=record, country_code="CA",
        flag_hints=[FlagType.POSTAL_UNRESOLVED, FlagType.MUNICIPALITY_UNRESOLVED],
        prior_search_log=[],
    )
    flag_types = {f.flag_type for f in out.flags}
    assert FlagType.POSTAL_UNRESOLVED in flag_types
    assert FlagType.MUNICIPALITY_UNRESOLVED in flag_types


def test_escalation_does_not_redo_prior_searches(mock_llm, mock_tavily):
    """Prior search results are passed in messages — escalator should not re-fire them."""
    from cleaning.escalation import EscalationAgent
    from cleaning.cache import WebSearchCache
    from cleaning.flags import FlagType
    from cleaning.types import SearchHit

    cache = WebSearchCache()
    mock_tavily.return_value = "fresh result"
    # Pre-populate cache with the prior query so a re-fire would be a HIT not a MISS.
    cache.put("M6H Toronto postal", "prior-result")
    mock_llm.messages_create.return_value = _mock_response(
        text='{"country": "Canada", "postal_code": "M6H 1E7", '
             '"municipality": "The Annex", "validation_notes": "ok"}'
    )
    esc = EscalationAgent(llm_client=mock_llm, web_cache=cache,
                          tools=[{"name": "web_search"}])
    prior = [SearchHit("M6H Toronto postal", "prior-result")]
    esc.investigate(
        record={"id": 1, "country": "Canada"},
        country_code="CA", flag_hints=[FlagType.POSTAL_UNRESOLVED],
        prior_search_log=prior,
    )
    # Verify the prior search log was passed into the system or message context.
    # We check by inspecting what was sent to messages_create.
    args, kwargs = mock_llm.messages_create.call_args
    messages_sent = kwargs["messages"]
    flat_text = str(messages_sent)
    assert "M6H Toronto postal" in flat_text or "prior-result" in flat_text
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `python3 -m pytest tests/cleaning/test_escalation.py -v`
Expected: FAIL — `cleaning.escalation` does not exist.

- [ ] **Step 3: Implement `cleaning/escalation.py`**

```python
"""Per-record escalation sub-agent for hard cases.

Triggered by CleaningAgent when needs_escalation() returns non-empty for a record.
Receives the parent's prior_search_log so it does not redo searches.
Returns an updated CleaningOutput with flags. Does NOT touch the DB.
See spec §5.2.
"""
from __future__ import annotations

import json
import logging
from typing import Optional

from cleaning.cache import WebSearchCache
from cleaning.flags import Flag, FlagSeverity, FlagType
from cleaning.llm_client import LLMClient
from cleaning.types import CleaningOutput, SearchHit


logger = logging.getLogger(__name__)


_SYSTEM_PROMPT = """You are an escalation specialist for a real-estate data cleaning system.
You receive ONE record that the country agent could not fully resolve, plus the
list of issues to investigate and a transcript of prior web searches.

Your job:
- Resolve the issues if you can. Use web_search ONLY for queries not in the prior log.
- If you cannot resolve, return your best guess and explicitly state the confidence.
- Return ONLY a JSON object with the fields: country, postal_code, municipality, validation_notes.
"""


class EscalationAgent:
    def __init__(
        self,
        llm_client: LLMClient,
        web_cache: WebSearchCache,
        tools: list[dict],
        max_rounds: int = 10,
    ):
        self.llm = llm_client
        self.cache = web_cache
        self.tools = tools
        self.max_rounds = max_rounds

    def investigate(
        self,
        record: dict,
        country_code: Optional[str],
        flag_hints: list[FlagType],
        prior_search_log: list[SearchHit],
    ) -> CleaningOutput:
        prompt = self._build_prompt(record, country_code, flag_hints, prior_search_log)
        messages: list[dict] = [{"role": "user", "content": prompt}]

        for _ in range(self.max_rounds):
            resp = self.llm.messages_create(
                system=_SYSTEM_PROMPT, messages=messages, tools=self.tools,
            )
            tool_calls = [b for b in resp.content
                          if hasattr(b, "type") and b.type == "tool_use"]
            if not tool_calls:
                final_text = next((b.text for b in resp.content if hasattr(b, "text")), "")
                return self._build_output(record, final_text, flag_hints)
            messages.append({"role": "assistant", "content": resp.content})
            tool_results = []
            for tc in tool_calls:
                result = self.cache.web_search_cached(tc.input.get("query", ""))
                tool_results.append({"type": "tool_result", "tool_use_id": tc.id,
                                     "content": result})
            messages.append({"role": "user", "content": tool_results})

        logger.warning("EscalationAgent: max_rounds reached on record %s", record.get("id"))
        return self._build_output(record, "", flag_hints)

    def _build_prompt(self, record, country_code, flag_hints, prior_search_log):
        prior = "\n".join(f"  query: {s.query}\n  result snippet: {s.result[:200]}..."
                          for s in prior_search_log) or "(none)"
        return (
            f"COUNTRY (may be unknown): {country_code}\n\n"
            f"RECORD:\n{json.dumps(record, indent=2)}\n\n"
            f"ISSUES TO RESOLVE: {[ft.value for ft in flag_hints]}\n\n"
            f"PRIOR SEARCHES (do NOT repeat these queries):\n{prior}\n\n"
            f"Resolve the issues. Return ONLY the JSON object."
        )

    def _build_output(
        self, record: dict, final_text: str, flag_hints: list[FlagType],
    ) -> CleaningOutput:
        merged = dict(record)
        try:
            parsed = json.loads(final_text.strip())
            for k in ("country", "postal_code", "municipality", "validation_notes"):
                if parsed.get(k):
                    merged[k] = parsed[k]
        except (json.JSONDecodeError, ValueError):
            logger.warning("EscalationAgent: could not parse JSON: %r", final_text[:200])

        flags = self._build_flags(merged, flag_hints)
        return CleaningOutput(cleaned_record=merged, flags=flags)

    def _build_flags(self, merged: dict, flag_hints: list[FlagType]) -> list[Flag]:
        """Decide which hint-flags survived (still unresolved) vs which were resolved."""
        from cleaning.agent import needs_escalation
        survivors = set(needs_escalation(CleaningOutput(cleaned_record=merged)))
        flags: list[Flag] = []
        any_resolved = False
        for hint in flag_hints:
            if hint in survivors:
                flags.append(Flag(
                    flag_type=hint,
                    severity=FlagSeverity.NEEDS_REVIEW,
                    reason=f"escalation could not resolve {hint.value}",
                    raised_by="escalator",
                ))
            else:
                any_resolved = True
        if any_resolved:
            flags.append(Flag(
                flag_type=FlagType.RESOLVED_AFTER_ESCALATION,
                severity=FlagSeverity.INFO,
                reason="record resolved by escalation",
                raised_by="escalator",
            ))
        return flags
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `python3 -m pytest tests/cleaning/test_escalation.py tests/cleaning/test_agent.py -v`
Expected: All escalation + all agent tests pass.

- [ ] **Step 5: Commit**

```bash
git add cleaning/escalation.py tests/cleaning/test_escalation.py
git commit -m "add EscalationAgent: per-record investigator for hard cases

Receives prior_search_log to avoid re-firing searches, expects JSON
output, builds flags based on which hints survived (unresolved →
NEEDS_REVIEW flag; any resolved → RESOLVED_AFTER_ESCALATION INFO flag)."
```

---

## Phase 4 — Orchestrator + Public API

### Task 11: Build orchestrator helpers

**Files:**
- Create: `cleaning/orchestrator.py` (helpers only; main `run_cleaning_workflow` in Task 12)
- Create: `tests/cleaning/test_orchestrator.py` (helper tests only for now)

Five helpers — pure functions, easy to unit test:
1. `interpret_query(query) -> dict` — replaces today's `data_cleaning_agent.interpret_user_query`
2. `detect_country_filter(query, override) -> str | None` — single source of truth (replaces today's three duplicates)
3. `fetch_records(db_path, filters) -> list[dict]`
4. `group_by_country(records) -> dict[str|None, list[dict]]`
5. `merge_results(pre_cleaned, agent_outputs) -> list[dict]`

- [ ] **Step 1: Write the failing tests**

Create `tests/cleaning/test_orchestrator.py`:

```python
"""Tests for cleaning.orchestrator helpers + run_cleaning_workflow.

Helper tests added in Task 11; workflow tests added in Task 12.
"""


def test_detect_country_filter_explicit_override():
    from cleaning.orchestrator import detect_country_filter
    assert detect_country_filter("anything", override="USA") == "USA"


def test_detect_country_filter_keyword_canada():
    from cleaning.orchestrator import detect_country_filter
    assert detect_country_filter("CLEAN canadian data") == "CA"


def test_detect_country_filter_ambiguous_returns_none():
    from cleaning.orchestrator import detect_country_filter
    assert detect_country_filter("CLEAN all uncleaned data") is None


def test_detect_country_filter_north_american_returns_none_for_per_record_routing():
    from cleaning.orchestrator import detect_country_filter
    assert detect_country_filter("CLEAN north american data") is None


def test_interpret_query_extracts_country_and_scope():
    from cleaning.orchestrator import interpret_query
    f = interpret_query("CLEAN japanese data")
    assert f.get("country") == "JP"
    f2 = interpret_query("CLEAN all uncleaned data")
    assert f2.get("scope") == "all_uncleaned"


def test_group_by_country_uses_pre_cleaner_canonical_code():
    from cleaning.orchestrator import group_by_country
    records = [
        {"id": 1, "country": "Canada"},
        {"id": 2, "country": "United States"},
        {"id": 3, "country": "CA"},
        {"id": 4, "country": ""},
        {"id": 5, "country": "Atlantis"},  # unknown
    ]
    g = group_by_country(records)
    assert {1, 3} == {r["id"] for r in g["CA"]}
    assert {2} == {r["id"] for r in g["USA"]}
    # records with no resolvable country code go under None
    assert {4, 5} == {r["id"] for r in g[None]}


def test_merge_results_combines_pre_cleaned_with_agent_output():
    from cleaning.orchestrator import merge_results
    from cleaning.types import CleaningOutput
    pre = [{"id": 1, "name": "John", "_pre_clean_changes": ["name capitalized"]}]
    outs = [CleaningOutput(cleaned_record={"id": 1, "postal_code": "M6H 1E7",
                                            "municipality": "The Annex",
                                            "validation_notes": "HIGH"})]
    merged = merge_results(pre, outs)
    assert merged[0]["raw_data_id"] == 1
    assert "Pre-cleaned" in merged[0]["validation_notes"]
    assert merged[0]["postal_code"] == "M6H 1E7"


def test_fetch_records_filters_by_country(tmp_db):
    from db_helpers import insert_raw_data
    from cleaning.orchestrator import fetch_records
    insert_raw_data(tmp_db, name="alice", country="Canada")
    insert_raw_data(tmp_db, name="bob", country="United States")
    insert_raw_data(tmp_db, name="carol", country="CA")
    canadian = fetch_records(tmp_db, filters={"country": "CA"})
    assert {r["name"] for r in canadian} == {"alice", "carol"}


def test_fetch_records_excludes_already_cleaned(tmp_db):
    """scope=all_uncleaned must not return records that already have a cleaned row."""
    import sqlite3
    from db_helpers import insert_raw_data
    from cleaning.orchestrator import fetch_records
    raw_id_1 = insert_raw_data(tmp_db, name="already_done", country="CA")
    insert_raw_data(tmp_db, name="still_dirty", country="CA")
    # simulate a pre-existing cleaned_data row for record 1
    conn = sqlite3.connect(tmp_db)
    conn.execute(
        "INSERT INTO cleaned_data (raw_data_id, name, country) VALUES (?, ?, ?)",
        (raw_id_1, "already_done", "Canada"),
    )
    conn.commit()
    conn.close()
    result = fetch_records(tmp_db, filters={"scope": "all_uncleaned"})
    assert all(r["name"] != "already_done" for r in result)
    assert any(r["name"] == "still_dirty" for r in result)
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `python3 -m pytest tests/cleaning/test_orchestrator.py -v`
Expected: FAIL — `cleaning.orchestrator` does not exist.

- [ ] **Step 3: Implement `cleaning/orchestrator.py` (helpers only)**

```python
"""Orchestrator for the cleaning workflow.

This module's responsibilities:
- Interpret the user query into filters
- Fetch matching raw records
- Pre-clean (deterministic)
- Group records by country (per-record auto-routing)
- Dispatch each group to a CleaningAgent
- Merge results
- Persist to cleaned_data + flags + audit_log
- Return a CleaningRunReport

The main entrypoint run_cleaning_workflow() is added in Task 12 of the plan;
this file currently exposes the helpers needed by it.
"""
from __future__ import annotations

import logging
from typing import Optional

from cleaning.flags import Flag
from cleaning.pre_cleaner import get_country_code, pre_clean_record, needs_research
from cleaning.types import CleaningOutput
from db_helpers import query_records
from validate_data_quality import get_records_needing_cleaning


logger = logging.getLogger(__name__)


_KEYWORD_TO_COUNTRY = {
    "canadian": "CA", "canada": "CA",
    "american": "USA", "usa": "USA", "united states": "USA", "u.s.": "USA",
    "dutch": "NL", "netherlands": "NL", "holland": "NL",
    "mexican": "MX", "mexico": "MX",
    "japanese": "JP", "japan": "JP",
}


def detect_country_filter(query: str, override: Optional[str] = None) -> Optional[str]:
    """Return canonical country code if the query unambiguously names one country.

    `override` wins over keyword detection. "north american" / "european" /
    "all uncleaned" return None (per-record auto-routing).
    """
    if override:
        return override
    q = (query or "").lower()
    if "north american" in q or "european" in q or "all" in q or "uncleaned" in q:
        return None
    matched = {code for kw, code in _KEYWORD_TO_COUNTRY.items() if kw in q}
    return matched.pop() if len(matched) == 1 else None


def interpret_query(query: str) -> dict:
    """Parse the user query into a filters dict (country, scope, limit).

    Uses Python keyword regex rather than an LLM call — fast, free, and
    sufficient for the simple trigger vocabulary. An LLM upgrade path exists
    if query complexity grows beyond keyword matching.
    """
    q = (query or "").lower()
    filters: dict = {}
    code = detect_country_filter(query)
    if code:
        filters["country"] = code
    if "all" in q or "uncleaned" in q or "dirty" in q:
        filters["scope"] = "all_uncleaned"
    if "first" in q:
        filters["scope"] = "first_batch"
        filters["limit"] = 5
    return filters


def fetch_records(db_path: str, filters: dict) -> list[dict]:
    """Fetch raw records from DB filtered by country / scope.

    Country filtering uses pre_cleaner.get_country_code so that 'Canada', 'CA',
    'CDN', etc. all match the same canonical code.

    When filters["scope"] == "all_uncleaned", only records that do NOT already
    have a cleaned_data row are returned. This prevents re-processing on queries
    like "CLEAN all uncleaned data".
    """
    import sqlite3
    records = query_records(db_path, table="raw_data", filters={}, limit=50)

    if filters.get("scope") == "all_uncleaned":
        conn = sqlite3.connect(db_path)
        try:
            already_cleaned = {
                row[0] for row in conn.execute(
                    "SELECT DISTINCT raw_data_id FROM cleaned_data"
                ).fetchall()
            }
        finally:
            conn.close()
        records = [r for r in records if r["id"] not in already_cleaned]

    if "country" in filters:
        target = filters["country"]
        records = [r for r in records if get_country_code(r.get("country", "")) == target]
    if "limit" in filters:
        records = records[: filters["limit"]]
    return records


def group_by_country(records: list[dict]) -> dict[Optional[str], list[dict]]:
    """Group records by canonical country code. Unknown country → key None."""
    groups: dict[Optional[str], list[dict]] = {}
    for r in records:
        code = get_country_code(r.get("country") or "")
        groups.setdefault(code, []).append(r)
    return groups


def merge_results(
    pre_cleaned: list[dict], agent_outputs: list[CleaningOutput],
) -> list[dict]:
    """Combine pre-clean changes with agent/escalation output for persistence.

    Returns a list of dicts ready for insert_cleaned_data + audit_log generation.
    """
    by_id = {out.cleaned_record["id"]: out for out in agent_outputs}
    merged: list[dict] = []
    for record in pre_cleaned:
        out = by_id.get(record["id"])
        result = dict(record)
        result["raw_data_id"] = record["id"]
        if out is not None:
            for k in ("postal_code", "municipality", "validation_notes",
                      "country", "state_province"):
                if out.cleaned_record.get(k):
                    result[k] = out.cleaned_record[k]
        # Combine pre-clean change log with agent's notes
        pre_changes = record.get("_pre_clean_changes", [])
        existing_notes = result.get("validation_notes", "")
        parts = []
        if pre_changes:
            parts.append("Pre-cleaned: " + "; ".join(pre_changes))
        if existing_notes:
            parts.append(existing_notes)
        result["validation_notes"] = " | ".join(parts) if parts else ""
        result["_flags"] = out.flags if out else []
        merged.append(result)
    return merged
```

- [ ] **Step 4: Run helper tests, verify they pass**

Run: `python3 -m pytest tests/cleaning/test_orchestrator.py -v`
Expected: All 8 helper tests pass.

- [ ] **Step 5: Commit**

```bash
git add cleaning/orchestrator.py tests/cleaning/test_orchestrator.py
git commit -m "add cleaning.orchestrator helpers: interpret/detect/fetch/group/merge

Five pure helper functions used by run_cleaning_workflow (Task 12).
detect_country_filter is the single source of truth for country
keyword resolution — replaces three duplicated copies in the legacy
codebase. group_by_country routes records to per-country sub-batches
using pre_cleaner.get_country_code as the canonical code lookup."
```

---

### Task 12: Build `run_cleaning_workflow` + persistence

**Files:**
- Modify: `cleaning/orchestrator.py` (add `persist_outputs` and `run_cleaning_workflow`)
- Modify: `tests/cleaning/test_orchestrator.py` (add workflow integration test)

This wires everything together. Per-record persistence in a single transaction (cleaned_data + flags + audit_log together).

- [ ] **Step 1: Write the failing integration test**

Append to `tests/cleaning/test_orchestrator.py`:

```python
# ---- Workflow integration tests ----

from unittest.mock import MagicMock, patch
import pytest


def test_run_cleaning_workflow_end_to_end_mixed_batch(tmp_db, mock_tavily, monkeypatch):
    """Mixed batch: 2 CA + 1 USA + 1 unknown country.
    Mocks LLM to return canned tables. Verifies records persisted, flags raised.
    """
    from db_helpers import insert_raw_data, query_flags
    from cleaning.llm_client import Clients, LLMClient
    from cleaning.orchestrator import run_cleaning_workflow

    insert_raw_data(tmp_db, name="alice", country="Canada", postal_code="M6H 1E7",
                    address="25 Muir Ave", city="Toronto", state_province="Ontario",
                    municipality="")
    insert_raw_data(tmp_db, name="bob", country="CA", postal_code="V6B 2W9",
                    address="100 Granville", city="Vancouver", state_province="BC",
                    municipality="")
    insert_raw_data(tmp_db, name="carol", country="USA", postal_code="10025",
                    address="123 W 95th St", city="New York", state_province="NY",
                    municipality="")
    insert_raw_data(tmp_db, name="diana", country="", postal_code="???",
                    address="?", city="?", state_province="?",
                    municipality="")

    sdk = MagicMock()
    fast_resp = MagicMock(); fast_resp.content = []; fast_resp.stop_reason = "end_turn"
    standard_resp_text = (
        "| ID | Postal Code | Municipality | Validation Notes |\n"
        "| 1 | M6H 1E7 | The Annex | HIGH |\n"
        "| 2 | V6B 2W9 | Yaletown | HIGH |\n"
        "| 3 | 10025 | Upper West Side | HIGH |"
    )
    text_block = MagicMock(); text_block.text = standard_resp_text
    del text_block.name
    text_block.type = "text"
    standard_resp = MagicMock(); standard_resp.content = [text_block]
    standard_resp.stop_reason = "end_turn"

    deep_resp_text = '{"country": "Canada", "postal_code": "M6H 1E7", ' \
                     '"municipality": "The Annex", "validation_notes": "resolved"}'
    deep_block = MagicMock(); deep_block.text = deep_resp_text
    del deep_block.name
    deep_block.type = "text"
    deep_resp = MagicMock(); deep_resp.content = [deep_block]
    deep_resp.stop_reason = "end_turn"

    fast_client = LLMClient(sdk=MagicMock(), model="fast",
                            supports_cache_control=False, base_url=None)
    standard_client = LLMClient(sdk=MagicMock(), model="std",
                                 supports_cache_control=False, base_url=None)
    standard_client.sdk.messages.create.return_value = standard_resp
    deep_client = LLMClient(sdk=MagicMock(), model="deep",
                             supports_cache_control=False, base_url=None)
    deep_client.sdk.messages.create.return_value = deep_resp
    clients = Clients(fast=fast_client, standard=standard_client, deep=deep_client)

    report = run_cleaning_workflow(
        "CLEAN all uncleaned data", db_path=tmp_db, clients=clients,
    )

    assert report.records_processed == 4
    assert report.cleaned_count == 4
    # diana's unknown country triggers UNKNOWN_COUNTRY → escalator resolves it
    flags = query_flags(tmp_db, only_unresolved=False)
    flag_types = {f["flag_type"] for f in flags}
    assert "resolved_after_escalation" in flag_types or "unknown_country" in flag_types


def test_run_cleaning_workflow_no_records_returns_zero_report(tmp_db):
    from cleaning.llm_client import Clients, LLMClient
    from cleaning.orchestrator import run_cleaning_workflow
    from unittest.mock import MagicMock

    fake = LLMClient(sdk=MagicMock(), model="m",
                     supports_cache_control=False, base_url=None)
    clients = Clients(fast=fake, standard=fake, deep=fake)
    report = run_cleaning_workflow("CLEAN canadian data", db_path=tmp_db, clients=clients)
    assert report.records_processed == 0
    assert "No records" in report.summary_text
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `python3 -m pytest tests/cleaning/test_orchestrator.py -v`
Expected: 8 helper tests pass; 2 new workflow tests fail (`run_cleaning_workflow` not defined).

- [ ] **Step 3: Implement `persist_outputs` and `run_cleaning_workflow`**

Append to `cleaning/orchestrator.py`:

```python
import time
from typing import Optional

from cleaning.agent import CleaningAgent
from cleaning.cache import WebSearchCache
from cleaning.escalation import EscalationAgent
from cleaning.flags import FlagType, persist_flags
from cleaning.llm_client import Clients, build_clients
from cleaning.types import CleaningRunReport
from config import DB_PATH as DEFAULT_DB_PATH
from db_helpers import insert_audit_log, insert_cleaned_data
from prompts import build_system_prompt
from prompts.research import build_research_prompt
from schema_discovery import format_schema_for_prompt


_WEB_SEARCH_TOOL = {
    "name": "web_search",
    "description": "Search the web to verify addresses, postal codes, and municipalities.",
    "input_schema": {
        "type": "object",
        "properties": {
            "query": {"type": "string", "description": "Search query"},
            "max_results": {"type": "integer", "default": 5},
        },
        "required": ["query"],
    },
}


def persist_outputs(db_path: str, merged: list[dict]) -> tuple[int, list[dict]]:
    """Persist each merged record + its flags + audit log entries.

    Each record is persisted in its own transaction (one record's failure does
    not roll back the rest). Returns (saved_count, errors).
    """
    saved = 0
    errors: list[dict] = []
    for r in merged:
        try:
            cleaned_id = insert_cleaned_data(
                db_path,
                raw_data_id=r["raw_data_id"],
                name=r.get("name"),
                age=r.get("age"),
                city=r.get("city"),
                address=r.get("address"),
                postal_code=r.get("postal_code"),
                municipality=r.get("municipality"),
                state_province=r.get("state_province"),
                country=r.get("country"),
                phone=r.get("phone"),
                validation_notes=r.get("validation_notes", ""),
                cleaned_by="cleaning-workflow",
            )
            for change in r.get("_pre_clean_changes", []):
                insert_audit_log(
                    db_path, raw_data_id=r["raw_data_id"], cleaned_data_id=cleaned_id,
                    rule_applied="pre_clean", description=change,
                    applied_by="pre-cleaner",
                )
            persist_flags(
                db_path, raw_data_id=r["raw_data_id"], cleaned_data_id=cleaned_id,
                flags=r.get("_flags", []),
            )
            saved += 1
        except Exception as e:
            errors.append({"raw_data_id": r.get("raw_data_id"), "error": str(e)})
    return saved, errors


def run_cleaning_workflow(
    query: str,
    *,
    country_override: Optional[str] = None,
    db_path: Optional[str] = None,
    clients: Optional[Clients] = None,
) -> CleaningRunReport:
    """Public entrypoint. See spec §5.6."""
    db_path = db_path or DEFAULT_DB_PATH
    clients = clients or build_clients()
    timing: dict[str, float] = {}

    # 1. Interpret
    t = time.time()
    filters = interpret_query(query)
    if country_override:
        filters["country"] = country_override
    timing["interpret"] = time.time() - t

    # 2. Fetch
    t = time.time()
    records = fetch_records(db_path, filters)
    timing["fetch"] = time.time() - t
    if not records:
        return _empty_report(timing, "No records found matching your query.")

    # 3. Pre-clean
    t = time.time()
    pre_cleaned = [pre_clean_record(r) for r in records]
    timing["pre_clean"] = time.time() - t

    # 4. Group by country
    t = time.time()
    groups = group_by_country(pre_cleaned)
    timing["group"] = time.time() - t

    # 5. Dispatch
    t = time.time()
    schema = format_schema_for_prompt(db_path)
    web_cache = WebSearchCache()
    escalator = EscalationAgent(
        llm_client=clients.deep, web_cache=web_cache, tools=[_WEB_SEARCH_TOOL],
    )

    agent_outputs: list[CleaningOutput] = []
    for code, batch in groups.items():
        if code is None:
            # Unknown-country bypass: orchestrator dispatches to escalator directly
            for rec in batch:
                out = escalator.investigate(
                    record=rec, country_code=None,
                    flag_hints=[FlagType.UNKNOWN_COUNTRY],
                    prior_search_log=[],
                )
                agent_outputs.append(out)
            continue
        agent = CleaningAgent(
            country_code=code,
            system_prompt=build_system_prompt(code, schema=schema),
            research_prompt_builder=build_research_prompt,
            tools=[_WEB_SEARCH_TOOL],
            llm_client=clients.standard,
            web_cache=web_cache,
            escalator=escalator,
        )
        agent_outputs.extend(agent.process(batch))
    timing["research"] = time.time() - t

    # 6. Merge
    t = time.time()
    merged = merge_results(pre_cleaned, agent_outputs)
    timing["merge"] = time.time() - t

    # 7. Persist
    t = time.time()
    saved, errors = persist_outputs(db_path, merged)
    timing["persist"] = time.time() - t

    # 8. Build report
    flags_by_type: dict[str, int] = {}
    flag_summary: list[dict] = []
    for r in merged:
        for f in r.get("_flags", []):
            flags_by_type[f.flag_type.value] = flags_by_type.get(f.flag_type.value, 0) + 1
            flag_summary.append({"raw_data_id": r["raw_data_id"],
                                 "flag_type": f.flag_type.value,
                                 "severity": f.severity.value, "reason": f.reason})

    summary_text = (
        f"Cleaned {saved}/{len(records)} records. "
        f"{len(flag_summary)} flag(s) raised. "
        f"Cache: {web_cache.stats()['hits']} hits / "
        f"{web_cache.stats()['misses']} misses. "
        f"Total: {sum(timing.values()):.2f}s."
    )
    return CleaningRunReport(
        records_processed=len(records),
        cleaned_count=saved,
        flagged_count=len(flag_summary),
        flags_by_type=flags_by_type,
        cache_stats=web_cache.stats(),
        timing=timing,
        flag_summary=flag_summary,
        errors=errors,
        summary_text=summary_text,
    )


def _empty_report(timing: dict, message: str) -> CleaningRunReport:
    return CleaningRunReport(
        records_processed=0, cleaned_count=0, flagged_count=0,
        flags_by_type={}, cache_stats={"hits": 0, "misses": 0, "queries_cached": 0},
        timing=timing, flag_summary=[], errors=[], summary_text=message,
    )
```

- [ ] **Step 4: Run all orchestrator tests**

Run: `python3 -m pytest tests/cleaning/test_orchestrator.py -v`
Expected: All tests pass (8 helpers + 2 workflow).

- [ ] **Step 5: Commit**

```bash
git add cleaning/orchestrator.py tests/cleaning/test_orchestrator.py
git commit -m "add run_cleaning_workflow + persist_outputs

Wires interpret → fetch → pre-clean → group → dispatch → merge → persist.
Unknown-country records bypass per-country agents and go directly to the
escalator (orchestrator's only escalation responsibility per spec §6).
Per-record transaction means one record's persistence failure does not
roll back the rest; failures are recorded in CleaningRunReport.errors."
```

---

### Task 13: Wire up `cleaning/__init__.py` public exports

**Files:**
- Modify: `cleaning/__init__.py`

- [ ] **Step 1: Replace the placeholder with real exports**

```python
"""Public API for the cleaning subpackage.

See spec at docs/superpowers/specs/2026-04-27-data-cleaning-c-hybrid-refactor-design.md
"""
from cleaning.flags import Flag, FlagSeverity, FlagType
from cleaning.llm_client import Clients, LLMClient, build_clients
from cleaning.orchestrator import run_cleaning_workflow
from cleaning.types import CleaningOutput, CleaningRunReport, SearchHit

__all__ = [
    "run_cleaning_workflow", "build_clients", "Clients", "LLMClient",
    "CleaningOutput", "CleaningRunReport", "SearchHit",
    "Flag", "FlagSeverity", "FlagType",
]
```

> Note: `AdHocConversation` will be added to this list in Task 14.

- [ ] **Step 2: Smoke-test the public import**

Run: `python3 -c "from cleaning import run_cleaning_workflow, build_clients, Flag, FlagType; print('ok')"`
Expected: `ok`

- [ ] **Step 3: Run the entire test suite to confirm nothing regressed**

Run: `python3 -m pytest tests/ -v`
Expected: All tests pass.

- [ ] **Step 4: Commit**

```bash
git add cleaning/__init__.py
git commit -m "expose cleaning public API

run_cleaning_workflow, Clients/build_clients/LLMClient,
CleaningOutput/CleaningRunReport/SearchHit, Flag/FlagType/FlagSeverity.
AdHocConversation added to exports in Task 14."
```

---

## Phase 5 — REPL + AdHoc

### Task 14: Build `AdHocConversation` with CRUD tools

**Files:**
- Create: `cleaning/conversation.py`
- Create: `tests/cleaning/test_conversation.py`
- Modify: `cleaning/__init__.py` (add `AdHocConversation` to exports)

The cleaned-up version of today's `send_message` loop. Has the full CRUD tool set. Used by the REPL for ad-hoc questions ("show me record 5", "delete record 12") that aren't cleaning workflows.

- [ ] **Step 1: Write the failing test**

Create `tests/cleaning/test_conversation.py`:

```python
"""Tests for cleaning.conversation.AdHocConversation."""
from unittest.mock import MagicMock


def _text_response(text):
    block = MagicMock(); block.type = "text"; block.text = text
    del block.name
    resp = MagicMock(); resp.content = [block]; resp.stop_reason = "end_turn"
    return resp


def test_adhoc_send_returns_assistant_text(tmp_db):
    from cleaning.conversation import AdHocConversation
    from cleaning.llm_client import Clients, LLMClient
    sdk = MagicMock()
    sdk.messages.create.return_value = _text_response("Hello, how can I help?")
    fake = LLMClient(sdk=sdk, model="m", supports_cache_control=False, base_url=None)
    clients = Clients(fast=fake, standard=fake, deep=fake)
    convo = AdHocConversation(clients=clients, db_path=tmp_db)
    out = convo.send("Hi")
    assert "Hello, how can I help?" in out


def test_adhoc_send_appends_to_message_history(tmp_db):
    from cleaning.conversation import AdHocConversation
    from cleaning.llm_client import Clients, LLMClient
    sdk = MagicMock()
    sdk.messages.create.return_value = _text_response("ok")
    fake = LLMClient(sdk=sdk, model="m", supports_cache_control=False, base_url=None)
    clients = Clients(fast=fake, standard=fake, deep=fake)
    convo = AdHocConversation(clients=clients, db_path=tmp_db)
    convo.send("first message")
    convo.send("second message")
    assert len(convo.messages) >= 4  # 2 user + 2 assistant minimum
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `python3 -m pytest tests/cleaning/test_conversation.py -v`
Expected: FAIL — `cleaning.conversation` not present.

- [ ] **Step 3: Implement `cleaning/conversation.py`**

```python
"""Ad-hoc conversation loop for the REPL.

Separate from the cleaning workflow because it serves a different purpose:
ad-hoc questions like 'show me record 5', 'delete record 12', 'insert this
new contact'. Has full CRUD tool access; cleaning agents do NOT (see spec §5.8).
"""
from __future__ import annotations

import logging
from typing import Any

from cleaning.cache import WebSearchCache, _tavily_call as _direct_tavily  # noqa: F401
from cleaning.llm_client import Clients
from db_helpers import (
    delete_raw_data, get_cleaned_data_for_raw, get_raw_data_by_id,
    insert_raw_data, query_records, update_cleaned_data, update_raw_data,
)
from guardrails import (
    GuardrailError, check_age, check_country, check_delete_confirmation,
    check_delete_not_bulk, check_nl_phone_format, check_no_wildcard_update,
    check_protected_fields, check_usa_state,
)
from schema_discovery import get_column_metadata, get_table_schema


logger = logging.getLogger(__name__)


_SQLITE_TO_JSON_TYPE = {
    "TEXT": "string", "INTEGER": "integer", "REAL": "number",
    "NUMERIC": "number", "BLOB": "string", "TIMESTAMP": "string",
}
_AUTO_MANAGED = {
    "id", "imported_at", "cleaned_at", "imported_by", "cleaned_by",
    "applied_at", "applied_by",
}


def _build_table_properties(db_path: str, table_name: str,
                            exclude: set | None = None) -> dict:
    exclude = (exclude or set()) | _AUTO_MANAGED
    columns = get_table_schema(db_path, table_name)
    descriptions = get_column_metadata(db_path, table_name)
    props = {}
    for col in columns:
        if col["name"] in exclude:
            continue
        sqlite_type = col["type"].upper().split("(")[0].strip()
        json_type = _SQLITE_TO_JSON_TYPE.get(sqlite_type, "string")
        entry = {"type": json_type}
        if col["name"] in descriptions:
            entry["description"] = descriptions[col["name"]]
        props[col["name"]] = entry
    return props


def _column_names(db_path: str, table_name: str,
                  exclude: set | None = None) -> list[str]:
    exclude = (exclude or set()) | _AUTO_MANAGED
    return [c["name"] for c in get_table_schema(db_path, table_name)
            if c["name"] not in exclude]


_SYSTEM_PROMPT = """You are a helpful data assistant for a real estate cleaning database.
You can answer questions about records, search the web, and modify the database
when explicitly asked. Use the tools provided. Be concise."""


class AdHocConversation:
    def __init__(self, *, clients: Clients, db_path: str):
        self.clients = clients
        self.db_path = db_path
        self.messages: list[dict] = []
        self.cache = WebSearchCache()
        self.tools = self._build_tools()

    def _build_tools(self) -> list[dict]:
        return [
            {
                "name": "web_search",
                "description": "Search the web for verification.",
                "input_schema": {
                    "type": "object",
                    "properties": {"query": {"type": "string"},
                                   "max_results": {"type": "integer", "default": 5}},
                    "required": ["query"],
                },
            },
            {
                "name": "insert_record",
                "description": "Insert a new record into raw_data.",
                "input_schema": {
                    "type": "object",
                    "properties": _build_table_properties(self.db_path, "raw_data"),
                    "required": ["name"],
                },
            },
            {
                "name": "update_record",
                "description": (
                    "Update fields on raw_data or cleaned_data by ID. "
                    f"raw_data fields: {_column_names(self.db_path, 'raw_data')}. "
                    f"cleaned_data fields: {_column_names(self.db_path, 'cleaned_data', exclude={'raw_data_id'})}."
                ),
                "input_schema": {
                    "type": "object",
                    "properties": {
                        "table": {"type": "string", "enum": ["raw_data", "cleaned_data"]},
                        "record_id": {"type": "integer"},
                        "fields": {"type": "object"},
                    },
                    "required": ["table", "record_id", "fields"],
                },
            },
            {
                "name": "delete_record",
                "description": "Delete a raw_data record by ID. Requires confirm='yes'.",
                "input_schema": {
                    "type": "object",
                    "properties": {
                        "record_id": {"type": "integer"},
                        "confirm": {"type": "string"},
                        "override_cleaned_check": {"type": "boolean"},
                    },
                    "required": ["record_id", "confirm"],
                },
            },
            {
                "name": "query_records",
                "description": "Search and filter records.",
                "input_schema": {
                    "type": "object",
                    "properties": {
                        "table": {"type": "string",
                                  "enum": ["raw_data", "cleaned_data", "audit_log", "flags"]},
                        "filters": {"type": "object"},
                        "limit": {"type": "integer"},
                    },
                    "required": ["table"],
                },
            },
        ]

    def send(self, user_input: str) -> str:
        self.messages.append({"role": "user", "content": user_input})
        while True:
            resp = self.clients.standard.messages_create(
                system=_SYSTEM_PROMPT, messages=self.messages, tools=self.tools,
            )
            if resp.stop_reason != "tool_use":
                text = next((b.text for b in resp.content if hasattr(b, "text")), "")
                self.messages.append({"role": "assistant", "content": resp.content})
                return text

            self.messages.append({"role": "assistant", "content": resp.content})
            tool_calls = [b for b in resp.content
                          if hasattr(b, "type") and b.type == "tool_use"]
            results = []
            for tc in tool_calls:
                result = self._execute_tool(tc.name, tc.input)
                results.append({"type": "tool_result",
                                "tool_use_id": tc.id, "content": result})
            self.messages.append({"role": "user", "content": results})

    def show_history(self) -> None:
        print(f"\n{'=' * 70}\nCONVERSATION HISTORY ({len(self.messages)} messages)\n{'=' * 70}")
        for i, m in enumerate(self.messages, 1):
            content_repr = m["content"] if isinstance(m["content"], str) else "<blocks>"
            preview = (content_repr[:80] + "...") if len(str(content_repr)) > 80 else content_repr
            print(f"{i}. {m['role'].upper()}: {preview}")

    # ---- tool dispatchers ----
    def _execute_tool(self, name: str, args: dict) -> str:
        try:
            if name == "web_search":
                return self.cache.web_search_cached(args.get("query", ""),
                                                    args.get("max_results", 5))
            if name == "insert_record":
                return self._insert_record(args)
            if name == "update_record":
                return self._update_record(args)
            if name == "delete_record":
                return self._delete_record(args)
            if name == "query_records":
                return self._query_records(args)
        except Exception as e:
            return f"Tool error: {e}"
        return f"Unknown tool: {name}"

    def _insert_record(self, args: dict) -> str:
        try:
            check_age(args.get("age"))
            check_country(args.get("country"))
        except GuardrailError as e:
            return f"GUARDRAIL BLOCKED: {e}"
        rid = insert_raw_data(
            self.db_path, name=args["name"], age=args.get("age"),
            city=args.get("city"), address=args.get("address"),
            postal_code=args.get("postal_code"),
            municipality=args.get("municipality"),
            state_province=args.get("state_province"),
            country=args.get("country"), phone=args.get("phone"),
            imported_by="adhoc-conversation",
        )
        return f"Inserted record ID {rid}: {args['name']}"

    def _update_record(self, args: dict) -> str:
        table = args.get("table", "raw_data")
        record_id = args.get("record_id")
        fields = args.get("fields", {})
        try:
            check_no_wildcard_update(fields)
            check_protected_fields(fields, table)
            if "age" in fields:
                check_age(fields["age"])
            if "country" in fields:
                check_country(fields["country"])
        except GuardrailError as e:
            return f"GUARDRAIL BLOCKED: {e}"
        current = (get_raw_data_by_id(self.db_path, record_id) if table == "raw_data"
                   else (query_records(self.db_path, "cleaned_data", {"id": record_id}, 1)
                         or [None])[0])
        if not current:
            return f"Record ID {record_id} not found in {table}."
        eff_country = fields.get("country", current.get("country", ""))
        try:
            if eff_country in ("USA", "United States") and "state_province" in fields:
                check_usa_state(fields["state_province"])
            if eff_country in ("NL", "Netherlands") and "phone" in fields:
                check_nl_phone_format(fields["phone"])
        except GuardrailError as e:
            return f"GUARDRAIL BLOCKED: {e}"
        try:
            updated = (update_raw_data(self.db_path, record_id, fields)
                       if table == "raw_data"
                       else update_cleaned_data(self.db_path, record_id, fields))
        except ValueError as e:
            return f"GUARDRAIL BLOCKED: {e}"
        return (f"Updated {table} record ID {record_id}: {list(fields.keys())} changed."
                if updated else f"No record found with ID {record_id} in {table}.")

    def _delete_record(self, args: dict) -> str:
        record_id = args.get("record_id")
        try:
            check_delete_not_bulk(record_id)
            check_delete_confirmation(args.get("confirm", ""))
        except GuardrailError as e:
            return f"GUARDRAIL BLOCKED: {e}"
        cleaned_entries = get_cleaned_data_for_raw(self.db_path, record_id)
        if cleaned_entries and not args.get("override_cleaned_check", False):
            return (f"GUARDRAIL BLOCKED: Record ID {record_id} has "
                    f"{len(cleaned_entries)} cleaned_data entries. "
                    f"Set override_cleaned_check=true to force.")
        deleted = delete_raw_data(self.db_path, record_id)
        return (f"Deleted raw_data record ID {record_id}." if deleted
                else f"No record found with ID {record_id}.")

    def _query_records(self, args: dict) -> str:
        table = args.get("table", "raw_data")
        filters = args.get("filters") or {}
        limit = min(args.get("limit", 50), 50)
        try:
            records = query_records(self.db_path, table, filters, limit)
        except ValueError as e:
            return f"Query error: {e}"
        if not records:
            return f"No records found in {table}" + (f" with filters: {filters}" if filters else ".")
        lines = [f"Found {len(records)} record(s) in {table}:"]
        for r in records:
            lines.append(f"  {r}")
        return "\n".join(lines)
```

- [ ] **Step 4: Add `AdHocConversation` to `cleaning/__init__.py`**

Modify `cleaning/__init__.py` — add the import and update `__all__`:

```python
from cleaning.conversation import AdHocConversation
# ... existing imports ...
__all__ = [
    "run_cleaning_workflow", "AdHocConversation",
    "build_clients", "Clients", "LLMClient",
    "CleaningOutput", "CleaningRunReport", "SearchHit",
    "Flag", "FlagSeverity", "FlagType",
]
```

- [ ] **Step 5: Run tests + smoke import**

Run: `python3 -m pytest tests/cleaning/test_conversation.py -v`
Expected: All tests pass.
Run: `python3 -c "from cleaning import AdHocConversation; print('ok')"`
Expected: `ok`

- [ ] **Step 6: Commit**

```bash
git add cleaning/conversation.py cleaning/__init__.py tests/cleaning/test_conversation.py
git commit -m "add AdHocConversation: REPL chat loop with full CRUD tools

Carved off from multi_turn_conversation.send_message. Has insert/update/
delete/query_records + web_search; cleaning agents do NOT have CRUD per
spec §5.8 (orchestrator is the sole writer). Reuses guardrails verbatim
from the existing module — guardrail behavior unchanged."
```

---

### Task 15: Rewrite `multi_turn_conversation.py` as a thin REPL wrapper

**Files:**
- Modify: `multi_turn_conversation.py` (replace entirely with ~120 lines)

The REPL stays at project root for backward compatibility (`python3 multi_turn_conversation.py` still works). All business logic moved to `cleaning/`.

- [ ] **Step 1: Replace the file's contents wholesale**

Overwrite `multi_turn_conversation.py` with:

```python
"""Interactive REPL for the data cleaning system.

Thin wrapper around cleaning.run_cleaning_workflow + cleaning.AdHocConversation.
All business logic lives in the cleaning/ subpackage.
"""
import os
import sys

from cleaning import AdHocConversation, build_clients, run_cleaning_workflow
from config import DB_PATH
from database import init_db


def _load_env() -> None:
    """Read .env into os.environ without requiring python-dotenv."""
    env_path = os.path.join(os.path.dirname(__file__), ".env")
    if not os.path.exists(env_path):
        return
    with open(env_path) as f:
        for line in f:
            line = line.strip()
            if line and not line.startswith("#") and "=" in line:
                k, v = line.split("=", 1)
                os.environ[k] = v


def _read_multiline(prompt: str) -> str:
    print(f"\n{prompt}")
    lines: list[str] = []
    while True:
        try:
            line = input()
        except EOFError:
            break
        if line.strip().upper() == "END":
            break
        lines.append(line)
    return "\n".join(lines)


def _print_help() -> None:
    print("Commands:")
    print("  CLEAN [query]   - run a cleaning workflow (e.g. 'CLEAN canadian data', 'CLEAN all')")
    print("  HISTORY         - show conversation history")
    print("  HELP            - this message")
    print("  QUIT            - exit")
    print("Anything else is sent to the ad-hoc data assistant.")


def main() -> None:
    _load_env()
    init_db(DB_PATH)
    clients = build_clients()
    convo = AdHocConversation(clients=clients, db_path=DB_PATH)

    print("=" * 70)
    print("DATA CLEANING REPL")
    print("=" * 70)
    _print_help()

    turn = 0
    while True:
        turn += 1
        cmd = _read_multiline(
            f"Turn {turn} (type 'END' on a new line to submit, or QUIT to exit):"
        )
        upper = cmd.strip().upper()

        if upper == "QUIT" or upper == "EXIT":
            print("Goodbye.")
            return
        if upper == "HISTORY":
            convo.show_history()
            turn -= 1
            continue
        if upper == "HELP":
            _print_help()
            turn -= 1
            continue
        if upper.startswith("CLEAN"):
            query = cmd[5:].strip()
            print(f"\n{'=' * 70}\nCLEANING WORKFLOW: {query or '(no query)'}\n{'=' * 70}")
            report = run_cleaning_workflow(query, clients=clients)
            print(report.summary_text)
            if report.flag_summary:
                print(f"\nFlags raised ({len(report.flag_summary)}):")
                for f in report.flag_summary[:20]:
                    print(f"  - record {f['raw_data_id']} [{f['severity']}] "
                          f"{f['flag_type']}: {f['reason']}")
                if len(report.flag_summary) > 20:
                    print(f"  ... and {len(report.flag_summary) - 20} more")
            if report.errors:
                print(f"\nErrors ({len(report.errors)}):")
                for e in report.errors:
                    print(f"  - record {e['raw_data_id']}: {e['error']}")
            continue
        if not cmd.strip():
            print("Please enter a message or command.")
            turn -= 1
            continue

        print("\n[Processing...]")
        print("\nASSISTANT:")
        print(convo.send(cmd))


if __name__ == "__main__":
    main()
```

- [ ] **Step 2: Run the entire test suite to confirm no regression**

Run: `python3 -m pytest tests/ -v`
Expected: All tests pass.

- [ ] **Step 3: Smoke-test the REPL imports and main function loads**

Run: `python3 -c "import multi_turn_conversation; print('ok')"`
Expected: `ok`

- [ ] **Step 4: Commit**

```bash
git add multi_turn_conversation.py
git commit -m "rewrite multi_turn_conversation.py as thin REPL wrapper

Down from 1051 lines to ~120 lines. All business logic now lives in
the cleaning/ subpackage. Behavior preserved: CLEAN/HISTORY/QUIT
commands, multi-line input via END marker, ad-hoc free-text routes
through AdHocConversation.send. Adds HELP command and shows flag/error
summaries from CleaningRunReport."
```

---

## Phase 6 — Cleanup + Validation

### Task 16: Delete dead code

**Files:**
- Delete: `data_cleaning_agent.py`
- Delete: `data_cleaning/clean_data_workflow.py`
- Delete: `data_cleaning/` (if empty)
- Delete: `debug_api.py`, `test_direct.py`, `test_sdk.py`
- Delete: `debug_output.txt` (if tracked)

- [ ] **Step 1: Confirm nothing in the codebase still imports from removed modules**

Run: `grep -rn "from data_cleaning_agent\|import data_cleaning_agent\|from data_cleaning\." --include="*.py" .`
Expected: No matches in `cleaning/`, `tests/`, or `multi_turn_conversation.py`. Matches in `data_cleaning_agent.py` itself or in `data_cleaning/clean_data_workflow.py` are about to be deleted, so they're fine.

If matches appear elsewhere, fix the imports before deleting.

- [ ] **Step 2: Delete the files**

```bash
git rm data_cleaning_agent.py
git rm data_cleaning/clean_data_workflow.py
git rm debug_api.py test_direct.py test_sdk.py
# debug_output.txt may not be tracked; ignore failure
git rm debug_output.txt 2>/dev/null || rm -f debug_output.txt
# remove now-empty data_cleaning/ directory
rmdir data_cleaning/ 2>/dev/null || true
```

- [ ] **Step 3: Run the full test suite**

Run: `python3 -m pytest tests/ -v`
Expected: All tests pass. No collection errors from missing imports.

- [ ] **Step 4: Smoke-test the REPL one more time**

Run: `python3 -c "import multi_turn_conversation; print('ok')"`
Expected: `ok`

- [ ] **Step 5: Commit**

```bash
git add -A
git commit -m "delete dead code: data_cleaning_agent, clean_data_workflow, debug scripts

- data_cleaning_agent.py — superseded by cleaning/orchestrator.py
- data_cleaning/clean_data_workflow.py — legacy menu workflow
- debug_api.py, test_direct.py, test_sdk.py — ad-hoc debug scripts
- debug_output.txt — debug artifact"
```

---

### Task 17: Final smoke test against `gpt-oss-20b:free`

**Files:** none (pure validation)

Confirm the refactored code actually runs end-to-end against the live model on a small batch. If `setup_sample_data.py` populates fixture rows, run it first to ensure there's something to clean.

- [ ] **Step 1: Verify env is set up**

Run: `python3 -c "import os; print('OPENROUTER_API_KEY:', bool(os.getenv('OPENROUTER_API_KEY'))); print('TAVILY_API_KEY:', bool(os.getenv('TAVILY_API_KEY')))"`
Expected: Both `True`.

If either is `False`, source `.env` first or check the file.

- [ ] **Step 2: Confirm there are records to clean**

Run: `python3 -c "from db_helpers import query_records; from config import DB_PATH; print(len(query_records(DB_PATH, 'raw_data', {}, 50)))"`
Expected: Some count > 0. If 0, run `python3 setup_sample_data.py` first.

- [ ] **Step 3: Run the workflow end-to-end on Canadian records**

Run: `python3 -c "
from cleaning import run_cleaning_workflow, build_clients
clients = build_clients()
report = run_cleaning_workflow('CLEAN canadian data', clients=clients)
print(report.summary_text)
print('flags by type:', report.flags_by_type)
print('cache stats:', report.cache_stats)
print('timing:', report.timing)
"`

Expected output: a summary like `Cleaned N/M records. K flag(s) raised. ...`. No tracebacks. Cache stats should show some hits if multiple records share an FSA.

- [ ] **Step 4: Run the REPL interactively**

Run: `python3 multi_turn_conversation.py`

Manually test:
- `HELP` shows the command list
- `CLEAN canadian data` → workflow runs, summary printed
- `show me the first 3 records` → ad-hoc conversation responds
- `QUIT` exits cleanly

Expected: each operation completes without traceback. The REPL is responsive.

- [ ] **Step 5: Final commit (no changes — just a marker)**

```bash
git commit --allow-empty -m "validate C-hybrid refactor end-to-end against gpt-oss-20b:free

Smoke-tested:
- run_cleaning_workflow('CLEAN canadian data') completes with cache hits
- REPL CLEAN/HELP/QUIT flow works
- Ad-hoc conversation tool-use loop works"
```

---

## Done.

Outcomes:
- `multi_turn_conversation.py` is ~120 lines (was 1051)
- Country detection is one source of truth (`cleaning.orchestrator.detect_country_filter` + `cleaning.pre_cleaner.get_country_code`)
- Mixed-country batches route per-record correctly
- Flags are queryable: `query_unresolved_flags()` returns the review queue
- Tiered LLM clients allow stage-now/vetting/production model switches via env vars
- `WebSearchCache` thread-safe from day one (A migration ready)
- `CleaningAgent` boundary preserves the three migration invariants
- Dead code removed
- Test suite covers pre-cleaner, flags, cache, llm-client, agent, escalation, orchestrator, conversation
