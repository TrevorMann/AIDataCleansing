"""DB-backed spell corrections loader for domain-specific cleaning."""

import csv
from pathlib import Path
from typing import Dict, Optional


def load_seed_corrections(conn, seed_file: str, domain: str) -> int:
    """Load corrections from CSV into DB. Idempotent (ON CONFLICT DO NOTHING).

    Args:
        conn: psycopg connection
        seed_file: Path to CSV with columns: wrong, right, source, confidence
        domain: Domain tag (e.g. 'real_estate')

    Returns:
        Number of rows inserted
    """
    seed_path = Path(seed_file)
    if not seed_path.exists():
        raise FileNotFoundError(f"Seed file not found: {seed_path}")

    rows = []
    with open(seed_path, newline="", encoding="utf-8") as f:
        for row in csv.DictReader(f):
            rows.append((
                row["wrong"].strip().lower(),
                domain,
                row["right"].strip().lower(),
                row.get("source", "manual_seed"),
                float(row.get("confidence", 1.0)),
            ))

    if not rows:
        return 0

    with conn.cursor() as cur:
        cur.executemany(
            """
            INSERT INTO spell_corrections (wrong, domain, right, source, confidence)
            VALUES (%s, %s, %s, %s, %s)
            ON CONFLICT (wrong, domain) DO NOTHING
            """,
            rows,
        )
    conn.commit()
    return len(rows)


def get_corrections_dict(conn, domain: str) -> Dict[str, str]:
    """Return {wrong: right} dict for domain, loaded from DB.

    Args:
        conn: psycopg connection
        domain: Domain tag

    Returns:
        Dict mapping misspelling → correct form
    """
    with conn.cursor() as cur:
        cur.execute(
            "SELECT wrong, right FROM spell_corrections WHERE domain = %s",
            (domain,),
        )
        return {row[0]: row[1] for row in cur.fetchall()}
