"""Tests for the flags table schema and db_helpers CRUD."""
import sqlite3


def test_flags_table_exists(tmp_db):
    conn = sqlite3.connect(tmp_db)
    rows = conn.execute(
        "SELECT name FROM sqlite_master WHERE type='table' AND name='flags'"
    ).fetchall()
    conn.close()
    assert len(rows) == 1


def test_flags_table_columns(tmp_db):
    conn = sqlite3.connect(tmp_db)
    cols = {r[1] for r in conn.execute("PRAGMA table_info(flags)").fetchall()}
    conn.close()
    expected = {
        'id', 'raw_data_id', 'cleaned_data_id', 'flag_type', 'severity',
        'reason', 'raised_by', 'raised_at', 'resolved_at', 'resolved_by',
        'resolution_note',
    }
    assert expected.issubset(cols)


def test_unresolved_index_exists(tmp_db):
    conn = sqlite3.connect(tmp_db)
    rows = conn.execute(
        "SELECT name FROM sqlite_master WHERE type='index' AND name='idx_flags_unresolved'"
    ).fetchall()
    conn.close()
    assert len(rows) == 1


def test_insert_flag_returns_id(tmp_db):
    from db_helpers import insert_raw_data, insert_flag
    raw_id = insert_raw_data(tmp_db, name="x", country="CA")
    flag_id = insert_flag(
        tmp_db,
        raw_data_id=raw_id, cleaned_data_id=None,
        flag_type="postal_unresolved", severity="NEEDS_REVIEW",
        reason="postal could not be verified", raised_by="agent:CA",
    )
    assert isinstance(flag_id, int) and flag_id > 0


def test_query_flags_unresolved_only(tmp_db):
    from db_helpers import insert_raw_data, insert_flag, update_flag_resolution, query_flags
    raw_id = insert_raw_data(tmp_db, name="x", country="CA")
    f1 = insert_flag(tmp_db, raw_data_id=raw_id, flag_type="t", severity="WARN",
                     reason="r1", raised_by="agent:CA")
    f2 = insert_flag(tmp_db, raw_data_id=raw_id, flag_type="t", severity="WARN",
                     reason="r2", raised_by="agent:CA")
    update_flag_resolution(tmp_db, f1, resolved_by="trevor", note="manual fix")

    unresolved = query_flags(tmp_db, only_unresolved=True)
    assert len(unresolved) == 1
    assert unresolved[0]['id'] == f2

    all_flags = query_flags(tmp_db, only_unresolved=False)
    assert len(all_flags) == 2
