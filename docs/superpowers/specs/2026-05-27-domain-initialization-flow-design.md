# Domain Initialization Flow — Design Spec

**Date:** 2026-05-27  
**Status:** Approved  
**Scope:** New `initialize_domain.py` orchestrator + `add_table` subcommand + supporting changes to annotation service, domain researcher, and domain registry.

---

## Problem

The current domain setup is broken into disconnected, order-dependent scripts with no single entry point:

- `annotate_domain.py` hardcodes `DEFAULT_TABLES = ["raw_data", "cleaned_data"]` — wrong for any real domain
- `research_domain.py` generates seed content without knowing the actual schema — Q&A answers are the only context, so the LLM guesses column names and produces off-domain output
- There is no Phase 0: no mechanism to record which DB tables belong to a domain
- There is no orchestrator: the user must know the right call order themselves
- Spell corrections are LLM-guessed upfront with no real data to look at

---

## Goal

Two entry points that cover the full domain lifecycle:

```bash
# First-time full initialization
python scripts/initialize_domain.py --domain sports_ticketing

# Add tables after DB schema expands
python scripts/initialize_domain.py --domain sports_ticketing add_table
```

---

## Architecture: Four Phases (initialize)

### Phase 0 — Table Registration *(one-time per domain)*

**Trigger:** Domain has no `tables` entry in `domain_registry.json`.  
**On repeat runs:** Skip entirely; load tables from registry and print *"Using registered tables: events, tickets, customers"*.

**Logic:**

1. Query DB for all tables in the public schema
2. Flag known system/seed tables (e.g. `column_metadata`, `spell_corrections`, `query_pattern_memory`, `plan_cache`) so the user knows to skip them
3. If ≤ 15 non-system tables: present full list, user selects which belong to this domain
4. If > 15 non-system tables: score each table name by keyword overlap with domain name + entity words list. Present top-scored candidates, user confirms or adjusts selection.
5. Write selected tables into `domain_registry.json` under `domains.<domain>.tables`

**System table list** (never auto-selected, flagged with `← system table`):
`column_metadata`, `spell_corrections`, `query_pattern_memory`, `plan_cache`, `municipality_lookup_cache`, `source_registry`

**Output example:**
```
══════════════════════════════════════════════════
  Phase 0 — Table Registration
══════════════════════════════════════════════════
Found 6 tables in database.

Select tables that belong to 'sports_ticketing':
  [1] events
  [2] tickets
  [3] customers
  [4] column_metadata       ← system table, likely skip
  [5] spell_corrections     ← system table, likely skip
  [6] query_pattern_memory  ← system table, likely skip

Enter numbers (comma-separated): 1,2,3

Registered: events, tickets, customers → domain_registry.json
```

---

### Phase 1 — Schema Discovery

**Input:** Declared tables from registry.  
**Output:** In-memory schema dict passed to Phase 2 and Phase 3. Printed summary.

**Logic:**

1. For each declared table: query `information_schema.columns` for column name, data type, nullable, primary key
2. Print table-by-table summary
3. Pause: *"Schema loaded. Continue to annotation? [Y/n]"*

**Output example:**
```
══════════════════════════════════════════════════
  Phase 1 — Schema Discovery
══════════════════════════════════════════════════
Scanning 3 tables...

  events (8 columns)
    event_id          uuid         NOT NULL  PK
    event_name        text
    start_datetime    timestamptz
    end_datetime      timestamptz
    home_team         text
    away_team         text
    venue_name        text
    event_type        text

  tickets (7 columns)
    ticket_id         uuid         NOT NULL  PK
    event_id          uuid
    section           text
    row               text
    seat              text
    price             numeric
    ticket_status     text

  customers (9 columns)
    ...

Schema loaded. Continue to annotation? [Y/n]
```

---

### Phase 2 — Annotation

**Input:** Schema from Phase 1 + domain name.  
**Output:** `column_metadata` rows in DB. Printed report.

**Logic:**

1. For each table in declared tables *(not hardcoded `DEFAULT_TABLES`)*:
   - For each column: check if annotation already exists in `column_metadata`
   - If missing (or `--force`): call LLM to generate description + confidence
   - Print per-column progress as it goes: *"  Annotating events.home_team... done (confidence=0.91)"*
   - Write result to `column_metadata`
2. At end: print count of annotated, skipped, low-confidence (<0.70)
3. Pause: *"Annotation complete. Review above. Continue to seed research? [Y/n]"*

**Key fix:** `MetadataAnnotationService` drops `DEFAULT_TABLES = ["raw_data", "cleaned_data"]`. Tables are passed in explicitly from the registry.

**Output example:**
```
══════════════════════════════════════════════════
  Phase 2 — Annotation
══════════════════════════════════════════════════
Annotating 24 columns across 3 tables...

  events.event_id          ... skipped (exists)
  events.event_name        ... done (confidence=0.93)
  events.home_team         ... done (confidence=0.91)
  events.away_team         ... done (confidence=0.91)
  events.venue_name        ... done (confidence=0.88)
  tickets.price            ... done (confidence=0.85)
  customers.postal_code    ... ⚠ low confidence (0.62) — review recommended
  ...

Done: 21 annotated, 3 skipped, 1 low-confidence.
Low-confidence columns:
  customers.postal_code  (0.62)

Annotation complete. Review above. Continue to seed research? [Y/n]
```

---

### Phase 3 — Seed Research

**Input:** Schema (Phase 1) + annotations from `column_metadata` (Phase 2) + data samples + Q&A answers.  
**Output:** Seed files written to `data/seeds/<domain>/`; loaded to DB via `SeederRegistry`.

**Spell corrections are data-driven, not pre-guessed.** Before Q&A, Phase 3 samples actual records from text columns. If columns are empty, spell correction generation is skipped and flagged.

**Logic:**

1. Load schema + annotations — build a compact context block for the LLM
2. **Sample data:** For each `text`/`varchar` column in declared tables, fetch up to 50 non-null values
   - If all text columns are empty: print *"⚠ No data found in text columns. Spell correction generation skipped. Re-run with `--refresh-seeds` after data is ingested."* Skip spell corrections; continue with query packs + column metadata only.
   - If data present: include samples in LLM context for evidence-based correction generation
3. Generate schema-filtered Q&A question list (see table below)
4. Run interactive Q&A
5. Build LLM prompt: schema context + annotation summaries + data samples + Q&A answers
6. Call LLM → parse response → show preview
7. Confirm → write seed files to `data/seeds/<domain>/`
8. Instantiate `SeederRegistry(domain)` directly and call `run_all(conn)` — same process, no subprocess

**Output example:**
```
══════════════════════════════════════════════════
  Phase 3 — Seed Research
══════════════════════════════════════════════════
Schema loaded: 24 columns across 3 tables.
Annotations loaded: 21 descriptions.
Sampling data from text columns...
  events.home_team      → 47 samples found
  events.venue_name     → 47 samples found
  customers.city        → 312 samples found
  customers.postal_code → 0 samples  ← will skip spell corrections for this column

Asking 6 questions tailored to your schema...
(home_team/away_team detected → asking about team name conventions)
(timestamptz columns detected → asking about timezone context)

[Q&A interaction...]

Connecting to LLM to generate seed content...

Preview:
  Spell corrections: 12  (evidence-based from 406 sampled values)
  Query packs: 5 gap types
  Column descriptions: 24

Write seed files? [y/N]

Writing data/seeds/sports_ticketing/spell_corrections.csv... done
Writing data/seeds/sports_ticketing/query_packs.yaml... done
Writing data/seeds/sports_ticketing/column_metadata.yaml... done

Loading seeds to database...
  spell_corrections: 12 rows upserted
  query_pattern_memory: 5 gap types, 18 queries upserted

══════════════════════════════════════════════════
  Domain 'sports_ticketing' initialization complete.
══════════════════════════════════════════════════
```

---

## Subcommand: `add_table`

**Purpose:** Register new tables added to the DB after initial initialization. Smart diff — shows only what's new.

```bash
python scripts/initialize_domain.py --domain sports_ticketing add_table
```

**Logic:**

1. Load currently registered tables from `domain_registry.json`
2. Query DB for all current tables
3. Diff: `db_tables - registered_tables - system_tables`
4. If diff is empty: print *"No new tables found. Domain is up to date."* and exit
5. If diff is non-empty: same selection UX as Phase 0 — present candidates, user selects
6. For large diffs (> 15 new tables): apply same keyword scoring as Phase 0
7. Append selected tables to `domain_registry.json`
8. Run Phase 2 (annotation) for:
   - All columns in newly added tables
   - Any unannotated columns in existing registered tables (catches columns added since last run)
   - Existing annotations are never overwritten unless `--force` is passed
9. Prompt: *"Seeds were generated from previous schema. Refresh seeds with new table context? [y/N]"*
   - Yes → re-run Phase 3 with full schema (including new tables) + fresh data samples
   - No → done

**Output example:**
```
══════════════════════════════════════════════════
  add_table — sports_ticketing
══════════════════════════════════════════════════
Currently registered: events, tickets, customers
Scanning database for new tables...

New tables found (not yet registered):
  [1] venues
  [2] promotions
  [3] flyway_schema_history   ← system table, likely skip

Enter numbers to add (comma-separated, Enter to skip all): 1,2

Adding: venues, promotions → domain_registry.json

══════════════════════════════════════════════════
  Annotating new tables...
══════════════════════════════════════════════════
  venues.venue_id       ... done (confidence=0.94)
  venues.venue_name     ... done (confidence=0.92)
  ...

Seeds were generated from previous schema. Refresh seeds with new table context? [y/N]
```

---

## `--refresh-seeds` Flag

Re-runs Phase 3 only, against current registered tables + fresh data samples. Does not re-run Phase 0, 1, or 2.

```bash
python scripts/initialize_domain.py --domain sports_ticketing --refresh-seeds
```

Use when:
- Initial Phase 3 was skipped because tables were empty
- Data has grown significantly and spell corrections are stale
- Schema expanded and seeds need to reflect new columns

---

## File Changes

### New Files

| File | Purpose |
|------|---------|
| `scripts/initialize_domain.py` | Thin orchestrator — phases 0-3 + `add_table` subcommand |
| `services/domain_initializer.py` | Phase 0 + `add_table` logic: table discovery, keyword scoring, diff, registry write |

### Modified Files

| File | Change |
|------|--------|
| `services/metadata_annotation.py` | Remove `DEFAULT_TABLES`; `run()` and `list_gaps()` accept explicit `tables: list[str]` param |
| `scripts/annotate_domain.py` | Load `tables` from `domain_registry.json`; if domain not registered, exit with clear message |
| `seeders/domain_researcher.py` | Add `research_with_schema()`: accepts schema dict + annotations dict + data samples → injects as context in LLM prompt |
| `scripts/research_domain.py` | Load schema + annotations + samples first; pass to `research_with_schema()`; filter Q&A by column presence |
| `data/domain_registry.json` | Add `tables: [...]` field under each domain entry |

### Unchanged Files

| File | Why |
|------|-----|
| `scripts/init_data.py` | Still independently runnable; `SeederRegistry` used directly in Phase 3 |
| `db/schema_discovery.py` | Used as-is |

---

## Progress Output Contract

Every phase and subcommand prints a double-line header:
```
══════════════════════════════════════════════════
  Phase N — Phase Name
══════════════════════════════════════════════════
```

Per-item lines within phases use indented dot-leader format:
```
  table.column_name     ... done (confidence=0.93)
  table.column_name     ... skipped (exists)
  table.column_name     ... ⚠ low confidence (0.62)
```

Sampling lines:
```
  table.column_name     → 47 samples found
  table.column_name     → 0 samples  ← will skip spell corrections for this column
```

Pause prompts are always explicit `[Y/n]` or `[y/N]` — uppercase letter is the default.

---

## Schema-Filtered Q&A Questions (Phase 3)

| Question | Activation Condition |
|----------|-----------|
| Entity description | Always |
| Main fields (pre-filled from schema, user adds context) | Always |
| Which text fields have spelling errors | Any `text`/`varchar` column AND data samples present |
| Team/player name aliases and abbreviations | Column name contains `team`, `player`, `athlete` |
| Postal/zip format and country context | Column name contains `postal`, `zip`, `postcode` |
| Date/time format and timezone notes | Any `timestamp`/`date` column present |
| Gap types requiring web search | Always |
| Trusted authoritative sources | Always |
| Additional domain context | Always (skippable) |

---

## Keyword Scoring (Phase 0 + add_table, large table sets)

When non-system table count > 15, score each table name against this word list:

```python
DOMAIN_ENTITY_WORDS = {
    "sports_ticketing": ["event", "ticket", "customer", "fan", "venue", "team",
                         "seat", "section", "purchase", "account", "booking",
                         "price", "sport", "game", "match", "player"],
    "_generic": ["record", "data", "entry", "item", "entity", "profile"],
}
```

Score = number of word overlaps between table name tokens and domain word list. Present top 10.

---

## System Tables (never auto-selected)

`column_metadata`, `spell_corrections`, `query_pattern_memory`, `plan_cache`,
`municipality_lookup_cache`, `source_registry`

These are flagged with `← system table` in selection lists but can still be manually added if the user chooses.

---

## Out of Scope

- `--tables` CLI flag (Option B) — deferred
- LLM-based table classification for large DBs (Option C) — future
- Schema migration generation — not in scope
  - **Revised 2026-05-31:** DDL is now generated for **auxiliary** lookup/cleaning/reference
    tables (keyed to join back to the user's data); the user's *source/main* tables are still
    never created or altered. See `2026-05-31-integration-config-store-design.md`.
- Multi-backend migration (Snowflake, DuckDB) — not in scope
