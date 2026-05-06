"""
Backend-agnostic DB helpers for seeders.

Abstracts Postgres vs SQLite differences so seeder upsert() methods
contain zero backend-specific SQL.

Supported backends
------------------
postgres  psycopg3 connection  — %s params, ON CONFLICT ... DO NOTHING
sqlite    sqlite3 connection   — ? params,  INSERT OR IGNORE
"""

from __future__ import annotations

from config import get_config_value


def _backend(conn) -> str:
    """Detect backend from connection type (doesn't require env var at call time)."""
    t = type(conn).__module__
    if t.startswith("psycopg"):
        return "postgres"
    return "sqlite"


def table_exists(conn, table: str) -> bool:
    """Return True if table exists in the current schema."""
    backend = _backend(conn)
    if backend == "postgres":
        with conn.cursor() as cur:
            cur.execute(
                "SELECT EXISTS(SELECT 1 FROM information_schema.tables WHERE table_name=%s)",
                (table,),
            )
            return bool(cur.fetchone()[0])
    else:
        cur = conn.cursor()
        cur.execute(
            "SELECT name FROM sqlite_master WHERE type='table' AND name=?",
            (table,),
        )
        return cur.fetchone() is not None


def bulk_insert_ignore(
    conn,
    table: str,
    rows: list[dict],
    conflict_cols: list[str],
) -> int:
    """
    Insert rows into table, silently skip duplicates on conflict_cols.
    Returns number of rows processed (not necessarily inserted — skipped
    rows are counted too, matching the previous seeder behaviour).

    rows must be non-empty and all have identical keys.
    """
    if not rows:
        return 0

    cols = list(rows[0].keys())
    backend = _backend(conn)

    if backend == "postgres":
        ph = ", ".join(["%s"] * len(cols))
        conflict = ", ".join(conflict_cols)
        sql = (
            f"INSERT INTO {table} ({', '.join(cols)}) VALUES ({ph})"
            f" ON CONFLICT ({conflict}) DO NOTHING"
        )
        params = [tuple(r[c] for c in cols) for r in rows]
        with conn.cursor() as cur:
            cur.executemany(sql, params)
    else:
        ph = ", ".join(["?"] * len(cols))
        sql = f"INSERT OR IGNORE INTO {table} ({', '.join(cols)}) VALUES ({ph})"
        params = [tuple(r[c] for c in cols) for r in rows]
        cur = conn.cursor()
        cur.executemany(sql, params)

    conn.commit()
    return len(rows)
