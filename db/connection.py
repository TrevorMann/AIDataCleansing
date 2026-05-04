import os
import sqlite3
from typing import Any

from config import get_config_value


def get_backend() -> str:
    """Return the configured database backend."""
    return (get_config_value("DB_BACKEND", "sqlite") or "sqlite").strip().lower()


def get_pg_dsn() -> str:
    """Return the configured PostgreSQL DSN or raise if missing."""
    dsn = get_config_value("POSTGRES_DSN")
    if not dsn:
        raise ValueError("POSTGRES_DSN must be set when DB_BACKEND=postgres")
    return dsn


def get_connection(path_or_dsn: str) -> Any:
    """Return a backend-specific database connection."""
    backend = get_backend()
    if backend == "postgres":
        from psycopg import connect
        from psycopg.rows import dict_row

        dsn = path_or_dsn or get_pg_dsn()
        return connect(dsn, row_factory=dict_row)

    conn = sqlite3.connect(path_or_dsn)
    conn.row_factory = sqlite3.Row
    return conn
