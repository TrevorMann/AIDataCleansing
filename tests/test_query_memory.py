"""Tests for C1: query_pattern_memory schema helpers."""

import json
from unittest.mock import MagicMock

import pytest

from db.pg_query_memory import (
    top_queries_for,
    record_query_outcome,
    update_source_score,
    load_query_packs,
)


def _make_conn(fetchall_rows=None, fetchone_row=None):
    mock_cur = MagicMock()
    mock_cur.__enter__ = lambda s: s
    mock_cur.__exit__ = MagicMock(return_value=False)
    mock_cur.fetchall.return_value = fetchall_rows or []
    mock_cur.fetchone.return_value = fetchone_row
    mock_conn = MagicMock()
    mock_conn.cursor.return_value = mock_cur
    return mock_conn, mock_cur


# --- top_queries_for ---

def test_top_queries_for_returns_list():
    rows = [("site:canadapost.ca postal code {postal_code}",), ("{postal_code} canada municipality",)]
    conn, cur = _make_conn(fetchall_rows=rows)
    result = top_queries_for(conn, "real_estate", "postal_unresolved", k=3)
    assert result == ["site:canadapost.ca postal code {postal_code}", "{postal_code} canada municipality"]
    cur.execute.assert_called_once()


def test_top_queries_for_empty_returns_empty():
    conn, _ = _make_conn(fetchall_rows=[])
    result = top_queries_for(conn, "real_estate", "postal_unresolved")
    assert result == []


# --- record_query_outcome ---

def test_record_query_outcome_success():
    conn, cur = _make_conn()
    record_query_outcome(conn, "real_estate", "postal_unresolved", "{postal_code} municipality", success=True)
    sql = cur.execute.call_args[0][0]
    assert "success_count" in sql
    conn.commit.assert_called_once()


def test_record_query_outcome_failure():
    conn, cur = _make_conn()
    record_query_outcome(conn, "real_estate", "postal_unresolved", "{postal_code} municipality", success=False)
    sql = cur.execute.call_args[0][0]
    assert "failure_count" in sql


# --- update_source_score ---

def test_update_source_score_success():
    conn, cur = _make_conn()
    update_source_score(conn, "real_estate", "canadapost.ca", success=True)
    sql = cur.execute.call_args[0][0]
    assert "trust_score" in sql
    conn.commit.assert_called_once()


def test_update_source_score_failure():
    conn, cur = _make_conn()
    update_source_score(conn, "real_estate", "shady-site.com", success=False)
    sql = cur.execute.call_args[0][0]
    assert "trust_score" in sql


# --- load_query_packs ---

def test_load_query_packs_real_estate(tmp_path):
    yaml_content = """
domain: test
gap_types:
  postal_unresolved:
    seed_queries:
      - "site:canadapost.ca {postal_code}"
      - "{postal_code} municipality"
  municipality_ambiguous:
    seed_queries:
      - "{postal_code} {state_province} municipality"
trusted_sources:
  - canadapost.ca
  - wikipedia.org
"""
    packs_file = tmp_path / "query_packs.yaml"
    packs_file.write_text(yaml_content)

    conn, cur = _make_conn()
    count = load_query_packs(conn, "real_estate", str(packs_file))

    assert count == 3  # 2 + 1 seed queries
    conn.commit.assert_called()
    # source_registry also inserted
    calls = [str(c) for c in cur.execute.call_args_list]
    assert any("source_registry" in c for c in calls)


def test_load_query_packs_missing_file():
    conn, _ = _make_conn()
    with pytest.raises(FileNotFoundError):
        load_query_packs(conn, "real_estate", "/nonexistent/query_packs.yaml")


# --- QueryPackSeeder ---

def test_query_pack_seeder_parse():
    from seeders.real_estate.query_packs import QueryPackSeeder
    seeder = QueryPackSeeder()
    payload = {
        "gap_types": {
            "postal_unresolved": {
                "seed_queries": ["query1", "query2"]
            }
        }
    }
    rows = seeder.parse(payload)
    assert len(rows) == 2
    assert all(r["domain"] == "real_estate" for r in rows)
    assert all(r["gap_type"] == "postal_unresolved" for r in rows)
