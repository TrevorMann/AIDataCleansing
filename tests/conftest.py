"""Shared pytest fixtures and helpers."""
from unittest.mock import MagicMock


def _mock_conn(*fetchall_results):
    """Build a mock psycopg2 connection with cursor returning preset data.

    Returns (conn, cur). ``cur.fetchall`` will pop results in order from
    *fetchall_results* on each successive call.
    """
    conn = MagicMock()
    cur = conn.cursor.return_value.__enter__.return_value
    cur.fetchall.side_effect = list(fetchall_results)
    return conn, cur
