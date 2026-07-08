# Gap Type Vocabulary (v1) Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build the v1 gap_type vocabulary — a controlled `<verb>:<field>[|<qualifier>]` string, `missing`-only detection driven by a `gap_detection` block in `column_metadata`, one shared classifier replacing the hardcoded `_identify_gaps`, and `FlagType` derivation from gaps.

**Architecture:** A new `gap_detection` JSON column on `column_metadata` declares per-field detection config. A pure `classify_gaps(record, config)` function reads that config and emits gap strings (v1: `missing` verb + column-value qualifier only). Pure string helpers in `cleaning/gap_types.py` own the closed verb set and build/parse logic. `web_search_enricher._identify_gaps` is refactored to delegate to the classifier. A `flags_from_gaps` helper derives existing `FlagType`s from gap strings.

**Tech Stack:** Python 3.12, PostgreSQL (psycopg) + SQLite backends via the `db/schema_discovery.py` dispatcher, pytest with mocks (no live DB/API), YAML seeds.

**Scope notes (from spec §7):** v1 builds `missing` detection and column-value qualifiers only. `malformed`/`out_of_range`/`mismatch` detection and content-sniffing sub-conditions are designed but NOT built — they appear as no-op branches/structure only. The full migration of legacy downstream signals (`_unknown_fsa` → `ambiguous:*`) waits for the deferred verbs; v1 keeps those legacy hints intact to avoid regressing web-search query lookups.

**Spec:** `docs/superpowers/specs/2026-06-15-gap-type-vocabulary-design.md`

---

## File Structure

**Create:**
- `cleaning/gap_types.py` — closed verb set + `build_gap()` / `parse_gap()` / `is_valid_gap()`. Pure, no deps.
- `cleaning/gap_classifier.py` — `classify_gaps(record, gap_config)`. Pure, no DB.
- `db/migrations/007_column_metadata_gap_detection.sql` — adds `gap_detection` column.
- `tests/cleaning/test_gap_types.py`
- `tests/cleaning/test_gap_classifier.py`
- `tests/cleaning/test_gap_to_flag.py`

**Modify:**
- `db/pg_init.py` — add `gap_detection` migration DO-block (mirrors migration 006 pattern).
- `db/sqlite_init.py` — add `gap_detection` column to `column_metadata` create + a migrate helper.
- `db/pg_query_memory.py` — add conn-based `gap_detection_for(conn, domain, schema=None)`, the **single Postgres SQL source** (enricher holds a live `pg_conn`, mirroring `top_queries_for`).
- `db/pg_schema_discovery.py` — add `get_gap_detection(db_path, domain, schema=None)` that **delegates** to `gap_detection_for` (no duplicated SQL).
- `db/sqlite_schema_discovery.py` — add `get_gap_detection(db_path, domain)`.
- `db/schema_discovery.py` — add `get_gap_detection` dispatcher entry.
- `cleaning/flags.py` — add `flags_from_gaps()` + internal `_GAP_TO_FLAG` mapping.
- `skills/_common/web_search_enricher/web_search_enricher.py` — refactor `_identify_gaps` to delegate to `classify_gaps`.
- `data/seeds/real_estate/column_metadata.yaml` — add `gap_detection` to relevant columns.
- `seeders/real_estate/column_metadata.py` — parse + upsert `gap_detection`.

---

## Task 1: Gap-type string helpers

**Files:**
- Create: `cleaning/gap_types.py`
- Test: `tests/cleaning/test_gap_types.py`

- [ ] **Step 1: Write the failing test**

```python
# tests/cleaning/test_gap_types.py
import pytest
from cleaning.gap_types import (
    VERBS, build_gap, parse_gap, is_valid_gap, ParsedGap,
)


def test_verbs_are_the_closed_set():
    assert VERBS == ("missing", "malformed", "ambiguous", "mismatch", "out_of_range")


def test_build_base_gap():
    assert build_gap("missing", "postal_code") == "missing:postal_code"


def test_build_gap_with_qualifier_is_lowercased_and_stripped():
    assert build_gap("missing", "postal_code", qualifier=" CA ") == "missing:postal_code|ca"


def test_build_mismatch_joins_sorted_fields_with_plus():
    assert build_gap("mismatch", ["province", "city"]) == "mismatch:city+province"


def test_build_gap_rejects_unknown_verb():
    with pytest.raises(ValueError):
        build_gap("frobnicated", "postal_code")


def test_parse_base_gap():
    assert parse_gap("missing:postal_code") == ParsedGap("missing", ("postal_code",), None)


def test_parse_qualified_gap():
    assert parse_gap("missing:postal_code|ca") == ParsedGap("missing", ("postal_code",), "ca")


def test_parse_mismatch_gap():
    assert parse_gap("mismatch:city+province") == ParsedGap("mismatch", ("city", "province"), None)


def test_is_valid_gap():
    assert is_valid_gap("missing:postal_code|ca") is True
    assert is_valid_gap("bogus:postal_code") is False
    assert is_valid_gap("missing") is False
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m pytest tests/cleaning/test_gap_types.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'cleaning.gap_types'`

- [ ] **Step 3: Write minimal implementation**

```python
# cleaning/gap_types.py
"""Gap-type string vocabulary: <verb>:<field>[|<qualifier>].

The verb set is CLOSED — extending it is a deliberate code change, not config.
See docs/superpowers/specs/2026-06-15-gap-type-vocabulary-design.md.
"""
from dataclasses import dataclass
from typing import Optional, Sequence, Union

VERBS = ("missing", "malformed", "ambiguous", "mismatch", "out_of_range")


@dataclass(frozen=True)
class ParsedGap:
    verb: str
    fields: tuple           # one field, or several for `mismatch`
    qualifier: Optional[str]


def build_gap(verb: str, field: Union[str, Sequence[str]], qualifier: Optional[str] = None) -> str:
    if verb not in VERBS:
        raise ValueError(f"unknown gap verb: {verb!r} (allowed: {VERBS})")
    if isinstance(field, str):
        field_part = field
    else:
        field_part = "+".join(sorted(field))
    gap = f"{verb}:{field_part}"
    if qualifier is not None and str(qualifier).strip():
        gap += f"|{str(qualifier).strip().lower()}"
    return gap


def parse_gap(gap: str) -> ParsedGap:
    """Split a gap string into its parts. LENIENT by design — does NOT validate
    the verb. Callers needing guarantees must also call is_valid_gap().
    """
    qualifier = None
    body = gap
    if "|" in body:
        body, qualifier = body.split("|", 1)
        qualifier = qualifier.strip().lower() or None
    verb, _, field_part = body.partition(":")
    fields = tuple(field_part.split("+")) if field_part else ()
    return ParsedGap(verb, fields, qualifier)


def is_valid_gap(gap: str) -> bool:
    parsed = parse_gap(gap)
    return parsed.verb in VERBS and len(parsed.fields) >= 1 and all(parsed.fields)
```

- [ ] **Step 4: Run test to verify it passes**

Run: `python -m pytest tests/cleaning/test_gap_types.py -v`
Expected: PASS (10 passed)

- [ ] **Step 5: Commit**

```bash
git add cleaning/gap_types.py tests/cleaning/test_gap_types.py
git commit -m "feat: gap-type string vocabulary helpers"
```

---

## Task 2: The shared classifier (missing-only)

**Files:**
- Create: `cleaning/gap_classifier.py`
- Test: `tests/cleaning/test_gap_classifier.py`

`classify_gaps` is a pure function — it takes the record and an already-loaded config dict, so tests need no DB. Config shape: `{column_name: {"missing": bool, "discriminator": Optional[str]}}`.

- [ ] **Step 1: Write the failing test**

```python
# tests/cleaning/test_gap_classifier.py
from cleaning.gap_classifier import classify_gaps


def test_emits_missing_for_null_field():
    config = {"postal_code": {"missing": True}}
    record = {"postal_code": None}
    assert classify_gaps(record, config) == ["missing:postal_code"]


def test_emits_missing_for_empty_string():
    config = {"postal_code": {"missing": True}}
    record = {"postal_code": "   "}
    assert classify_gaps(record, config) == ["missing:postal_code"]


def test_no_gap_when_field_present():
    config = {"postal_code": {"missing": True}}
    record = {"postal_code": "M5H 2N2"}
    assert classify_gaps(record, config) == []


def test_qualifier_appended_from_discriminator_column():
    config = {"postal_code": {"missing": True, "discriminator": "country"}}
    record = {"postal_code": None, "country": "CA"}
    assert classify_gaps(record, config) == ["missing:postal_code|ca"]


def test_no_qualifier_when_discriminator_value_absent():
    config = {"postal_code": {"missing": True, "discriminator": "country"}}
    record = {"postal_code": None, "country": None}
    assert classify_gaps(record, config) == ["missing:postal_code"]


def test_missing_disabled_emits_nothing():
    config = {"notes": {"missing": False}}
    record = {"notes": None}
    assert classify_gaps(record, config) == []


def test_unknown_verbs_not_built_in_v1():
    # malformed is designed but not built: a malformed-only config emits nothing
    config = {"phone": {"malformed": {"by": "country", "rules": {}}}}
    record = {"phone": "garbage"}
    assert classify_gaps(record, config) == []


def test_multiple_fields_preserves_order():
    config = {"postal_code": {"missing": True}, "country": {"missing": True}}
    record = {"postal_code": None, "country": ""}
    assert classify_gaps(record, config) == ["missing:postal_code", "missing:country"]
    # NOTE: classify_gaps cannot emit duplicates from a dict config (keys are
    # unique). Real dedup coverage lives in Task 6, where classifier output is
    # merged with legacy hints + _gap_hints that CAN overlap.
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m pytest tests/cleaning/test_gap_classifier.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'cleaning.gap_classifier'`

- [ ] **Step 3: Write minimal implementation**

```python
# cleaning/gap_classifier.py
"""Shared gap classifier. v1 implements the `missing` verb only.

Pure function: pass the record and a pre-loaded gap_detection config so this is
DB-free and trivially testable. Load the config with
db.schema_discovery.get_gap_detection().
"""
from typing import Optional

from cleaning.gap_types import build_gap


def _is_empty(value) -> bool:
    if value is None:
        return True
    if isinstance(value, str) and value.strip() == "":
        return True
    return False


def _qualifier_for(record: dict, cfg: dict) -> Optional[str]:
    disc = cfg.get("discriminator")
    if not disc:
        return None
    disc_val = record.get(disc)
    if _is_empty(disc_val):
        return None
    return str(disc_val)


def classify_gaps(record: dict, gap_config: dict) -> list:
    """Return de-duplicated gap-type strings for a record.

    v1: only the `missing` branch is built. malformed/out_of_range/mismatch keys
    in the config are intentionally ignored (designed, not built — see spec §7).
    """
    gaps = []
    for column, cfg in gap_config.items():
        if not cfg.get("missing"):
            continue
        if _is_empty(record.get(column)):
            gaps.append(build_gap("missing", column, qualifier=_qualifier_for(record, cfg)))
    # dedupe, preserve order
    return list(dict.fromkeys(gaps))
```

- [ ] **Step 4: Run test to verify it passes**

Run: `python -m pytest tests/cleaning/test_gap_classifier.py -v`
Expected: PASS (8 passed)

- [ ] **Step 5: Commit**

```bash
git add cleaning/gap_classifier.py tests/cleaning/test_gap_classifier.py
git commit -m "feat: missing-only gap classifier"
```

---

## Task 3: Schema migration — `gap_detection` column

**Files:**
- Create: `db/migrations/007_column_metadata_gap_detection.sql`
- Modify: `db/pg_init.py` (after the migration-006 DO-block, around line 137)
- Modify: `db/sqlite_init.py` (column_metadata create around line 103; add migrate helper called near line 129)

Stored as `JSONB` on Postgres, `TEXT` (JSON string) on SQLite.

- [ ] **Step 1: Write the migration SQL file**

```sql
-- db/migrations/007_column_metadata_gap_detection.sql
-- Per-field gap detection config (see 2026-06-15-gap-type-vocabulary-design.md).
ALTER TABLE column_metadata
  ADD COLUMN IF NOT EXISTS gap_detection JSONB DEFAULT NULL;
```

- [ ] **Step 2: Add the Postgres DO-block to `db/pg_init.py`**

Insert immediately after the migration-006 DO-block (the one ending near line 137, before the `column_profiles` CREATE):

```python
        # Migration 007: per-field gap detection config
        cursor.execute(f"""
            DO $$
            BEGIN
                IF NOT EXISTS (
                    SELECT 1 FROM information_schema.columns
                    WHERE table_schema = '{schema}' AND table_name='column_metadata' AND column_name='gap_detection'
                ) THEN
                    ALTER TABLE {schema}.column_metadata
                        ADD COLUMN gap_detection JSONB DEFAULT NULL;
                END IF;
            END $$
        """)
```

- [ ] **Step 3: Add the column + migrate helper to `db/sqlite_init.py`**

In the `column_metadata` CREATE (around line 103-109), add the column so fresh installs have it:

```python
        CREATE TABLE IF NOT EXISTS column_metadata (
            domain      TEXT NOT NULL DEFAULT 'base',
            table_name  TEXT NOT NULL,
            column_name TEXT NOT NULL,
            description TEXT,
            gap_detection TEXT DEFAULT NULL,
            PRIMARY KEY (domain, table_name, column_name)
        )
```

Add a migrate helper near the existing `_migrate_column_metadata_add_domain` and call it next to that call (around line 129):

```python
def _migrate_column_metadata_add_gap_detection(conn):
    cursor = conn.cursor()
    cols = {row[1] for row in cursor.execute("PRAGMA table_info(column_metadata)").fetchall()}
    if "gap_detection" not in cols:
        cursor.execute("ALTER TABLE column_metadata ADD COLUMN gap_detection TEXT DEFAULT NULL")
```

And call it alongside the existing migrate call:

```python
    _migrate_column_metadata_add_domain(conn)
    _migrate_column_metadata_add_gap_detection(conn)
    _seed_column_metadata(cursor)
```

- [ ] **Step 4: Verify SQLite init applies cleanly**

Run:
```bash
python -c "import os, tempfile; os.environ['DB_BACKEND']='sqlite'; \
p=tempfile.mktemp(suffix='.db'); import db.sqlite_init as s; s.init_db(p); \
import sqlite3; c=sqlite3.connect(p); \
print([r[1] for r in c.execute('PRAGMA table_info(column_metadata)')])"
```
Expected: a list that includes `'gap_detection'`.

(If `init_db` has a different signature, check `db/sqlite_init.py` for the public entry point and adjust the call.)

- [ ] **Step 5: Commit**

```bash
git add db/migrations/007_column_metadata_gap_detection.sql db/pg_init.py db/sqlite_init.py
git commit -m "feat: add gap_detection column to column_metadata"
```

---

## Task 4: Backend-agnostic `get_gap_detection` reader

**Files:**
- Modify: `db/pg_query_memory.py` (conn-based `gap_detection_for` — the single PG SQL source, next to `top_queries_for`)
- Modify: `db/pg_schema_discovery.py` (db_path `get_gap_detection`, delegates to `gap_detection_for`, ~line 102)
- Modify: `db/sqlite_schema_discovery.py` (next to `get_column_metadata`, ~line 70)
- Modify: `db/schema_discovery.py` (add dispatcher entry near the other `get_column_metadata` dispatch, ~line 42)
- Test: `tests/cleaning/test_gap_classifier.py` is pure; the readers are exercised by the SQLite check below (no new unit test — thin DB glue, consistent with existing untested readers like `get_column_metadata`).

Returns `{column_name: gap_detection_dict}` aggregated across the domain's tables. JSON parsed to a dict.

**Single Postgres SQL source.** The conn-based `gap_detection_for` (Step 1) is the
one place the Postgres `column_metadata` query lives; the db_path dispatcher reader
(Step 2) opens a connection and **delegates** to it — no duplicated SQL. SQLite needs
its own reader (Step 3) because of `?` placeholders and no schema prefix.

- [ ] **Step 1: Add the conn-based reader to `db/pg_query_memory.py` (the single PG SQL source)**

The enricher holds a live `pg_conn` (not a `db_path`) and already reads pattern
memory via conn-based helpers here. This is the canonical Postgres reader:

```python
def gap_detection_for(conn, domain: str, schema: str = None) -> dict:
    """Return {column_name: gap_detection_dict} for a domain using a live conn.

    Mirrors top_queries_for: postgres-first, best-effort. Returns {} on ANY DB
    error (missing table, bad schema, etc.) — intentional: the classifier then
    sees no config and emits no gaps, degrading gracefully rather than crashing.
    """
    import json
    if schema is None:
        schema = get_framework_schema()
    out = {}
    try:
        with conn.cursor() as cur:
            cur.execute(
                f"SELECT column_name, gap_detection FROM {schema}.column_metadata "
                f"WHERE domain = %s AND gap_detection IS NOT NULL",
                (domain,),
            )
            for column_name, cfg in cur.fetchall():
                if isinstance(cfg, str):
                    cfg = json.loads(cfg)
                if cfg:
                    out[column_name] = cfg
    except Exception:
        return {}  # best-effort: classifier falls back to empty config
    return out
```

(`get_framework_schema` is already imported at the top of `db/pg_query_memory.py`.)

- [ ] **Step 2: Add the db_path dispatcher reader to `db/pg_schema_discovery.py` (delegates — no SQL dup)**

```python
def get_gap_detection(db_path: str, domain: str, schema: str = None) -> dict:
    """db_path entry point. Opens a conn and delegates to the single PG SQL
    source (pg_query_memory.gap_detection_for) so there is one query, not two."""
    from db.pg_query_memory import gap_detection_for
    conn = get_db_connection(db_path)
    try:
        return gap_detection_for(conn, domain, schema=schema)
    finally:
        conn.close()
```

- [ ] **Step 3: Add the SQLite reader to `db/sqlite_schema_discovery.py`**

```python
def get_gap_detection(db_path: str, domain: str) -> dict:
    """Return {column_name: gap_detection_dict} for a domain, across its tables."""
    import json
    conn = get_db_connection(db_path)
    try:
        cursor = conn.cursor()
        cursor.execute(
            "SELECT column_name, gap_detection FROM column_metadata "
            "WHERE domain = ? AND gap_detection IS NOT NULL",
            (domain,),
        )
        out = {}
        for row in cursor.fetchall():
            raw = row["gap_detection"]
            if raw:
                out[row["column_name"]] = json.loads(raw)
        return out
    except Exception:
        return {}  # best-effort: classifier falls back to empty config on any DB error
    finally:
        conn.close()
```

- [ ] **Step 4: Add the dispatcher entry to `db/schema_discovery.py`**

```python
def get_gap_detection(db_path: str, domain: str):
    return _impl().get_gap_detection(db_path, domain)
```

- [ ] **Step 5: Verify end-to-end on SQLite**

Run:
```bash
python -c "import os, tempfile, json, sqlite3; os.environ['DB_BACKEND']='sqlite'; \
p=tempfile.mktemp(suffix='.db'); import db.sqlite_init as s; s.init_db(p); \
c=sqlite3.connect(p); \
c.execute(\"INSERT INTO column_metadata (domain, table_name, column_name, gap_detection) VALUES ('real_estate','raw_data','postal_code', ?)\", (json.dumps({'missing': True, 'discriminator': 'country'}),)); \
c.commit(); c.close(); \
from db.schema_discovery import get_gap_detection; \
print(get_gap_detection(p, 'real_estate'))"
```
Expected: `{'postal_code': {'missing': True, 'discriminator': 'country'}}`

- [ ] **Step 6: Commit**

```bash
git add db/pg_query_memory.py db/pg_schema_discovery.py db/sqlite_schema_discovery.py db/schema_discovery.py
git commit -m "feat: backend-agnostic get_gap_detection reader"
```

---

## Task 5: Derive `FlagType` from gaps

**Files:**
- Modify: `cleaning/flags.py`
- Test: `tests/cleaning/test_gap_to_flag.py`

Implements spec §6: data-defect flags derive from gap_type; process flags untouched. Mapping keyed on `(verb, first_field)`; unmapped gaps yield no flag.

**Pre-check (already verified):** every `FlagType` the mapping references —
`UNKNOWN_COUNTRY`, `POSTAL_UNRESOLVED`, `MUNICIPALITY_UNRESOLVED`, `POSTAL_AMBIGUOUS` —
already exists in `cleaning/flags.py` (lines 15-19). So the Step 1 test fails on the
missing `flags_from_gaps` symbol, not an `AttributeError` on the enum. If you ever add
a mapping for a verb/field whose flag does NOT exist, add the enum value first.

- [ ] **Step 1: Write the failing test**

```python
# tests/cleaning/test_gap_to_flag.py
from cleaning.flags import FlagType, flags_from_gaps


def test_missing_country_maps_to_unknown_country():
    assert flags_from_gaps(["missing:country"]) == [FlagType.UNKNOWN_COUNTRY]


def test_missing_postal_code_maps_to_postal_unresolved():
    # qualifier is ignored for flag derivation
    assert flags_from_gaps(["missing:postal_code|ca"]) == [FlagType.POSTAL_UNRESOLVED]


def test_missing_municipality_maps_to_municipality_unresolved():
    assert flags_from_gaps(["missing:municipality"]) == [FlagType.MUNICIPALITY_UNRESOLVED]


def test_unmapped_gap_yields_no_flag():
    assert flags_from_gaps(["missing:notes"]) == []


def test_dedupes_and_preserves_order():
    gaps = ["missing:postal_code|ca", "missing:postal_code|us", "missing:country"]
    assert flags_from_gaps(gaps) == [FlagType.POSTAL_UNRESOLVED, FlagType.UNKNOWN_COUNTRY]
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m pytest tests/cleaning/test_gap_to_flag.py -v`
Expected: FAIL with `ImportError: cannot import name 'flags_from_gaps'`

- [ ] **Step 3: Add the mapping + helper to `cleaning/flags.py`**

Add after the `FlagType` enum definition:

```python
from cleaning.gap_types import parse_gap

# Data-defect gaps -> output FlagType. Keyed on (verb, first_field).
# Process flags (guardrail_blocked, etc.) are never derived from gaps.
_GAP_TO_FLAG = {
    ("missing", "country"):      FlagType.UNKNOWN_COUNTRY,
    ("missing", "postal_code"):  FlagType.POSTAL_UNRESOLVED,
    ("missing", "municipality"): FlagType.MUNICIPALITY_UNRESOLVED,
    ("ambiguous", "postal_code"): FlagType.POSTAL_AMBIGUOUS,
}


def flags_from_gaps(gap_types: list) -> list:
    """Derive output FlagTypes from gap-type strings (spec §6).

    Unmapped gaps yield nothing. Result is de-duplicated, order-preserving.
    """
    flags = []
    for gap in gap_types:
        parsed = parse_gap(gap)
        first_field = parsed.fields[0] if parsed.fields else None
        flag = _GAP_TO_FLAG.get((parsed.verb, first_field))
        if flag is not None and flag not in flags:
            flags.append(flag)
    return flags
```

- [ ] **Step 4: Run test to verify it passes**

Run: `python -m pytest tests/cleaning/test_gap_to_flag.py -v`
Expected: PASS (5 passed)

- [ ] **Step 5: Commit**

```bash
git add cleaning/flags.py tests/cleaning/test_gap_to_flag.py
git commit -m "feat: derive FlagType from gap-type strings"
```

---

## Task 6: Wire the classifier into `web_search_enricher`

**Files:**
- Modify: `skills/_common/web_search_enricher/web_search_enricher.py` (`_identify_gaps`, ~line 108)
- Test: `tests/cleaning/test_gap_classifier.py` (add an integration test for the enricher delegation)

`_identify_gaps` becomes a thin caller of `classify_gaps`. Legacy downstream signals (`_unknown_fsa`, `_municipality_confidence`) map to deferred `ambiguous`/unresolved verbs, so v1 keeps them as legacy hint strings to avoid regressing `query_pattern_memory` lookups. Config is loaded once and cached on the instance.

**Architectural note (dispatcher exception):** the enricher reads gap config via the
conn-based `gap_detection_for(self.conn, …)` rather than the `db/schema_discovery`
db_path dispatcher. This is deliberate and consistent: the enricher is injected a live
`pg_conn` and does *all* its DB access conn-based (`top_queries_for`,
`record_query_outcome`). Routing one read through the db_path dispatcher would make the
enricher *more* internally inconsistent. The dispatcher's `get_gap_detection`
delegates to the same `gap_detection_for`, so there is still one Postgres SQL source.

- [ ] **Step 1: Write the failing test**

Instantiate the real `WebSearchEnricher` with a config (no live conn) and pre-seed
the config cache so no DB is touched — this tests observable behavior, not method
plumbing.

```python
# append to tests/cleaning/test_gap_classifier.py
from skills._common.web_search_enricher.web_search_enricher import WebSearchEnricher


def _enricher_with_config(gap_config):
    enr = WebSearchEnricher(config={"pg_conn": None})
    enr._gap_config_cache = gap_config   # pre-seed cache; _gap_config short-circuits
    return enr


def test_enricher_emits_classifier_gaps_plus_legacy_hints():
    enr = _enricher_with_config({"country": {"missing": True}})
    record = {"country": None, "_unknown_fsa": True, "_gap_hints": ["x"]}
    gaps = enr._identify_gaps(record)
    assert "missing:country" in gaps        # from classifier
    assert "postal_unresolved" in gaps      # legacy downstream signal kept
    assert "x" in gaps                       # explicit hint passthrough


def test_enricher_dedupes_overlapping_classifier_and_hint():
    # classifier emits missing:country; an explicit hint repeats it -> one entry.
    enr = _enricher_with_config({"country": {"missing": True}})
    record = {"country": None, "_gap_hints": ["missing:country"]}
    gaps = enr._identify_gaps(record)
    assert gaps.count("missing:country") == 1
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m pytest tests/cleaning/test_gap_classifier.py -k enricher -v`
Expected: FAIL (`AttributeError` on `_gap_config` / assertion on `missing:country`) — the refactor in Step 3 doesn't exist yet.

- [ ] **Step 3: Refactor `_identify_gaps` and add `_gap_config`**

Replace the existing `_identify_gaps` method with:

```python
    def _gap_config(self) -> dict:
        """Load + cache the domain's gap_detection config (column -> cfg).

        Uses the live self.conn (pg_conn), mirroring how _get_queries reads
        pattern memory. Best-effort: returns {} when no conn / on any error.
        """
        cached = getattr(self, "_gap_config_cache", None)
        if cached is not None:
            return cached
        config = {}
        if self.conn:
            try:
                from db.pg_query_memory import gap_detection_for
                config = gap_detection_for(self.conn, self.domain) or {}
            except Exception:
                config = {}
        self._gap_config_cache = config
        return config

    def _identify_gaps(self, record: dict) -> List[str]:
        from cleaning.gap_classifier import classify_gaps
        gaps = classify_gaps(record, self._gap_config())
        # Legacy downstream signals map to deferred verbs (ambiguous/unresolved);
        # keep them as hint strings in v1 so query_pattern_memory lookups still hit.
        if record.get("_unknown_fsa"):
            gaps.append("postal_unresolved")
        if record.get("_municipality_confidence", 1.0) < 0.70:
            gaps.append("municipality_ambiguous")
        gaps.extend(record.get("_gap_hints", []))
        return list(dict.fromkeys(gaps))  # dedupe, preserve order
```

Note: the previous `if not record.get("country"): gaps.append("unknown_country")` line is intentionally removed — `missing:country` now comes from the classifier when `country` is declared in `gap_detection`. (Domains without that declaration simply won't emit a country gap, which is the correct config-driven behavior.)

- [ ] **Step 4: Run the test + the enricher's existing tests**

Run:
```bash
python -m pytest tests/cleaning/test_gap_classifier.py -v
python -m pytest tests/ -k web_search -v
```
Expected: new test PASSES; no web_search regressions.

- [ ] **Step 5: Commit**

```bash
git add skills/_common/web_search_enricher/web_search_enricher.py tests/cleaning/test_gap_classifier.py
git commit -m "refactor: web_search_enricher delegates gap detection to classify_gaps"
```

---

## Task 7: Seed real_estate `gap_detection`

**Files:**
- Modify: `data/seeds/real_estate/column_metadata.yaml`
- Modify: `seeders/real_estate/column_metadata.py` (`parse` + `upsert`, lines 30-58)
- Test: covered by the SQLite end-to-end check in this task (seeder I/O, consistent with the untested existing seeder).

Adds `gap_detection` to the YAML and threads it through parse → upsert. v1 declares only `missing` (+ a `country` discriminator on postal_code) per the design's worked example.

**Placeholder syntax:** the existing seeder's `upsert` already uses SQLite `?`
placeholders (`column_metadata.py:48`). The new code below **matches that existing
syntax** to stay consistent — it does not introduce a new pattern. Making the seeder
layer backend-portable (`?` vs `%s`) is a pre-existing concern that affects the whole
seeder and is **out of scope** for this plan.

- [ ] **Step 1: Add `gap_detection` to the YAML**

In `data/seeds/real_estate/column_metadata.yaml`, under `tables.raw_data`, add entries (or extend existing ones) for the geography fields:

```yaml
    - column: postal_code
      description: >-
        Postal/ZIP code. Format and validity depend on country.
      gap_detection:
        missing: true
        discriminator: country
    - column: country
      description: >-
        ISO-ish country of the listing; drives postal and municipality logic.
      gap_detection:
        missing: true
    - column: municipality
      gap_detection:
        missing: true
        discriminator: country
```

(If `postal_code`/`country` rows already exist in the file, add only the `gap_detection:` block to them rather than duplicating the row.)

- [ ] **Step 2: Thread `gap_detection` through the seeder `parse`**

In `seeders/real_estate/column_metadata.py`, update the `parse` row dict to carry the JSON:

```python
    def parse(self, payload: Any) -> list:
        import json
        domain = payload.get("domain", self.domain)
        rows = []
        for table_name, cols in payload.get("tables", {}).items():
            for entry in cols:
                gd = entry.get("gap_detection")
                rows.append({
                    "domain":      domain,
                    "table_name":  table_name,
                    "column_name": entry["column"],
                    "description": entry.get("description", "").strip(),
                    "gap_detection": json.dumps(gd) if gd else None,
                })
        return rows
```

- [ ] **Step 3: Update `upsert` to write `gap_detection`**

```python
    def upsert(self, conn, rows: list) -> int:
        cursor = conn.cursor()
        count = 0
        for row in rows:
            cursor.execute(
                """
                INSERT INTO column_metadata (domain, table_name, column_name, description, gap_detection)
                VALUES (?, ?, ?, ?, ?)
                ON CONFLICT (domain, table_name, column_name)
                DO UPDATE SET description = excluded.description,
                              gap_detection = excluded.gap_detection
                """,
                (row["domain"], row["table_name"], row["column_name"],
                 row["description"], row["gap_detection"]),
            )
            count += 1
        conn.commit()
        return count
```

- [ ] **Step 4: Verify the seed lands and the classifier reads it**

Run:
```bash
python -m pytest tests/ -k "column_metadata or seed" -v
```
Expected: PASS (no seeder regressions). If a seeder integration test exercises the new column, it asserts `gap_detection` round-trips.

- [ ] **Step 5: Commit**

```bash
git add data/seeds/real_estate/column_metadata.yaml seeders/real_estate/column_metadata.py
git commit -m "feat: seed real_estate gap_detection config"
```

---

## Task 8: Full suite + spec cross-check

- [ ] **Step 1: Run the whole test suite**

Run: `python -m pytest tests/ -q`
Expected: all green. Investigate any failure before proceeding (use systematic-debugging).

- [ ] **Step 2: Confirm spec §7 v1 scope is met**

Verify each v1 row in the spec's scope table has a corresponding task:
- base strings + 5-verb set → Task 1
- column-value qualifiers → Task 1 + Task 2
- `missing` detection → Task 2
- `gap_detection` block (missing + discriminator) → Tasks 3, 4, 7
- shared `classify_gaps` replacing `_identify_gaps` → Tasks 2, 6
- `FlagType` derived from gaps → Task 5

- [ ] **Step 3: Final commit if anything was touched**

```bash
git add -A
git commit -m "chore: gap-type vocabulary v1 complete" || echo "nothing to commit"
```

---

## Out of Scope (designed, deferred — do NOT build here)

- `malformed` / `out_of_range` / `mismatch` detection (config structure parsed but no-op in `classify_gaps`).
- Content-sniffing sub-condition detectors (e.g. `stacked_unit`).
- Migrating legacy downstream signals (`_unknown_fsa`, `_municipality_confidence`) and the `query_packs.yaml` gap strings to `ambiguous:*` vocabulary — waits for the `ambiguous` verb build.
- Init-agent proposal UX in `initialize_domain.py` Phase 3 (spec §9) — separate plan.
- Folding `gap_type` back into the agentic-memory spec as the now-defined input vocabulary — separate follow-up.
