#!/usr/bin/env python3
"""
Unified domain initialization orchestrator.

Usage:
  python scripts/initialize_domain.py --domain sports_ticketing
  python scripts/initialize_domain.py --domain sports_ticketing add_table
  python scripts/initialize_domain.py --domain sports_ticketing --refresh-seeds
  python scripts/initialize_domain.py --domain sports_ticketing teardown
"""

import argparse
import os
import sys
from pathlib import Path
from typing import Optional

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from db.pii_columns import REDACTED, is_pii_column
from services.domain_initializer import DomainInitializer, SYSTEM_TABLES
from seeders.domain_researcher import DomainResearcher
from seeders.registry import SeederRegistry
from pathlib import Path as _Path

try:
    from services.metadata_annotation import MetadataAnnotationService
except ImportError:
    # Allow module to load even if psycopg is not available (e.g., in tests)
    MetadataAnnotationService = None


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

def phase0_register_tables(initializer: DomainInitializer, conn) -> tuple[list[str], str]:
    _phase_header(0, "Table Registration")

    existing = initializer.get_registered_tables()
    if existing:
        schema = initializer.get_schema()
        print(f"Using registered tables: {', '.join(existing)}")
        print(f"Schema: {schema}")
        return existing, schema

    # Step 1: Choose schema
    available_schemas = initializer.get_available_schemas(conn)
    if len(available_schemas) == 1:
        selected_schema = available_schemas[0]
        print(f"Database schema: {selected_schema}\n")
    else:
        print(f"Available schemas:\n")
        for i, schema in enumerate(available_schemas, 1):
            print(f"  [{i}] {schema}")
        raw = input("\nSelect schema (number): ").strip()
        try:
            idx = int(raw) - 1
            if not (0 <= idx < len(available_schemas)):
                print("Invalid selection. Using 'public'.")
                selected_schema = "public"
            else:
                selected_schema = available_schemas[idx]
        except ValueError:
            print("Invalid input. Using 'public'.")
            selected_schema = "public"
        print()

    # Step 2: Choose tables from that schema
    all_tables = initializer.get_all_db_tables(conn, selected_schema)
    non_system = [t for t in all_tables if t not in SYSTEM_TABLES]

    # For large DBs: score and present top candidates
    if len(non_system) > 15:
        print(f"Found {len(all_tables)} tables in schema '{selected_schema}'. Showing top candidates for '{initializer.domain}'...\n")
        scored = initializer.score_tables(non_system)
        candidates = [t for t, _ in scored[:10]]
    else:
        candidates = non_system

    print(f"Found {len(all_tables)} tables in '{selected_schema}' schema.\n")
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

    initializer.register_tables(selected, selected_schema)
    print(f"\nRegistered: {', '.join(selected)} → domain_registry.json")
    print(f"Schema: {selected_schema}")
    return selected, selected_schema


# ── Phase 1: Schema discovery ─────────────────────────────────────────────────

def _get_table_schema(table: str, conn, schema: str = "public") -> list[dict]:
    """Query column metadata for a single table. Tries Postgres, falls back to SQLite."""
    try:
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
                WHERE c.table_schema = %s AND c.table_name = %s
                ORDER BY c.ordinal_position
                """,
                (schema, table),
            )
            return [dict(row) for row in cur.fetchall()]
    except Exception:
        # SQLite fallback
        with conn.cursor() as cur:
            cur.execute(f"PRAGMA table_info({table})")
            return [
                {"name": r[1], "type": r[2], "notnull": bool(r[3]), "pk": bool(r[5])}
                for r in cur.fetchall()
            ]


def phase1_schema_discovery(tables: list[str], conn, schema: str = "public") -> dict[str, list[dict]]:
    _phase_header(1, "Schema Discovery")
    print(f"Scanning {len(tables)} tables in schema '{schema}'...\n")

    db_schema: dict[str, list[dict]] = {}
    for table in tables:
        columns = _get_table_schema(table, conn, schema)
        db_schema[table] = columns
        print(f"  {table} ({len(columns)} columns)")
        for col in columns:
            pk_flag = "  PK" if col.get("pk") else ""
            nn_flag = "  NOT NULL" if col.get("notnull") else ""
            print(f"    {col['name']:<28} {col['type']:<20}{nn_flag}{pk_flag}")

    return db_schema


# ── Phase 3 helpers ───────────────────────────────────────────────────────────

_TEXT_COLUMN_TYPES = frozenset({
    "text", "character varying", "varchar", "char", "character"
})

MIN_ANNOTATION_CONFIDENCE = 0.70


def _sample_text_columns(
    schema: dict[str, list[dict]],
    conn,
    n: int = 50,
) -> dict[str, list]:
    """Sample up to n non-null values from each text column. Skips on DB error."""
    from psycopg import sql  # local import: psycopg may be absent in test envs

    samples: dict[str, list] = {}
    for table, cols in schema.items():
        for col in cols:
            if col["type"].lower() not in _TEXT_COLUMN_TYPES:
                continue
            key = f"{table}.{col['name']}"
            if is_pii_column(col["name"]):
                samples[key] = [REDACTED]
                continue
            # Safe identifier quoting — table/column names come from the schema,
            # never interpolate them into the SQL string directly.
            query = sql.SQL(
                "SELECT {c} AS val FROM {t} "
                "WHERE {c} IS NOT NULL AND {c} != '' ORDER BY random() LIMIT %s"
            ).format(c=sql.Identifier(col["name"]), t=sql.Identifier(table))
            try:
                with conn.cursor() as cur:
                    cur.execute(query, (n,))
                    samples[key] = [row["val"] for row in cur.fetchall()]
            except Exception:
                pass  # Don't fail initialization if a sample query fails
    return samples


def _load_annotations(domain: str, conn) -> dict[str, str]:
    """Load column annotations from data_details.column_metadata. Returns {table.col: description}."""
    try:
        with conn.cursor() as cur:
            cur.execute(
                "SELECT table_name, column_name, description "
                "FROM data_details.column_metadata WHERE domain = %s AND description IS NOT NULL "
                "AND (confidence IS NULL OR confidence >= %s)",
                (domain, MIN_ANNOTATION_CONFIDENCE),
            )
            return {
                f"{row['table_name']}.{row['column_name']}": row["description"]
                for row in cur.fetchall()
            }
    except Exception:
        return {}


# ── Stubs for future tasks ────────────────────────────────────────────────────

def phase2_annotation(domain: str, tables: list[str], conn, schema: str = "public", force: bool = False) -> None:
    _phase_header(2, "Annotation")
    print(f"Annotating columns across {len(tables)} table(s) in schema '{schema}'...\n")

    llm = _build_llm_client()
    svc = MetadataAnnotationService(llm_client=llm)

    # Wrap _annotate_table to print per-table progress
    original_annotate = svc._annotate_table

    def _annotating_with_progress(d, dd, table, columns, conn_inner, db_schema="public"):
        print(f"  {table} ({len(columns)} columns) ... ", end="", flush=True)
        result = original_annotate(d, dd, table, columns, conn_inner, db_schema)
        if result is None:
            print("⚠ LLM call failed — skipped (re-run to retry)")
        else:
            low = sum(
                1 for c in columns
                if result["columns"].get(c, {}).get("confidence", 0.3) < 0.70
            )
            print("done" + (f" ({low} low-confidence)" if low else ""))
        return result

    svc._annotate_table = _annotating_with_progress

    report = svc.run(domain, conn, tables=tables, schema=schema, force=force)

    print(f"\nDone: {report.annotated} annotated, {report.skipped} skipped", end="")
    if report.low_confidence:
        print(f", {len(report.low_confidence)} low-confidence")
        print("\nLow-confidence columns (review recommended):")
        for lc in report.low_confidence:
            print(f"  {lc['table_name']}.{lc['column_name']}  confidence={lc['confidence']:.2f}")
    else:
        print()


def phase3_seed_research(domain: str, schema: dict, conn) -> None:
    _phase_header(3, "Seed Research")

    print("Loading annotations...")
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

    # Live web grounding — prefer current facts over model memory when a
    # Tavily key is available. Best-effort; skipped silently without a key.
    web_context = ""
    if os.getenv("TAVILY_API_KEY"):
        print("\nGathering web research to ground the seed content...")
        try:
            from cleaning.cache import WebSearchCache
            from seeders.domain_researcher import gather_web_context
            web_context = gather_web_context(domain, answers, WebSearchCache())
            print(f"  {len(web_context)} chars of search snippets gathered")
        except Exception as e:
            print(f"  ⚠ web grounding skipped: {e}")

    print("\nConnecting to LLM to generate seed content...")
    try:
        llm = _build_llm_client(tier="standard")
    except ValueError as e:
        print(f"\nERROR: {e}")
        sys.exit(1)

    bundle = researcher.research_with_schema(
        answers=answers,
        schema=schema,
        annotations=annotations,
        data_samples=samples,
        llm=llm,
        web_context=web_context,
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


def cmd_add_table(domain: str, initializer: DomainInitializer, conn) -> None:
    _section_header(f"add_table — {domain}")

    registered = initializer.get_registered_tables() or []
    print(f"Currently registered: {', '.join(registered) or 'none'}")
    print("Scanning database for new tables...\n")

    new_tables = initializer.diff_tables(conn)

    if not new_tables:
        print("No new tables found. Domain is up to date.")
        return

    # Same selection UX as Phase 0 — score if many candidates
    candidates = new_tables
    if len(candidates) > 15:
        scored = initializer.score_tables(candidates)
        candidates = [t for t, _ in scored[:10]]

    print("New tables found (not yet registered):\n")
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
    db_schema = initializer.get_schema()
    initializer.register_tables(updated, db_schema)
    print(f"\nAdded: {', '.join(selected)} → domain_registry.json")

    # Annotate new tables
    print()
    phase2_annotation(domain, selected, conn, db_schema)

    # Prompt for seed refresh
    if _pause("Seeds were generated from previous schema. Refresh seeds with new table context?",
              default_yes=False):
        all_tables = initializer.get_registered_tables() or []
        schema_dict = phase1_schema_discovery(all_tables, conn, db_schema)
        phase3_seed_research(domain, schema_dict, conn)


def cmd_teardown(domain: str, initializer: DomainInitializer, conn) -> None:
    """Reset a domain's initialization state so init can be re-run cleanly.

    Removes framework-generated state only — the user's actual data tables
    (events, tickets, ...) are never touched.
    """
    _section_header(f"Teardown — {domain}")

    tables = initializer.get_registered_tables()
    print("This removes the framework's initialization state for this domain:")
    print("  • column_metadata, spell_corrections, query_pattern_memory, source_registry rows")
    print(f"  • the 'tables' registration in domain_registry.json "
          f"(currently: {', '.join(tables) if tables else 'none'})")
    print("  • generated seed files under data/seeds/<domain>/ (optional, prompted)")
    print("\nYour actual data tables are NOT dropped or modified.")

    if not _pause("Proceed with teardown?", default_yes=False):
        print("Aborted.")
        return

    # Delete domain-scoped rows. Table/column names here are hardcoded literals,
    # only the domain value is parameterized. Commit per table; roll back on
    # failure so one missing table doesn't abort the rest of the transaction.
    deletions = [
        ("column_metadata", "domain"),
        ("spell_corrections", "domain"),
        ("query_pattern_memory", "domain"),
        ("source_registry", "domain_key"),
    ]
    print()
    for table, col in deletions:
        try:
            with conn.cursor() as cur:
                cur.execute(f"DELETE FROM {table} WHERE {col} = %s", (domain,))
                deleted = cur.rowcount
            conn.commit()
            print(f"  {table}: {deleted} row(s) deleted")
        except Exception as e:  # noqa: BLE001 — surface, don't abort
            conn.rollback()
            print(f"  ⚠ {table}: skipped ({e})")

    if initializer.unregister_tables():
        print("  domain_registry.json: 'tables' entry removed")
    else:
        print("  domain_registry.json: no 'tables' entry to remove")

    seed_dir = _Path("data/seeds") / domain
    if seed_dir.exists():
        if _pause(f"Also delete generated seed files in {seed_dir}?", default_yes=False):
            import shutil
            shutil.rmtree(seed_dir)
            print(f"  {seed_dir}: removed")

    _section_header(f"Teardown complete for '{domain}'.")


def cmd_refresh_seeds(domain: str, initializer: DomainInitializer, conn) -> None:
    _section_header(f"Refresh Seeds — {domain}")

    tables = initializer.get_registered_tables()
    if not tables:
        print(
            f"Domain '{domain}' not registered. "
            f"Run: python scripts/initialize_domain.py --domain {domain}"
        )
        sys.exit(1)

    db_schema = initializer.get_schema()
    print(f"Using registered tables: {', '.join(tables)}")
    print(f"Schema: {db_schema}")
    print("Skipping phases 0-2 (annotation unchanged). Running Phase 3 only.\n")

    schema_dict = phase1_schema_discovery(tables, conn, db_schema)
    phase3_seed_research(domain, schema_dict, conn)


# ── main ──────────────────────────────────────────────────────────────────────

def _build_llm_client(tier: str = "fast"):
    from cleaning.llm_client import build_client_for_tier
    return build_client_for_tier(tier)


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Initialize a domain: register tables, annotate schema, generate seeds."
    )
    parser.add_argument("--domain", required=True, help="Domain name (e.g. sports_ticketing)")
    parser.add_argument("--force", action="store_true",
                        help="Re-annotate columns that already have annotations")
    parser.add_argument("--refresh-seeds", action="store_true",
                        help="Re-run Phase 3 seed research only (skip phases 0-2)")
    parser.add_argument("subcommand", nargs="?", choices=["add_table", "teardown"],
                        help="add_table: register new DB tables added since last init; "
                             "teardown: reset init state so the domain can be re-initialized")
    args = parser.parse_args()

    from db.connection import get_connection, get_pg_dsn
    conn = get_connection(get_pg_dsn())
    initializer = DomainInitializer(args.domain)

    if args.subcommand == "add_table":
        cmd_add_table(args.domain, initializer, conn)
        return

    if args.subcommand == "teardown":
        cmd_teardown(args.domain, initializer, conn)
        return

    if args.refresh_seeds:
        cmd_refresh_seeds(args.domain, initializer, conn)
        return

    # Full initialization flow
    tables, db_schema = phase0_register_tables(initializer, conn)

    if not _pause("Tables registered. Continue to schema discovery?"):
        print("Stopped after Phase 0.")
        return

    schema = phase1_schema_discovery(tables, conn, db_schema)

    if not _pause("Schema discovered. Continue to annotation?"):
        print("Stopped after Phase 1.")
        return

    phase2_annotation(args.domain, tables, conn, db_schema, force=args.force)

    if not _pause("Annotation complete. Continue to seed research?"):
        print("Stopped after Phase 2.")
        return

    phase3_seed_research(args.domain, schema, conn)

    _section_header(f"Domain '{args.domain}' initialization complete.")


if __name__ == "__main__":
    main()
