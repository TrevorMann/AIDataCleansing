"""Smoke test for the gap-type vocabulary against the live (configured) database.

Runs the deterministic gap flow end-to-end on sample records:
  load gap_detection config from the DB  ->  classify_gaps  ->  flags_from_gaps

Uses whatever backend your .env selects (DB_BACKEND / POSTGRES_DSN). It does NOT
call any LLM or web search — the gap vocabulary is deterministic. To exercise the
model/web path, run the full pipeline (run_cleaning_workflow_v2) separately.

Usage (PowerShell, from repo root):
    .venv-win\\Scripts\\python.exe scripts\\tests\\smoke_gap_detection.py
    .venv-win\\Scripts\\python.exe scripts\\tests\\smoke_gap_detection.py --domain real_estate
"""

import argparse
import json

from db.connection import get_backend
from db.schema_discovery import get_gap_detection
from cleaning.gap_classifier import classify_gaps
from cleaning.flags import flags_from_gaps

# Sample records to classify. Edit these, or pass --records path/to/file.json.
SAMPLE_RECORDS = [
    {"address": "123 King St, Toronto", "country": "CA", "postal_code": None, "municipality": None},
    {"address": "55 Main St, Buffalo", "country": "US", "postal_code": "14201", "municipality": "Buffalo"},
    {"address": "??", "country": None, "postal_code": None, "municipality": None},
]


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--domain", default="real_estate", help="Domain to load gap_detection config for")
    ap.add_argument("--records", help="Optional path to a JSON file: a list of record dicts")
    args = ap.parse_args()

    print(f"backend = {get_backend()}  |  domain = {args.domain}\n")

    config = get_gap_detection("", args.domain)
    if not config:
        print("No gap_detection config found. Did you run:")
        print(f"  python scripts/init_data.py --domain {args.domain} --only column_metadata")
        return
    print("Loaded gap_detection config from DB:")
    for col, cfg in config.items():
        print(f"  {col}: {cfg}")
    print()

    records = SAMPLE_RECORDS
    if args.records:
        with open(args.records, encoding="utf-8") as f:
            records = json.load(f)

    for i, record in enumerate(records, 1):
        gaps = classify_gaps(record, config)
        flags = [f.value for f in flags_from_gaps(gaps)]
        print(f"record {i}: {record}")
        print(f"  gaps  : {gaps}")
        print(f"  flags : {flags}\n")


if __name__ == "__main__":
    main()
