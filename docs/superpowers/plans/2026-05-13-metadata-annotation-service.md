# Metadata Annotation Service Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build a domain-agnostic `MetadataAnnotationService` that generates LLM descriptions for DB columns and stores them in `column_metadata`, surfaced via CLI and used by `SkillPlanner` at plan time.

**Architecture:** `MetadataAnnotationService` (pure Python, no MCP) calls LLM per column, upserts into `column_metadata` with confidence + provenance fields. CLI wraps the service. `SkillPlanner` queries annotations to enrich planning prompts. `OrchestrationTeam` warns when gaps exist.

**Tech Stack:** Python 3.11+, psycopg2, `cleaning.llm_client.LLMClient`, `seeders.registry.SeederRegistry` (for manifest description), pytest + `unittest.mock`

---

## File Map

| Action | File | Responsibility |
|--------|------|----------------|
| Create | `db/migrations/006_column_metadata_annotation_fields.sql` | Add `is_llm_generated`, `confidence`, `generated_at` columns |
| Modify | `db/pg_init.py` | Run migration 006 in `init_db()` |
| Create | `prompts/annotation.py` | `build_annotation_prompt()` — pure function |
| Create | `services/__init__.py` | Empty package marker |
| Create | `services/metadata_annotation.py` | `AnnotationReport`, `MetadataAnnotationService` |
| Create | `scripts/annotate_domain.py` | CLI entry point |
| Modify | `skills/_common/skill_planner/skill_planner.py` | Add `_get_annotation_context()`, update `_build_prompt()` |
| Modify | `cleaning/orchestrator_v2.py` | Add `_warn_annotation_gaps()` called from `__init__` |
| Create | `tests/test_metadata_annotation.py` | Service + prompt unit tests |
| Modify | `CLAUDE.md` | Document `annotate_domain.py` |

---

## Task 1: DB Migration + pg_init.py wiring

**Files:**
- Create: `db/migrations/006_column_metadata_annotation_fields.sql`
- Modify: `db/pg_init.py:93–115` (after the existing `column_metadata` DO-block)

- [ ] **Step 1: Create migration file**

```sql
-- db/migrations/006_column_metadata_annotation_fields.sql
ALTER TABLE column_metadata
  ADD COLUMN IF NOT EXISTS is_llm_generated  BOOLEAN   DEFAULT FALSE,
  ADD COLUMN IF NOT EXISTS confidence        FLOAT     DEFAULT NULL,
  ADD COLUMN IF NOT EXISTS generated_at      TIMESTAMP DEFAULT NULL;
```

- [ ] **Step 2: Wire migration into pg_init.py**

In `db/pg_init.py`, after the existing `DO $$ ... $$` block that guards the `domain` column (ends around line 115), add:

```python
        # Migration 006: annotation provenance fields
        cursor.execute("""
            DO $$
            BEGIN
                IF NOT EXISTS (
                    SELECT 1 FROM information_schema.columns
                    WHERE table_name='column_metadata' AND column_name='is_llm_generated'
                ) THEN
                    ALTER TABLE column_metadata
                        ADD COLUMN is_llm_generated BOOLEAN   DEFAULT FALSE,
                        ADD COLUMN confidence        FLOAT     DEFAULT NULL,
                        ADD COLUMN generated_at      TIMESTAMP DEFAULT NULL;
                END IF;
            END $$
        """)
```

- [ ] **Step 3: Verify migration applies cleanly**

```bash
python -c "from db.pg_init import init_db; init_db('')"
```

Expected: no errors.

```bash
python -c "
from db.connection import get_connection, get_pg_dsn
conn = get_connection(get_pg_dsn())
cur = conn.cursor()
cur.execute(\"SELECT column_name FROM information_schema.columns WHERE table_name='column_metadata' ORDER BY ordinal_position\")
print([r[0] for r in cur.fetchall()])
"
```

Expected: list includes `'is_llm_generated'`, `'confidence'`, `'generated_at'`.

- [ ] **Step 4: Commit**

```bash
git add db/migrations/006_column_metadata_annotation_fields.sql db/pg_init.py
git commit -m "feat(db): add annotation provenance fields to column_metadata"
```

---

## Task 2: Annotation prompt

**Files:**
- Create: `prompts/annotation.py`
- Test: `tests/test_metadata_annotation.py` (start file here)

- [ ] **Step 1: Write the failing test**

Create `tests/test_metadata_annotation.py`:

```python
"""Tests for MetadataAnnotationService and build_annotation_prompt."""
import json
from unittest.mock import MagicMock, patch

import pytest

from prompts.annotation import build_annotation_prompt


def test_build_annotation_prompt_contains_all_inputs():
    prompt = build_annotation_prompt(
        domain="real_estate",
        domain_description="Real estate property listings — Toronto/Canada focus",
        table_name="raw_data",
        column_name="postal_code",
        sample_values=["M5V 2T6", "K1A 0A9"],
    )
    assert "real_estate" in prompt
    assert "Real estate property listings" in prompt
    assert "raw_data" in prompt
    assert "postal_code" in prompt
    assert "M5V 2T6" in prompt


def test_build_annotation_prompt_empty_samples_says_none():
    prompt = build_annotation_prompt("test", "Test domain", "raw_data", "ref_1", [])
    assert "none available" in prompt
```

- [ ] **Step 2: Run to verify it fails**

```bash
python -m pytest tests/test_metadata_annotation.py::test_build_annotation_prompt_contains_all_inputs -v
```

Expected: `ModuleNotFoundError: No module named 'prompts.annotation'`

- [ ] **Step 3: Create `prompts/annotation.py`**

```python
def build_annotation_prompt(
    domain: str,
    domain_description: str,
    table_name: str,
    column_name: str,
    sample_values: list,
) -> str:
    samples_str = str(sample_values) if sample_values else "none available"
    return (
        f"Domain: {domain}\n"
        f"Domain context: {domain_description}\n"
        f"Table: {table_name}\n"
        f"Column: {column_name}\n"
        f"Sample values (may be empty): {samples_str}\n\n"
        "Respond with JSON only:\n"
        '{"description": "<one sentence: what this column stores, expected format, any known constraints>",'
        ' "confidence": <0.0-1.0 float>}\n\n'
        "Rules:\n"
        "- description must be ≤ 120 characters\n"
        "- Use domain context to resolve ambiguous column names"
        " (e.g. 'price' means listing price in real estate)\n"
        "- confidence < 0.70 means the column name/samples remain ambiguous"
        " even with domain context\n"
        "- Do not hallucinate constraints not evident from name, samples, or domain context\n"
    )
```

- [ ] **Step 4: Run tests — expect PASS**

```bash
python -m pytest tests/test_metadata_annotation.py -v -k "prompt"
```

Expected: 2 passed.

- [ ] **Step 5: Commit**

```bash
git add prompts/annotation.py tests/test_metadata_annotation.py
git commit -m "feat(prompts): add build_annotation_prompt for column annotation"
```

---

## Task 3: MetadataAnnotationService

**Files:**
- Create: `services/__init__.py`
- Create: `services/metadata_annotation.py`
- Test: `tests/test_metadata_annotation.py` (append)

- [ ] **Step 1: Write failing tests — append to `tests/test_metadata_annotation.py`**

```python
from services.metadata_annotation import AnnotationReport, MetadataAnnotationService


# Helper: build a mock conn whose cursor().fetchall() returns results in sequence
def _mock_conn(*fetchall_results):
    conn = MagicMock()
    cur = conn.cursor.return_value.__enter__.return_value
    cur.fetchall.side_effect = list(fetchall_results)
    return conn, cur


# --- list_gaps ---

def test_list_gaps_returns_unannotated_columns():
    svc = MetadataAnnotationService(llm_client=None)
    conn, _ = _mock_conn(
        [("raw_data", "postal_code")],          # existing annotations
        [("id",), ("postal_code",), ("city",)],  # raw_data columns
        [],                                      # cleaned_data columns
    )
    gaps = svc.list_gaps("real_estate", conn, tables=["raw_data", "cleaned_data"])
    assert {"table_name": "raw_data", "column_name": "id"} in gaps
    assert {"table_name": "raw_data", "column_name": "city"} in gaps
    assert {"table_name": "raw_data", "column_name": "postal_code"} not in gaps


def test_list_gaps_empty_when_all_annotated():
    svc = MetadataAnnotationService(llm_client=None)
    conn, _ = _mock_conn(
        [("raw_data", "city")],   # existing
        [("city",)],              # raw_data columns
    )
    assert svc.list_gaps("real_estate", conn, tables=["raw_data"]) == []


# --- run ---

def test_run_annotates_gaps_and_returns_report():
    llm = MagicMock()
    llm.messages_create.return_value.content = [
        MagicMock(text='{"description": "City name field", "confidence": 0.90}')
    ]
    svc = MetadataAnnotationService(llm_client=llm)
    conn, cur = _mock_conn(
        [],            # no existing annotations
        [("city",)],   # raw_data columns
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
        [("raw_data", "city")],   # already annotated
        [("city",)],
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
        MagicMock(text='{"description": "Unknown ref field", "confidence": 0.40}')
    ]
    svc = MetadataAnnotationService(llm_client=llm)
    conn, _ = _mock_conn(
        [],
        [("ref_1",)],
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
    conn, _ = _mock_conn([], [("city",)], [])
    with patch("services.metadata_annotation.SeederRegistry") as mock_sr:
        mock_sr.return_value.manifest = {"description": "Test domain"}
        report = svc.run("test_domain", conn, tables=["raw_data"])

    assert report.annotated == 1
    assert report.low_confidence[0]["confidence"] < 0.70
```

- [ ] **Step 2: Run to verify tests fail**

```bash
python -m pytest tests/test_metadata_annotation.py -v -k "not prompt"
```

Expected: `ModuleNotFoundError: No module named 'services'`

- [ ] **Step 3: Create `services/__init__.py`**

```python
```
(empty file)

- [ ] **Step 4: Create `services/metadata_annotation.py`**

```python
"""LLM-driven column annotation service — populates column_metadata for any domain."""

from __future__ import annotations

import json
import logging
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Optional

from seeders.registry import SeederRegistry

logger = logging.getLogger(__name__)


@dataclass
class AnnotationReport:
    domain: str
    annotated: int = 0
    skipped: int = 0
    low_confidence: list = field(default_factory=list)  # [{table_name, column_name, confidence}]


class MetadataAnnotationService:
    DEFAULT_TABLES = ["raw_data", "cleaned_data"]
    LOW_CONFIDENCE_THRESHOLD = 0.70
    ANNOTATION_SYSTEM = "You are a database column annotator. Output JSON only."

    def __init__(self, llm_client: Optional[Any] = None):
        self._llm = llm_client

    # ── Public API ──────────────────────────────────────────────────────────

    def list_gaps(self, domain: str, conn, tables: list[str] = None) -> list[dict]:
        """Return [{table_name, column_name}] lacking annotation for domain."""
        tables = tables or self.DEFAULT_TABLES
        existing = self._get_existing_annotations(domain, conn)
        gaps = []
        for table in tables:
            for col in self._get_table_columns(table, conn):
                if (table, col) not in existing:
                    gaps.append({"table_name": table, "column_name": col})
        return gaps

    def run(
        self,
        domain: str,
        conn,
        force: bool = False,
        tables: list[str] = None,
    ) -> AnnotationReport:
        """Annotate unannotated columns for domain. Skips existing unless force=True."""
        tables = tables or self.DEFAULT_TABLES
        try:
            sr = SeederRegistry(domain)
            domain_description = sr.manifest.get("description", domain)
        except FileNotFoundError:
            domain_description = domain

        existing = self._get_existing_annotations(domain, conn)
        report = AnnotationReport(domain=domain)

        for table in tables:
            for column in self._get_table_columns(table, conn):
                if (table, column) in existing and not force:
                    report.skipped += 1
                    continue

                result = self._annotate_column(
                    domain, domain_description, table, column, conn
                )
                self._upsert_annotation(
                    domain, table, column,
                    result["description"], result["confidence"],
                    conn, force,
                )
                report.annotated += 1
                if result["confidence"] < self.LOW_CONFIDENCE_THRESHOLD:
                    report.low_confidence.append(
                        {"table_name": table, "column_name": column,
                         "confidence": result["confidence"]}
                    )

        return report

    # ── Private helpers ─────────────────────────────────────────────────────

    def _get_existing_annotations(self, domain: str, conn) -> set:
        with conn.cursor() as cur:
            cur.execute(
                "SELECT table_name, column_name FROM column_metadata WHERE domain = %s",
                (domain,),
            )
            return {(row[0], row[1]) for row in cur.fetchall()}

    def _get_table_columns(self, table: str, conn) -> list[str]:
        with conn.cursor() as cur:
            cur.execute(
                "SELECT column_name FROM information_schema.columns "
                "WHERE table_name = %s ORDER BY ordinal_position",
                (table,),
            )
            return [row[0] for row in cur.fetchall()]

    def _get_sample_values(self, table: str, column: str, conn, n: int = 5) -> list:
        try:
            with conn.cursor() as cur:
                cur.execute(
                    f'SELECT "{column}" FROM "{table}" WHERE "{column}" IS NOT NULL LIMIT %s',  # noqa: S608
                    (n,),
                )
                return [row[0] for row in cur.fetchall()]
        except Exception:
            return []

    def _annotate_column(
        self,
        domain: str,
        domain_description: str,
        table: str,
        column: str,
        conn,
    ) -> dict:
        from prompts.annotation import build_annotation_prompt

        samples = self._get_sample_values(table, column, conn)
        prompt = build_annotation_prompt(domain, domain_description, table, column, samples)

        try:
            resp = self._llm.messages_create(
                system=self.ANNOTATION_SYSTEM,
                messages=[{"role": "user", "content": prompt}],
                tools=[],
                max_tokens=256,
            )
            text = next((b.text for b in resp.content if hasattr(b, "text")), "{}")
            result = json.loads(text.strip())
            return {
                "description": str(result.get("description", ""))[:120],
                "confidence": float(result.get("confidence", 0.5)),
            }
        except Exception:
            return {"description": column.replace("_", " "), "confidence": 0.3}

    def _upsert_annotation(
        self,
        domain: str,
        table: str,
        column: str,
        description: str,
        confidence: float,
        conn,
        force: bool,
    ) -> None:
        now = datetime.now(timezone.utc)
        with conn.cursor() as cur:
            if force:
                cur.execute(
                    """
                    INSERT INTO column_metadata
                        (domain, table_name, column_name, description,
                         is_llm_generated, confidence, generated_at)
                    VALUES (%s, %s, %s, %s, TRUE, %s, %s)
                    ON CONFLICT (domain, table_name, column_name) DO UPDATE
                      SET description      = EXCLUDED.description,
                          is_llm_generated = TRUE,
                          confidence       = EXCLUDED.confidence,
                          generated_at     = EXCLUDED.generated_at
                    """,
                    (domain, table, column, description, confidence, now),
                )
            else:
                cur.execute(
                    """
                    INSERT INTO column_metadata
                        (domain, table_name, column_name, description,
                         is_llm_generated, confidence, generated_at)
                    VALUES (%s, %s, %s, %s, TRUE, %s, %s)
                    ON CONFLICT (domain, table_name, column_name) DO NOTHING
                    """,
                    (domain, table, column, description, confidence, now),
                )
        conn.commit()
```

- [ ] **Step 5: Run tests — expect PASS**

```bash
python -m pytest tests/test_metadata_annotation.py -v
```

Expected: all tests pass.

- [ ] **Step 6: Commit**

```bash
git add services/__init__.py services/metadata_annotation.py tests/test_metadata_annotation.py
git commit -m "feat(services): add MetadataAnnotationService with LLM-driven column annotation"
```

---

## Task 4: CLI

**Files:**
- Create: `scripts/annotate_domain.py`

- [ ] **Step 1: Write test — append to `tests/test_metadata_annotation.py`**

```python
import io
import sys
from unittest.mock import patch, MagicMock


def test_cli_dry_run_prints_gaps(capsys):
    """Dry run prints gaps without writing to DB."""
    import scripts.annotate_domain as cli_module

    with patch.object(cli_module, "get_db_connection", return_value=MagicMock()), \
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
         patch("services.metadata_annotation.MetadataAnnotationService.list_gaps",
               return_value=[]), \
         patch("sys.argv", ["annotate_domain.py", "--domain", "real_estate", "--dry-run"]):
        cli_module.main()

    captured = capsys.readouterr()
    assert "No annotation gaps" in captured.out
```

- [ ] **Step 2: Run to verify tests fail**

```bash
python -m pytest tests/test_metadata_annotation.py::test_cli_dry_run_prints_gaps tests/test_metadata_annotation.py::test_cli_dry_run_no_gaps_message -v
```

Expected: `ModuleNotFoundError` (script missing).

- [ ] **Step 3: Create `scripts/annotate_domain.py`**

```python
#!/usr/bin/env python3
"""CLI: generate LLM annotations for domain columns in column_metadata."""

import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from db.pg_init import get_db_connection
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

    conn = get_db_connection("")

    if args.dry_run:
        svc = MetadataAnnotationService(llm_client=None)
        gaps = svc.list_gaps(args.domain, conn)
        if not gaps:
            print(f"No annotation gaps found for domain '{args.domain}'.")
            return
        print(f"Annotation gaps for '{args.domain}' ({len(gaps)} columns):")
        for g in gaps:
            print(f"  {g['table_name']}.{g['column_name']}")
        return

    svc = MetadataAnnotationService(llm_client=_build_llm_client())
    print(f"Annotating {args.domain}...")
    report = svc.run(args.domain, conn, force=args.force)

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

- [ ] **Step 4: Run tests — expect PASS**

```bash
python -m pytest tests/test_metadata_annotation.py::test_cli_dry_run_prints_gaps tests/test_metadata_annotation.py::test_cli_dry_run_no_gaps_message -v
```

- [ ] **Step 5: Smoke test dry-run manually (requires DB)**

```bash
python scripts/annotate_domain.py --domain real_estate --dry-run
```

Expected: list of unannotated columns or "No annotation gaps found".

- [ ] **Step 6: Commit**

```bash
git add scripts/annotate_domain.py tests/test_metadata_annotation.py
git commit -m "feat(scripts): add annotate_domain CLI for on-demand column annotation"
```

---

## Task 5: SkillPlanner annotation enrichment

**Files:**
- Modify: `skills/_common/skill_planner/skill_planner.py`
- Test: `tests/test_skill_planner.py` (append)

- [ ] **Step 1: Write failing test — append to `tests/test_skill_planner.py`**

```python
def test_build_prompt_includes_annotation_context():
    """When column_metadata has rows for the domain, _build_prompt includes them."""
    registry = SkillRegistry.load("real_estate")
    planner = SkillPlanner()
    planner.domain = "real_estate"

    mock_conn = MagicMock()
    cur = mock_conn.cursor.return_value.__enter__.return_value
    cur.fetchall.return_value = [
        ("raw_data", "postal_code", "CA/US postal code. Format: A1A 1A1 (CA) or 5 digits (US)."),
    ]
    planner.conn = mock_conn

    record = {"postal_code": "M5V", "city": "Toronto"}
    menu = planner._build_menu(registry)
    prompt = planner._build_prompt(record, menu)

    assert "postal_code" in prompt
    assert "CA/US postal code" in prompt


def test_build_prompt_skips_annotation_context_when_no_conn():
    """No DB conn → prompt still works, no annotation block."""
    registry = SkillRegistry.load("real_estate")
    planner = SkillPlanner()
    planner.domain = "real_estate"
    planner.conn = None

    record = {"postal_code": "M5V"}
    menu = planner._build_menu(registry)
    prompt = planner._build_prompt(record, menu)

    assert "Column Annotations" not in prompt
    assert "postal_code" in prompt  # still in record
```

- [ ] **Step 2: Run to verify tests fail**

```bash
python -m pytest tests/test_skill_planner.py::test_build_prompt_includes_annotation_context tests/test_skill_planner.py::test_build_prompt_skips_annotation_context_when_no_conn -v
```

Expected: FAIL (method doesn't exist yet).

- [ ] **Step 3: Add `_get_annotation_context` and update `_build_prompt` in `skills/_common/skill_planner/skill_planner.py`**

Add this method to `SkillPlanner` (after `_build_menu`, before `_build_prompt`):

```python
    def _get_annotation_context(self) -> str:
        """Query column_metadata for domain annotations. Returns '' if unavailable."""
        if not self.conn or not self.domain:
            return ""
        try:
            with self.conn.cursor() as cur:
                cur.execute(
                    "SELECT table_name, column_name, description "
                    "FROM column_metadata WHERE domain = %s "
                    "ORDER BY table_name, column_name",
                    (self.domain,),
                )
                rows = cur.fetchall()
            if not rows:
                return ""
            lines = [f"## Column Annotations (domain: {self.domain})"]
            for table, col, desc in rows:
                lines.append(f"{table}.{col}: {desc}")
            return "\n".join(lines) + "\n\n"
        except Exception:
            return ""
```

Update `_build_prompt` to prepend annotation context:

```python
    def _build_prompt(self, record: dict, menu: List[dict]) -> str:
        safe_record = {k: v for k, v in record.items() if not k.startswith("_") or k in (
            "_triage_route", "_triage_data_confidence", "_gap_hints", "_unknown_fsa",
            "_municipality_confidence",
        )}
        menu_text = json.dumps(
            [{"name": m["name"], "cost": m["cost"], "depends_on": m["depends_on"]} for m in menu],
            indent=2,
        )
        annotation_context = self._get_annotation_context()
        return (
            f"{annotation_context}"
            f"Record:\n{json.dumps(safe_record, indent=2)}\n\n"
            f"Available skills:\n{menu_text}\n\n"
            "Output JSON plan."
        )
```

- [ ] **Step 4: Run tests — expect PASS**

```bash
python -m pytest tests/test_skill_planner.py -v
```

Expected: all pass (including pre-existing tests).

- [ ] **Step 5: Commit**

```bash
git add skills/_common/skill_planner/skill_planner.py tests/test_skill_planner.py
git commit -m "feat(planner): enrich planning prompt with column_metadata annotations"
```

---

## Task 6: OrchestrationTeam annotation gap warning

**Files:**
- Modify: `cleaning/orchestrator_v2.py`
- Test: `tests/cleaning/test_spell_corrections.py` is an example — write new test in `tests/test_metadata_annotation.py`

- [ ] **Step 1: Write failing test — append to `tests/test_metadata_annotation.py`**

```python
from cleaning.orchestrator_v2 import OrchestrationTeam
from skills.registry import SkillRegistry
import logging


def test_orchestration_team_warns_on_annotation_gaps(caplog):
    """OrchestrationTeam warns at init when domain columns lack annotations."""
    registry = MagicMock(spec=SkillRegistry)
    registry.get.return_value = None
    registry.metadata = {}
    registry.domain = "real_estate"

    mock_conn = MagicMock()
    cur = mock_conn.cursor.return_value.__enter__.return_value
    # list_gaps will see: no existing annotations, one column in raw_data
    cur.fetchall.side_effect = [
        [],           # _get_existing_annotations: no annotations
        [("city",)],  # _get_table_columns raw_data
        [],           # _get_table_columns cleaned_data
    ]
    registry.runtime = {"pg_conn": mock_conn}

    with caplog.at_level(logging.WARNING, logger="cleaning.orchestrator_v2"):
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
    # All columns already annotated
    cur.fetchall.side_effect = [
        [("raw_data", "city"), ("cleaned_data", "city")],  # existing
        [("city",)],                                        # raw_data cols
        [("city",)],                                        # cleaned_data cols
    ]
    registry.runtime = {"pg_conn": mock_conn}

    with caplog.at_level(logging.WARNING, logger="cleaning.orchestrator_v2"):
        OrchestrationTeam(registry)

    assert not any("annotation" in msg.lower() for msg in caplog.messages)
```

- [ ] **Step 2: Run to verify tests fail**

```bash
python -m pytest tests/test_metadata_annotation.py::test_orchestration_team_warns_on_annotation_gaps tests/test_metadata_annotation.py::test_orchestration_team_no_warning_when_annotated -v
```

Expected: FAIL (method missing).

- [ ] **Step 3: Add domain attribute to SkillRegistry**

In `skills/registry.py`, add `self.domain: str = ""` to `__init__` and set it in `load_domain`:

In `__init__` (after line `self.runtime: Dict[str, Any] = {}`):
```python
        self.domain: str = ""
```

In `load_domain` (first line of the method body, before opening the yaml file):
```python
        self.domain = domain
```

- [ ] **Step 4: Add `_warn_annotation_gaps` to `OrchestrationTeam` in `cleaning/orchestrator_v2.py`**

Add after the `__init__` method body (call it at end of `__init__`):

```python
    def __init__(self, registry: SkillRegistry, batch_budget: Optional[BatchBudget] = None):
        self.registry = registry
        self.batch_budget = batch_budget
        self.planner = registry.get("skill_planner")
        self.triage_skill = registry.get("data_quality_triage")
        self._warn_annotation_gaps()

    def _warn_annotation_gaps(self) -> None:
        """Warn once at session start if domain columns lack annotations."""
        conn = self.registry.runtime.get("pg_conn") if hasattr(self.registry, "runtime") else None
        domain = getattr(self.registry, "domain", None)
        if not conn or not domain:
            return
        try:
            from services.metadata_annotation import MetadataAnnotationService
            gaps = MetadataAnnotationService(llm_client=None).list_gaps(domain, conn)
            if gaps:
                logger.warning(
                    "%d column(s) in '%s' have no annotations. "
                    "Run: python scripts/annotate_domain.py --domain %s",
                    len(gaps), domain, domain,
                )
        except Exception:
            pass
```

- [ ] **Step 5: Run tests — expect PASS**

```bash
python -m pytest tests/test_metadata_annotation.py -v
```

Expected: all pass.

```bash
python -m pytest tests/ -v
```

Expected: full suite passes (no regressions).

- [ ] **Step 6: Commit**

```bash
git add cleaning/orchestrator_v2.py skills/registry.py tests/test_metadata_annotation.py
git commit -m "feat(orchestrator): warn at session start when domain columns lack annotations"
```

---

## Task 7: CLAUDE.md + final verification

**Files:**
- Modify: `CLAUDE.md`

- [ ] **Step 1: Add annotate_domain to CLAUDE.md Bootstrap section**

In `CLAUDE.md`, after the `python scripts/init_data.py` examples block, add:

```markdown
## Annotate Domain Columns

Generate LLM descriptions for each column in `column_metadata` (run after seeding):

```bash
# Preview unannotated columns (no writes)
python scripts/annotate_domain.py --domain real_estate --dry-run

# Annotate all gaps
python scripts/annotate_domain.py --domain real_estate

# Overwrite existing LLM-generated annotations
python scripts/annotate_domain.py --domain real_estate --force
```

Annotations are stored in `column_metadata` with `is_llm_generated=TRUE` and a `confidence` score. Columns with confidence < 0.70 are flagged in the output — review and edit them directly in the DB or re-run after improving `prompts/annotation.py`.
```

- [ ] **Step 2: Run full test suite**

```bash
python -m pytest tests/ -v
```

Expected: all tests pass.

- [ ] **Step 3: Run CLI dry-run end-to-end (requires DB)**

```bash
python scripts/annotate_domain.py --domain real_estate --dry-run
```

- [ ] **Step 4: Final commit**

```bash
git add CLAUDE.md
git commit -m "docs: document annotate_domain CLI in CLAUDE.md"
```
