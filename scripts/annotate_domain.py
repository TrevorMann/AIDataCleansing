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

    from services.domain_initializer import DomainInitializer
    di = DomainInitializer(args.domain)
    tables = di.get_registered_tables()
    if not tables:
        print(
            f"Domain '{args.domain}' has no registered tables.\n"
            f"Run: python scripts/initialize_domain.py --domain {args.domain}"
        )
        sys.exit(1)

    conn = get_db_connection("")

    if args.dry_run:
        svc = MetadataAnnotationService(llm_client=None)
        gaps = svc.list_gaps(args.domain, conn, tables)
        if not gaps:
            print(f"No annotation gaps found for domain '{args.domain}'.")
            return
        print(f"Annotation gaps for '{args.domain}' ({len(gaps)} columns):")
        for g in gaps:
            print(f"  {g['table_name']}.{g['column_name']}")
        return

    svc = MetadataAnnotationService(llm_client=_build_llm_client())
    print(f"Annotating {args.domain}...")
    report = svc.run(args.domain, conn, tables, force=args.force)

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
