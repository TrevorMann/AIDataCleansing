"""
Unified domain management CLI.

Commands
--------
scaffold        Create skeleton files for a new domain
seed            Run seeders for a domain (wraps SeederRegistry)
init            Apply schema migrations then seed (full bootstrap)
migrate-schema  Print DDL + guide to translate schema to a target backend
list            Show registered seeders for a domain
add-seeder      Register a new seeder entry into domain manifest

Usage
-----
# Scaffold a new domain
python scripts/domain.py scaffold --domain hospitality

# Seed an existing domain (all enabled seeders)
python scripts/domain.py seed --domain real_estate

# Seed specific seeders only
python scripts/domain.py seed --domain real_estate --only wikipedia_fsa statscan_shapefile

# Dry-run (print plan, no DB writes)
python scripts/domain.py seed --domain real_estate --dry-run

# Full init: migrations + seed
python scripts/domain.py init --domain real_estate

# List registered seeders
python scripts/domain.py list --domain real_estate

# Register a Wikipedia FSA seeder
python scripts/domain.py add-seeder --domain real_estate --type wikipedia_fsa \\
    --name wikipedia_fsa_BC --country CA --letters V

# Register a Stats Can shapefile seeder
python scripts/domain.py add-seeder --domain real_estate --type statscan_shp \\
    --name statscan_fsa_BC \\
    --fsa-shapefile "F:/data/lfsa000a21a_e.shp" \\
    --csd-shapefile "F:/data/lcsd000a25a_e.shp" \\
    --country CA --province-pruid 59

# Register a CSV FSA seeder
python scripts/domain.py add-seeder --domain real_estate --type csv_fsa \\
    --name csv_fsa_ON --country CA \\
    --csv-path "data/seeds/real_estate/fsa_prefixes/CA_ON.csv" \\
    --fsa-col FSA --municipality-col CSD_NAME --province-default ON
"""

import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

# DB imports are lazy (inside cmd_seed / cmd_init) so scaffold/list/add-seeder
# work without a configured database connection.
from seeders.manage import (
    add_wikipedia_fsa_seeder,
    add_statscan_shp_seeder,
    add_csv_fsa_seeder,
    list_seeders,
)
from scripts.scaffold_domain import scaffold


# ---------------------------------------------------------------------------
# Subcommand handlers
# ---------------------------------------------------------------------------

def cmd_scaffold(args):
    print(f"Scaffolding domain: {args.domain}")
    scaffold(args.domain)
    print(f"\nNext steps:")
    print(f"  Edit skills/{args.domain}/skills.yaml")
    print(f"  Edit seeders/{args.domain}/manifest.yaml")
    print(f"  python scripts/domain.py add-seeder --domain {args.domain} --type ...")
    print(f"  python scripts/domain.py seed --domain {args.domain} --dry-run")


def cmd_seed(args):
    from seeders.registry import SeederRegistry

    try:
        registry = SeederRegistry(args.domain)
    except FileNotFoundError as e:
        print(f"ERROR: {e}")
        sys.exit(1)

    if args.dry_run:
        conn = None
    else:
        from db.connection import get_connection, get_backend, get_pg_dsn
        from config import get_config_value
        if get_backend() == "postgres":
            conn = get_connection(get_pg_dsn())
        else:
            db_path = get_config_value("DB_PATH", "data/cleaning.db")
            conn = get_connection(db_path)

    only = args.only or None
    results = registry.run_all(conn, only=only, dry_run=args.dry_run)

    succeeded = sum(1 for v in results.values() if v is not None and v >= 0)
    failed = sum(1 for v in results.values() if v is None)
    print(f"\nSummary: {succeeded} succeeded, {failed} failed")
    if failed:
        sys.exit(1)


def cmd_init(args):
    """Apply schema migrations then run all seeders."""
    from db.connection import get_connection, get_pg_dsn

    print(f"Initializing domain: {args.domain}")

    # Run migrations declared in manifest
    try:
        import yaml
        manifest_path = Path(f"seeders/{args.domain}/manifest.yaml")
        manifest = yaml.safe_load(manifest_path.read_text()) or {}
    except FileNotFoundError:
        print(f"ERROR: No manifest for domain '{args.domain}'")
        sys.exit(1)

    from db.connection import get_backend, get_pg_dsn
    from config import get_config_value

    migrations = manifest.get("schema_migrations", [])
    if get_backend() == "postgres":
        if migrations:
            from db.connection import get_connection
            conn = get_connection(get_pg_dsn())
            print(f"Running {len(migrations)} migration(s)...")
            for mig_path in migrations:
                sql = Path(mig_path).read_text()
                with conn.cursor() as cur:
                    cur.execute(sql)
                conn.commit()
                print(f"  Applied: {mig_path}")
        else:
            print("No schema migrations declared in manifest.")
    else:
        db_path = get_config_value("DB_PATH", "data/cleaning.db")
        from db.sqlite_init import init_db, create_seeder_tables
        from db.connection import get_connection
        print(f"Initializing SQLite schema: {db_path}")
        init_db(db_path)
        print("  SQLite schema ready (core + municipality + seeder tables)")

    # Seed
    args.dry_run = False
    args.only = None
    cmd_seed(args)


def cmd_migrate_schema(args):
    """Print domain DDL and guide user to complete translation in Claude Code."""
    import yaml

    domain = args.domain
    target = args.target

    # Collect migration files for this domain
    migrations_dir = Path("db/migrations")
    pg_init = Path("db/pg_init.py")
    manifest_path = Path(f"seeders/{domain}/manifest.yaml")

    if not manifest_path.exists():
        print(f"ERROR: No manifest for domain '{domain}'")
        sys.exit(1)

    print(f"Domain : {domain}")
    print(f"Target : {target}")
    print()

    # Print migration SQL files
    sql_files = sorted(migrations_dir.glob("*.sql")) if migrations_dir.exists() else []
    if sql_files:
        print(f"Migration files ({len(sql_files)}):")
        for f in sql_files:
            print(f"  {f}")
    else:
        print("No migration files found in db/migrations/")

    if pg_init.exists():
        print(f"Postgres init : {pg_init}")

    print()
    print("To complete the schema translation, open Claude Code and say:")
    print(f'  "migrate schema for {domain} to {target}"')
    print()
    print("The backend-schema-manager skill will:")
    print(f"  1. Read the migrations above")
    print(f"  2. Translate Postgres DDL to {target}")
    print(f"  3. Generate db/{target}_init.py")
    print(f"  4. Update db/upsert.py + db/connection.py")
    print(f"  5. Print .env instructions (DB_BACKEND={target})")


def cmd_list(args):
    try:
        seeders = list_seeders(args.domain)
    except FileNotFoundError as e:
        print(f"ERROR: {e}")
        sys.exit(1)

    if not seeders:
        print(f"No seeders registered for domain '{args.domain}'")
        return

    print(f"Seeders for domain '{args.domain}':")
    for s in seeders:
        status = "enabled" if s.get("enabled", True) else "disabled"
        cadence = s.get("refresh_cadence", "?")
        print(f"  {s['name']:30s}  [{status}]  cadence={cadence}")
        print(f"    class: {s['class']}")


def cmd_add_seeder(args):
    seeder_type = args.type

    if seeder_type == "wikipedia_fsa":
        letters = args.letters.split(",") if args.letters else None
        entry = add_wikipedia_fsa_seeder(
            args.domain, args.name,
            country=args.country,
            letters=letters,
            rate_limit_seconds=float(args.rate_limit or 1.0),
        )

    elif seeder_type == "statscan_shp":
        if not args.fsa_shapefile or not args.csd_shapefile:
            print("ERROR: --fsa-shapefile and --csd-shapefile required for statscan_shp")
            sys.exit(1)
        entry = add_statscan_shp_seeder(
            args.domain, args.name,
            fsa_shapefile=args.fsa_shapefile,
            csd_shapefile=args.csd_shapefile,
            country=args.country or "CA",
            province_pruid=args.province_pruid,
        )

    elif seeder_type == "csv_fsa":
        if not args.csv_path:
            print("ERROR: --csv-path required for csv_fsa")
            sys.exit(1)
        entry = add_csv_fsa_seeder(
            args.domain, args.name,
            country=args.country,
            csv_path=args.csv_path,
            fsa_col=args.fsa_col or "FSA",
            municipality_col=args.municipality_col or "MUNICIPALITY",
            province_default=args.province_default,
        )

    else:
        print(f"ERROR: Unknown seeder type '{seeder_type}'. "
              f"Choices: wikipedia_fsa, statscan_shp, csv_fsa")
        sys.exit(1)

    print(f"Registered seeder '{entry['name']}' in seeders/{args.domain}/manifest.yaml")
    print(f"  class:   {entry['class']}")
    print(f"  config:  {entry['config']}")
    print(f"\nRun: python scripts/domain.py seed --domain {args.domain} --only {entry['name']}")


# ---------------------------------------------------------------------------
# Argument parser
# ---------------------------------------------------------------------------

def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Domain management: scaffold, seed, init, inspect."
    )
    sub = parser.add_subparsers(dest="command", required=True)

    # scaffold
    p = sub.add_parser("scaffold", help="Create skeleton for a new domain")
    p.add_argument("--domain", required=True)

    # seed
    p = sub.add_parser("seed", help="Run seeders for a domain")
    p.add_argument("--domain", required=True)
    p.add_argument("--only", nargs="*", metavar="SEEDER", help="Run specific seeders")
    p.add_argument("--dry-run", action="store_true")

    # init
    p = sub.add_parser("init", help="Apply migrations then seed")
    p.add_argument("--domain", required=True)

    # migrate-schema
    p = sub.add_parser("migrate-schema", help="Print DDL + guide to translate schema to target backend")
    p.add_argument("--domain", required=True)
    p.add_argument("--target", required=True,
                   help="Target backend: snowflake, duckdb, sqlserver, redshift, bigquery, sqlite")

    # list
    p = sub.add_parser("list", help="List registered seeders for a domain")
    p.add_argument("--domain", required=True)

    # add-seeder
    p = sub.add_parser("add-seeder", help="Register a seeder into domain manifest")
    p.add_argument("--domain", required=True)
    p.add_argument("--type", required=True,
                   choices=["wikipedia_fsa", "statscan_shp", "csv_fsa"],
                   help="Seeder type")
    p.add_argument("--name", required=True, help="Unique seeder name")
    p.add_argument("--country", default="CA")
    p.add_argument("--letters",
                   help="Comma-separated FSA letters for wikipedia_fsa, e.g. M,V,T")
    p.add_argument("--rate-limit", type=float, default=1.0,
                   help="Seconds between Wikipedia requests (wikipedia_fsa)")
    p.add_argument("--fsa-shapefile", help="Path to lfsa...shp (statscan_shp)")
    p.add_argument("--csd-shapefile", help="Path to lcsd...shp (statscan_shp)")
    p.add_argument("--province-pruid",
                   help="Stats Can PRUID to filter, e.g. 35 for ON (statscan_shp)")
    p.add_argument("--csv-path", help="Path to CSV file (csv_fsa)")
    p.add_argument("--fsa-col", default="FSA")
    p.add_argument("--municipality-col", default="MUNICIPALITY")
    p.add_argument("--province-default")

    return parser


def main():
    parser = build_parser()
    args = parser.parse_args()

    dispatch = {
        "scaffold":        cmd_scaffold,
        "seed":            cmd_seed,
        "init":            cmd_init,
        "migrate-schema":  cmd_migrate_schema,
        "list":            cmd_list,
        "add-seeder":      cmd_add_seeder,
    }
    dispatch[args.command](args)


if __name__ == "__main__":
    main()
