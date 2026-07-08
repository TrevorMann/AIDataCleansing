"""Tests for db/upsert.py backend-agnostic seeder helpers."""

import sqlite3

import pytest

from db.upsert import bulk_upsert


def _sqlite_conn_with_table():
    conn = sqlite3.connect(":memory:")
    conn.execute(
        """
        CREATE TABLE column_metadata (
            domain        TEXT NOT NULL,
            table_name    TEXT NOT NULL,
            column_name   TEXT NOT NULL,
            description   TEXT,
            gap_detection TEXT,
            PRIMARY KEY (domain, table_name, column_name)
        )
        """
    )
    return conn


def test_bulk_upsert_inserts_new_rows():
    conn = _sqlite_conn_with_table()
    rows = [
        {"domain": "re", "table_name": "raw", "column_name": "postal_code",
         "description": "ZIP", "gap_detection": '{"missing": true}'},
    ]
    n = bulk_upsert(conn, "column_metadata", rows,
                    conflict_cols=["domain", "table_name", "column_name"],
                    update_cols=["description", "gap_detection"])
    assert n == 1
    got = conn.execute(
        "SELECT description, gap_detection FROM column_metadata"
    ).fetchone()
    assert got == ("ZIP", '{"missing": true}')


def test_bulk_upsert_updates_update_cols_on_conflict():
    conn = _sqlite_conn_with_table()
    key = {"domain": "re", "table_name": "raw", "column_name": "postal_code"}
    bulk_upsert(conn, "column_metadata",
                [{**key, "description": "old", "gap_detection": None}],
                conflict_cols=list(key), update_cols=["description", "gap_detection"])
    # Second upsert on the same key overwrites the update_cols.
    bulk_upsert(conn, "column_metadata",
                [{**key, "description": "new", "gap_detection": '{"missing": true}'}],
                conflict_cols=list(key), update_cols=["description", "gap_detection"])
    rows = conn.execute(
        "SELECT description, gap_detection FROM column_metadata"
    ).fetchall()
    assert rows == [("new", '{"missing": true}')]  # one row, updated in place


def test_bulk_upsert_empty_rows_is_noop():
    conn = _sqlite_conn_with_table()
    assert bulk_upsert(conn, "column_metadata", [],
                       conflict_cols=["domain"], update_cols=["description"]) == 0


class _FakePGCursor:
    last_sql = None

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False

    def executemany(self, sql, params):
        type(self).last_sql = sql
        self.params = list(params)


class _FakePGConn:
    """Stands in for a psycopg connection so _backend() picks the postgres branch."""

    def __init__(self):
        self._cur = _FakePGCursor()

    def cursor(self):
        return self._cur

    def commit(self):
        pass


_FakePGConn.__module__ = "psycopg"  # _backend keys on type(conn).__module__


def test_bulk_upsert_postgres_builds_pyformat_do_update_sql():
    conn = _FakePGConn()
    rows = [{"domain": "re", "table_name": "raw", "column_name": "c",
             "description": "d", "gap_detection": None}]
    bulk_upsert(conn, "column_metadata", rows,
                conflict_cols=["domain", "table_name", "column_name"],
                update_cols=["description", "gap_detection"])
    sql = _FakePGCursor.last_sql
    assert "%s" in sql and "?" not in sql          # postgres placeholders
    assert "ON CONFLICT (domain, table_name, column_name)" in sql
    assert "DO UPDATE SET description = excluded.description" in sql
    assert "gap_detection = excluded.gap_detection" in sql
