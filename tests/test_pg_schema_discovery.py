from unittest.mock import MagicMock, patch

from db.pg_schema_discovery import MIN_ANNOTATION_CONFIDENCE, get_column_metadata


def test_get_column_metadata_filters_by_min_confidence():
    conn = MagicMock()
    conn.cursor.return_value.fetchall.return_value = []
    with patch("db.pg_schema_discovery.get_db_connection", return_value=conn):
        get_column_metadata("dsn", "raw_data")

    executed_sql, params = conn.cursor.return_value.execute.call_args[0]
    assert "confidence" in executed_sql
    assert MIN_ANNOTATION_CONFIDENCE in params


def test_get_column_metadata_returns_descriptions():
    conn = MagicMock()
    conn.cursor.return_value.fetchall.return_value = [
        {"column_name": "city", "description": "City name"},
        {"column_name": "notes", "description": None},
    ]
    with patch("db.pg_schema_discovery.get_db_connection", return_value=conn):
        result = get_column_metadata("dsn", "raw_data")

    assert result == {"city": "City name"}
