---
name: backend-schema-manager
description: >
  Use when managing domain schema (migrations, init files, seeder upserts) from
  Postgres to another backend. Triggers on: "migrate schema to X", "add X backend",
  "support Snowflake/Redshift/SQL Server/DuckDB/BigQuery", or
  scripts/domain.py migrate-schema --target <backend>.
---

# Backend Schema Translator

Translates Postgres DDL + seeder upsert patterns to any target backend.
Source of truth is always Postgres. All other backends are derived.

## Schema Evolution Strategy

** YOU MUST NOT not make any breaking changes to the schema, this means no deletion of columns or tables, and no changing of column types that would cause data loss, unless there is a specific migration strategy in place (e.g. adding a new column, backfilling data, then deleting old column). This is to ensure that all backends can maintain compatibility with the same evolving schema over time. **

1. Create a new migration file in `db/migrations/` with a timestamp and descriptive name, e.g. `2024_06_01_add_price_to_listings.sql`.
2. Update pg_init.py : Add the corresponding create table or alter statement to the postgres init script.
3. You MUST immediately trigger the process section of this skill for all active backends definied in the environment (eg. Snowflake, DuckDB, etc.) to ensure parity.

## Type Translation Strategy

When authoring new schemas, always use Standard Postgres as the intermediate representation. Even if the user says 'Add this table to {backend},' the internal workflow must be:
User Intent → Postgres DDL → Skill Translation → {backend} Init.

**Do not use a hardcoded lookup table.** Use your SQL knowledge directly.
For backends you know well (SQLite, SQL Server, Snowflake, Redshift, DuckDB, BigQuery):
apply translations from memory.

For unfamiliar backends or uncertain mappings, call:
```
web_search("<backend> equivalent of Postgres SERIAL JSONB TIMESTAMPTZ TEXT")
```
Schema generation is one-time work — a web_search call here costs nothing compared
to a wrong type that fails silently at insert time.

**Key gotchas worth double-checking for any backend:**
- Auto-increment primary keys (`SERIAL` / `BIGSERIAL`) — syntax varies widely
- Semi-structured / JSON columns (`JSONB`) — native vs TEXT workaround
- Timezone-aware timestamps (`TIMESTAMPTZ`) — not universally supported
- `NOW()` / `CURRENT_TIMESTAMP` default expressions — syntax varies

## Process

1. Drafting: If the dataset is new, draft the Postgres CREATE TABLE statement first.
2. run translation to {backend} to generate the init script for the new backend, and present to the user for review.
1. Read `db/migrations/*.sql` + `db/pg_init.py` for the domain schema and identity any new ddl changes since the last run.
2. Read `db/upsert.py` — adapter pattern already handles Postgres vs SQLite
3. Translate types (memory + web_search as needed — see above). Translate new postgres types to {backend} equivalents.
4. Generate `db/<backend>_init.py` (CREATE TABLE equivalents)
5. Update db/{{backend}}_init.py to include the new tables.
6. If the backend is already in use, generate an ALTER TABLE snippet for the user to run manually if automatic migration isn't enabled.
7. Extend `db/upsert.py` `_backend()` + `bulk_insert_ignore()` for new backend
8. Extend `db/connection.py` `get_connection()` for new backend
9. Add `DB_BACKEND=<backend>` support to `config.py` docs
10. Check if the new dataset requires custom conflict columns for bulk_insert_ignore() and update db/upsert.py accordingly.

## Idempotent Insert Patterns

These vary most across backends and are the hardest to get right:

| Backend     | Pattern |
|-------------|---------|
| Postgres    | `INSERT INTO t (...) VALUES (...) ON CONFLICT (cols) DO NOTHING` |
| SQLite      | `INSERT OR IGNORE INTO t (...) VALUES (...)` |
| SQL Server  | `IF NOT EXISTS (SELECT 1 FROM t WHERE col=?) INSERT INTO t ...` or `MERGE` |
| Snowflake   | `MERGE INTO t USING (SELECT ...) ON (match) WHEN NOT MATCHED THEN INSERT` |
| Redshift    | `INSERT INTO t (...) SELECT ... WHERE NOT EXISTS (SELECT 1 FROM t WHERE ...)` |
| DuckDB      | `INSERT OR IGNORE INTO t (...) VALUES (...)` |
| BigQuery    | `MERGE t USING (SELECT ...) ON (match) WHEN NOT MATCHED THEN INSERT` |

For backends not listed: `web_search("<backend> idempotent upsert insert if not exists")`.

## CREATE TABLE IF NOT EXISTS

Postgres, SQLite, DuckDB, Snowflake, BigQuery: supported natively.

SQL Server: not supported — use:
```sql
IF OBJECT_ID('dbo.t', 'U') IS NULL CREATE TABLE dbo.t (...)
```

## Unique Constraints Note

Snowflake and BigQuery: UNIQUE constraints are **metadata only, not enforced**.
Use MERGE upsert logic to maintain uniqueness in application layer.

## Output File Locations

```
db/
  <backend>_init.py        # CREATE TABLE equivalents for target backend
  upsert.py                # Add branch in _backend() + bulk_insert_ignore()
  connection.py            # Add get_connection() branch for new backend
```

## Extending db/upsert.py

Add new backend to `_backend()` detection and `bulk_insert_ignore()`:

```python
def _backend(conn) -> str:
    t = type(conn).__module__
    if t.startswith("psycopg"):           return "postgres"
    if "snowflake" in t:                  return "snowflake"
    if "pyodbc" in t or "pymssql" in t:   return "sqlserver"
    if "redshift_connector" in t:         return "redshift"
    if "duckdb" in t:                     return "duckdb"
    if "bigquery" in t:                   return "bigquery"
    return "sqlite"
```

you MUST Add Appropriate imports for new backends, e.g. `import snowflake.connector` for Snowflake. This must be added to the requirements.txt and requirements file ran on the environment.

Snowflake / BigQuery MERGE example for `bulk_insert_ignore`:
```python
merge_keys = " AND ".join(f"target.{c} = source.{c}" for c in conflict_cols)
sql = f"""
MERGE INTO {table} AS target
USING (SELECT {', '.join(f'%s AS {c}' for c in cols)}) AS source
ON {merge_keys}
WHEN NOT MATCHED THEN INSERT ({', '.join(cols)})
  VALUES ({', '.join(f'source.{c}' for c in cols)})
"""
```

## Manual Overrides

Check db/overrides/{{backend}}.sql. If a table name exists there, use that raw SQL instead of the automated translation for that specific table.

## Seeder Compatibility

All seeder `upsert()` methods call `db.upsert.bulk_insert_ignore()` — zero changes
needed in seeder code when adding a new backend. Only `db/upsert.py` changes.


## Domain Expansion Logic
When adding a new industry or subject area:
1. **Identify the Domain:** Define a new `domain` folder structure if it doesn't exist.
2. **Schema Baseline:** Generate the Postgres DDL as the Source of Truth. Ensure it includes the "Enhancement Metadata" columns (confidence, source, timestamp) to support your cleaning pipeline.
3. **Cross-Backend Synchronization:**
   - Use the `{{backend}}` variable to generate the specific `init.py` for every target DB in the stack.
   - **Crucial:** Automatically update the `requirements.txt` or `pyproject.toml` with the driver for the new backend (e.g., `snowflake-connector-python`, `duckdb`).

## Multi-Step Confidence Handling
Since the data goes through "Seeded -> Lookup -> Web Search" escalations:
- The `db/upsert.py` logic must support "Partial Updates." 
- **Rule:** If a record exists, only overwrite `NULL` fields or fields where the new `_source_confidence` is higher than the existing one.


## Command

# To add a new domain/dataset and sync it:
python scripts/domain.py add-dataset --name <domain_name> --sync-all

# To sync an existing Postgres schema to a specific target:
python scripts/domain.py migrate-schema --domain <domain_name> --target <backend>

```bash
python scripts/domain.py migrate-schema --domain real_estate --target snowflake
```

Should:
1. Read all migration SQL + pg_init for domain
2. Translate types (LLM knowledge + web_search for unfamiliar backends)
3. Where {{backend}} is the user-specified target database.
4. Write `db/{backend}_init.py` (appropriate backend CREATE TABLE statements) (example `db/snowflake_init.py`)
5. Print connection string format for new backend
6. Print `DB_BACKEND={backend}` (example: `DB_BACKEND=snowflake`) .env instructions 
7. Optionally if the user has credentials in a credential store by pass the .env file and create access to credential store for backend credentials. 
8. Optionally if user has credentials configured, run the new init script against their database instance to create tables automatically.
9. Add instructions for any manual steps needed (e.g. installing new Python package, setting up credentials, etc.)
```
