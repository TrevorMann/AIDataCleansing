# Metadata Annotation Service — Design Spec

**Date:** 2026-05-13  
**Status:** Approved for implementation  
**Branch:** feature/metadata-annotation-service (new)

---

## Context

`column_metadata` table exists but is populated by a hardcoded `_seed_column_metadata()` in `pg_init.py` (domain=`"base"`, ~18 rows). New domains (e.g., `sports_ticketing`) get no annotations today. The SkillPlanner plans skills from `skill.md` files with no DB schema context. This creates a gap: the LLM planner doesn't know what columns mean in a given domain.

Goal: LLM-generated per-domain column annotations stored in `column_metadata`, generated on-demand via a CLI command, reused by the pipeline at plan time.

---

## Scope

**In:**
- `MetadataAnnotationService` — domain-agnostic Python class
- DB migration adding `is_llm_generated`, `confidence`, `generated_at` to `column_metadata`
- CLI: `scripts/annotate_domain.py`
- LLM prompt for annotation
- `SkillPlanner` reads annotations at plan time (soft enrichment)
- Pipeline warning when domain columns lack annotations

**Out:**
- MCP server (deferred — not needed yet)
- Automatic annotation at domain init
- Real-time schema change detection

---

## Database Changes

### Migration: `db/migrations/006_column_metadata_annotation_fields.sql`

```sql
ALTER TABLE column_metadata
  ADD COLUMN IF NOT EXISTS is_llm_generated  BOOLEAN   DEFAULT FALSE,
  ADD COLUMN IF NOT EXISTS confidence        FLOAT     DEFAULT NULL,
  ADD COLUMN IF NOT EXISTS generated_at      TIMESTAMP DEFAULT NULL;
```

Existing hardcoded base rows (`domain='base'`) keep `is_llm_generated=FALSE` (default). No data migration needed.

---

## MetadataAnnotationService

**File:** `services/metadata_annotation.py`

```python
@dataclass
class AnnotationReport:
    domain: str
    annotated: int
    skipped: int
    low_confidence: list[dict]   # [{table, column, confidence}] where confidence < 0.70

class MetadataAnnotationService:
    LOW_CONFIDENCE_THRESHOLD = 0.70

    def __init__(self, llm_client):
        self._llm = llm_client

    def run(self, domain: str, conn, force: bool = False) -> AnnotationReport:
        """
        Discover unannotated columns for domain, call LLM, upsert into column_metadata.
        Skips existing annotations unless force=True.
        """
```

### Flow

1. Query `information_schema.columns` for tables belonging to the domain (detected via `column_metadata` existing entries OR via domain prefix convention)
2. Query existing `column_metadata` rows for domain → build set of already-annotated `(table, column)` pairs
3. For each unannotated column (skip if annotated and `force=False`):
   - Fetch up to 5 sample values from the table
   - LLM call → `{"description": "...", "confidence": 0.0–1.0}`
4. Upsert into `column_metadata`:
   ```sql
   INSERT INTO column_metadata (domain, table_name, column_name, description,
                                 is_llm_generated, confidence, generated_at)
   VALUES (...)
   ON CONFLICT (domain, table_name, column_name)
   DO UPDATE SET description=EXCLUDED.description,
                 is_llm_generated=TRUE,
                 confidence=EXCLUDED.confidence,
                 generated_at=EXCLUDED.generated_at
   ```
   (only fires if `force=True`, because unannotated columns hit the INSERT path)
5. Return `AnnotationReport`

### Domain Table Discovery

Use a `DomainTableResolver` (small helper) that returns table names for a domain:
- Strategy 1: Check `seeders/<domain>/manifest.yaml` for declared tables
- Strategy 2: Fallback — query `information_schema.tables` and filter by domain-registered prefix

---

## LLM Prompt

**File:** `prompts/annotation.py`

Short, focused — Haiku-level. One call per column.

```
You are annotating database columns for a data cleaning pipeline.

Domain: {domain}
Domain context: {domain_description}
Table: {table_name}
Column: {column_name}
Sample values (may be empty or None): {sample_values}

Respond with JSON only:
{{"description": "<one sentence: what this column stores, its expected format, and any known constraints>",
  "confidence": <0.0-1.0 float: how certain you are given the name, samples, and domain context>}}

Rules:
- description must be ≤ 120 characters
- Use domain context to resolve ambiguous column names (e.g. "price" means listing price in real estate)
- confidence < 0.70 means the column name/samples remain ambiguous even with domain context
- Do not hallucinate constraints not evident from name, samples, or domain context
```

`domain_description` sourced from `seeders/<domain>/manifest.yaml` → `description` field. Falls back to the domain name string if manifest has no description.

Cache key: `(domain, table_name, column_name)` — same inputs always produce the same annotation. Use `plan_cache` table pattern (already exists in `db/migrations/005_plan_cache.sql`) or a simple in-memory dict per run.

---

## CLI

**File:** `scripts/annotate_domain.py`

```bash
# Annotate all unannotated columns for a domain
python scripts/annotate_domain.py --domain real_estate

# Show gaps without writing (dry run)
python scripts/annotate_domain.py --domain real_estate --dry-run

# Overwrite existing LLM-generated annotations
python scripts/annotate_domain.py --domain real_estate --force

# Force-overwrite even human-edited annotations (dangerous, explicit flag)
python scripts/annotate_domain.py --domain real_estate --force --overwrite-manual
```

Output:
```
Annotating real_estate...
  raw_data.listing_type        ✓ confidence=0.92
  raw_data.listing_price       ✓ confidence=0.88
  raw_data.ref_1               ⚠ confidence=0.45 (low — review recommended)
  cleaned_data.listing_type    skipped (already annotated)

Done: 2 annotated, 1 skipped, 1 low-confidence
```

`--overwrite-manual` guard: `force=True` skips columns where `is_llm_generated=FALSE` (human-edited). `--overwrite-manual` removes that guard.

---

## Pipeline Integration

### SkillPlanner enrichment

`skills/_common/skill_planner/skill_planner.py` currently builds its planning prompt from `skill.md` files. After this change, it also queries `column_metadata` for the domain and appends a compact annotation block:

```
## Column Annotations (domain: real_estate)
raw_data.postal_code: CA/US postal code. Format: A1A 1A1 (CA) or 5 digits (US).
raw_data.listing_price: Listing price in CAD/USD. May include commas or currency symbols.
...
```

This is soft enrichment — if `column_metadata` has no domain rows, the planner proceeds without it (no error).

### Pipeline warning

At `OrchestrationTeam.__init__` or at session start (not per-record), query `column_metadata` for the domain. If any columns in `information_schema` for the domain's tables lack annotations, emit:

```
WARNING: 3 columns in real_estate have no annotations.
Run: python scripts/annotate_domain.py --domain real_estate
```

Non-blocking. Logged at WARNING level only.

---

## Files to Create / Modify

| Action | File |
|--------|------|
| Create | `db/migrations/006_column_metadata_annotation_fields.sql` |
| Create | `services/__init__.py` |
| Create | `services/metadata_annotation.py` |
| Create | `prompts/annotation.py` |
| Create | `scripts/annotate_domain.py` |
| Modify | `skills/_common/skill_planner/skill_planner.py` — read annotations |
| Modify | `cleaning/orchestrator_v2.py` — emit warning on missing annotations |
| Modify | `db/pg_init.py` — run migration 006 in `init_db()` |
| Modify | `CLAUDE.md` — document annotate_domain.py usage |

---

## Verification

1. Run migration: `psql $POSTGRES_DSN -f db/migrations/006_column_metadata_annotation_fields.sql`
2. Check new columns exist: `\d column_metadata`
3. Dry-run for real_estate: `python scripts/annotate_domain.py --domain real_estate --dry-run` — should list unannotated columns
4. Run annotation: `python scripts/annotate_domain.py --domain real_estate`
5. Verify DB: `SELECT * FROM column_metadata WHERE domain='real_estate' AND is_llm_generated=TRUE`
6. Run pipeline on a real_estate record — confirm no annotation warning in logs
7. Run pipeline on sports_ticketing (no annotations) — confirm WARNING is emitted
8. Run tests: `python -m pytest tests/ -v` — no regressions
