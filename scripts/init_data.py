"""CLI: seed public datasets per domain. Idempotent."""

import argparse
import sys
from pathlib import Path

# Allow running as script from project root
sys.path.insert(0, str(Path(__file__).parent.parent))

from db.connection import get_connection, get_pg_dsn
from seeders.registry import SeederRegistry


def main():
    ap = argparse.ArgumentParser(description="Seed domain data into DB. Idempotent.")
    ap.add_argument("--domain", required=True, help="Domain to seed (real_estate, sports_ticketing, ...)")
    ap.add_argument("--only", nargs="*", help="Specific seeder names to run")
    ap.add_argument("--dry-run", action="store_true", help="Print plan without executing")
    args = ap.parse_args()

    try:
        registry = SeederRegistry(args.domain)
    except FileNotFoundError as e:
        print(f"ERROR: {e}")
        sys.exit(1)

    conn = get_connection(get_pg_dsn())
    results = registry.run_all(conn, only=args.only, dry_run=args.dry_run)

    succeeded = sum(1 for v in results.values() if v is not None and v >= 0)
    failed = sum(1 for v in results.values() if v is None)
    print(f"\nSummary: {succeeded} succeeded, {failed} failed")
    if failed:
        sys.exit(1)


if __name__ == "__main__":
    main()
