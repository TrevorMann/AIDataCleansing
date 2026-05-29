# Runbook — Initialize a Domain (`sports_ticketing` on PostgreSQL)

Step-by-step guide for running `scripts/initialize_domain.py` end to end, plus the
teardown → re-run loop for iterative testing. Worked example uses `sports_ticketing`.

> **Backend support today:** the orchestrator is **PostgreSQL-only**. Its entry point
> connects with `get_connection(get_pg_dsn())`, which requires `POSTGRES_DSN` and assumes
> a Postgres connection. SQLite is **not yet wired** — see [SQLite](#sqlite-not-yet-supported)
> at the end for exactly what blocks it.

---

## 0. Prerequisites

### 0.1 Environment (`.env`)

```bash
# LLM backend (one is required — annotation + seed research call the LLM)
ANTHROPIC_API_KEY=sk-ant-...        # or OPENROUTER_API_KEY=sk-or-...

# PostgreSQL
POSTGRES_DSN=postgresql://user:pass@localhost:5432/cleaning_db
DB_BACKEND=postgres

# Web-search enrichment (used by the pipeline, not strictly by init)
TAVILY_API_KEY=tvly-...
```

Sanity check the DSN before you start:

```bash
psql "$POSTGRES_DSN" -c '\dt'
```

### 0.2 Framework tables

Apply the framework migrations (creates `column_metadata`, `spell_corrections`,
`query_pattern_memory`, `plan_cache`, `source_registry`, …):

```bash
python db/pg_init.py
```

### 0.3 Your domain data tables

Phase 0 only finds tables that already exist in the database, and Phase 3 samples real
values from them. Create and populate your domain tables first, e.g.:

```sql
CREATE TABLE events (
    event_id       uuid PRIMARY KEY,
    event_name     text,
    start_datetime timestamptz,
    home_team      text,
    away_team      text,
    venue_name     text
);
CREATE TABLE tickets (
    ticket_id     uuid PRIMARY KEY,
    event_id      uuid,
    section       text,
    seat          text,
    price         numeric,
    ticket_status text
);
-- ...plus some INSERTs so Phase 3 has text to sample
```

Empty tables are fine for Phases 0–2; Phase 3 will detect empty text columns and skip
spell-correction generation (re-run later with `--refresh-seeds`).

### 0.4 Domain scaffold (seeders manifest)

Phase 3 loads generated seeds through `SeederRegistry(domain)`, which reads
`seeders/<domain>/manifest.yaml`. For a brand-new domain, scaffold it first:

```bash
python scripts/scaffold_domain.py --domain sports_ticketing
```

`sports_ticketing` is already partially scaffolded in this repo
(`seeders/sports_ticketing/manifest.yaml`, `data/seeds/sports_ticketing/`), so you can
skip this for that domain.

---

## 1. Run the full initialization

```bash
python scripts/initialize_domain.py --domain sports_ticketing
```

What to expect at each phase (all pauses default to the **uppercase** letter):

| Phase | Prompt / action | Notes |
|-------|-----------------|-------|
| **0 — Table Registration** | Lists DB tables; system tables flagged `← system table`. Enter comma-separated numbers (e.g. `1,2,3`). | If the domain already has a `tables` entry, this is skipped: *"Using registered tables: …"*. |
| pause | `Tables registered. Continue to schema discovery? [Y/n]` | |
| **1 — Schema Discovery** | Prints columns / types / NOT NULL / PK per table. | Read-only. |
| pause | `Schema discovered. Continue to annotation? [Y/n]` | |
| **2 — Annotation** | Per-column `... done (confidence=0.NN)` or `⚠ low confidence`. Writes `column_metadata`. | Existing annotations are skipped unless `--force`. |
| pause | `Annotation complete. Continue to seed research? [Y/n]` | |
| **3 — Seed Research** | Samples text columns, asks schema-tailored Q&A, calls the LLM, previews counts, then `Write seed files? [y/N]`. | On `y`, writes `data/seeds/<domain>/*` and loads them via the seeder registry. |

Completion banner: `Domain 'sports_ticketing' initialization complete.`

### Verify

```bash
psql "$POSTGRES_DSN" -c "SELECT table_name, count(*) FROM column_metadata WHERE domain='sports_ticketing' GROUP BY 1;"
psql "$POSTGRES_DSN" -c "SELECT count(*) FROM spell_corrections WHERE domain='sports_ticketing';"
psql "$POSTGRES_DSN" -c "SELECT gap_type, count(*) FROM query_pattern_memory WHERE domain='sports_ticketing' GROUP BY 1;"
cat data/domain_registry.json   # sports_ticketing should now have a "tables" entry
ls data/seeds/sports_ticketing/
```

---

## 2. Iterative testing — teardown → re-run

`teardown` resets the framework's init state so you can run a clean pass again. It removes
the domain's rows from `column_metadata`, `spell_corrections`, `query_pattern_memory`,
`source_registry`, drops the `tables` entry from `domain_registry.json`, and (when you
confirm) deletes generated seed files. **It does not drop your `events` / `tickets` data
tables.**

```bash
python scripts/initialize_domain.py --domain sports_ticketing teardown
```

It prints what will be removed and prompts `Proceed with teardown? [y/N]`, then prompts
separately before deleting seed files. Typical loop:

```bash
python scripts/initialize_domain.py --domain sports_ticketing teardown   # reset
# ...tweak prompts / annotation logic / Q&A / seed data...
python scripts/initialize_domain.py --domain sports_ticketing            # re-run clean
```

### Confirm teardown was clean

```bash
psql "$POSTGRES_DSN" -c "SELECT count(*) FROM column_metadata WHERE domain='sports_ticketing';"  # expect 0
```

---

## 3. Maintenance subcommands

```bash
# Register tables added to the DB after the first init (smart diff — shows only new ones)
python scripts/initialize_domain.py --domain sports_ticketing add_table

# Re-run Phase 3 only — e.g. after ingesting data into previously-empty tables,
# or to refresh stale spell corrections. Skips Phases 0–2.
python scripts/initialize_domain.py --domain sports_ticketing --refresh-seeds

# Re-annotate columns that already have annotations
python scripts/initialize_domain.py --domain sports_ticketing --force
```

---

## 4. Troubleshooting

| Symptom | Cause / fix |
|---------|-------------|
| `POSTGRES_DSN must be set when DB_BACKEND=postgres` | Set `POSTGRES_DSN` in `.env`. |
| Phase 0 lists no domain tables | Your data tables don't exist yet — create them (step 0.3). |
| Phase 3: *"No data found in text columns"* | Text columns are empty. Load data, then `--refresh-seeds`. |
| Phase 2 errors / no annotations | LLM key missing or invalid (`ANTHROPIC_API_KEY` / `OPENROUTER_API_KEY`). |
| Phase 3 seeder run error | `seeders/<domain>/manifest.yaml` missing — run `scaffold_domain.py`. |
| `annotate_domain.py`: *"has no registered tables"* | Run Phase 0 (or full `initialize_domain.py`) first — it reads tables from the registry. |

---

## SQLite — not yet supported

Running this orchestrator against SQLite **will not work today**. Three things block it:

1. **Entry point is hardwired to Postgres.** `main()` calls
   `get_connection(get_pg_dsn())`; with `DB_BACKEND=sqlite` it would still require
   `POSTGRES_DSN` and then hand a Postgres DSN to `sqlite3.connect()`. The entry point
   would need to be backend-aware (use the SQLite `DB_PATH` when `DB_BACKEND=sqlite`).
2. **Data sampling uses `psycopg.sql.Identifier`.** `_sample_text_columns` (Phase 3) and
   `MetadataAnnotationService._get_sample_values` (Phase 2) build queries with psycopg's
   identifier API, which a `sqlite3` connection can't execute — samples would silently
   come back empty.
3. **`%s` parameter style.** Teardown's `DELETE … WHERE … = %s` is the Postgres paramstyle;
   SQLite expects `?`.

`db/connection.py` already supports a SQLite connection (`get_connection` + `DB_PATH`), and
`initialize_domain._get_table_schema` / `DomainInitializer.get_all_db_tables` already have
SQLite fallbacks — so the gap is the three items above, not a full rewrite. Worth a small
follow-up before testing on SQLite.
