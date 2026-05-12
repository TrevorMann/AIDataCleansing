"""Tests for RecordLinker skill."""

import pytest
from skills._common.record_linker.record_linker import RecordLinker


BASIC_CONFIG = {
    "blocking_fields": [],
    "match_rules": [
        {"name": "email_exact", "fields": ["email"], "match_type": "exact", "weight": 1.0},
        {
            "name": "name_company",
            "fields": ["first_name", "last_name", "company"],
            "match_type": "fuzzy",
            "threshold": 0.85,
            "weight": 0.90,
        },
    ],
}


def test_exact_match_links_records():
    linker = RecordLinker(BASIC_CONFIG)
    record = {"id": "A", "email": "user@example.com"}
    candidates = [
        {"id": "B", "email": "user@example.com"},
        {"id": "C", "email": "other@example.com"},
    ]
    result = linker.run(record, tools={"candidates": candidates})
    linked = result.get("_linked_records", [])
    ids = [r["id"] for r in linked]
    assert "B" in ids
    assert "C" not in ids


def test_fuzzy_match_links_near_identical():
    linker = RecordLinker(BASIC_CONFIG)
    record = {"id": "A", "first_name": "John", "last_name": "Smith", "company": "Acme Inc"}
    candidates = [{"id": "B", "first_name": "John", "last_name": "Smyth", "company": "Acme Inc"}]
    result = linker.run(record, tools={"candidates": candidates})
    linked = result.get("_linked_records", [])
    assert len(linked) == 1
    assert linked[0]["matched_rule"] == "name_company"


def test_no_match_returns_empty():
    linker = RecordLinker(BASIC_CONFIG)
    record = {"id": "A", "email": "a@a.com"}
    candidates = [{"id": "B", "email": "b@b.com"}]
    result = linker.run(record, tools={"candidates": candidates})
    assert result.get("_linked_records", []) == []


def test_record_fields_not_mutated():
    """RecordLinker must never change source field values."""
    linker = RecordLinker(BASIC_CONFIG)
    record = {"id": "A", "email": "user@example.com", "first_name": "John"}
    candidates = [{"id": "B", "email": "user@example.com", "first_name": "Jane"}]
    result = linker.run(record, tools={"candidates": candidates})
    assert result["first_name"] == "John"   # not overwritten with candidate's value
    assert result["email"] == "user@example.com"


def test_link_batch_transitive_grouping():
    """A→B on email, B→C on name → A,B,C same group."""
    config = {
        "blocking_fields": [],
        "match_rules": [
            {"name": "email", "fields": ["email"], "match_type": "exact", "weight": 1.0},
            {
                "name": "name",
                "fields": ["first_name", "last_name"],
                "match_type": "fuzzy",
                "threshold": 0.85,
                "weight": 0.9,
            },
        ],
    }
    linker = RecordLinker(config)
    records = [
        {"id": "A", "email": "shared@x.com", "first_name": "Alice", "last_name": "Smith"},
        {"id": "B", "email": "shared@x.com", "first_name": "Alice", "last_name": "Smyth"},
        {"id": "C", "email": "other@x.com",  "first_name": "Alice", "last_name": "Smith"},
    ]
    result = linker.link_batch(records)
    groups = {r["id"]: r["_group_id"] for r in result}
    # A and B share email → same group
    assert groups["A"] == groups["B"]
    # C matches B/A on name → same group transitively
    assert groups["C"] == groups["A"]


def test_link_batch_no_cross_contamination():
    """Unrelated records get distinct group_ids."""
    linker = RecordLinker(BASIC_CONFIG)
    records = [
        {"id": "X", "email": "x@x.com"},
        {"id": "Y", "email": "y@y.com"},
    ]
    result = linker.link_batch(records)
    groups = {r["id"]: r["_group_id"] for r in result}
    assert groups["X"] != groups["Y"]


def test_audit_not_in_record():
    linker = RecordLinker(BASIC_CONFIG)
    result = linker.run({"id": "A", "email": "a@a.com"}, tools={"candidates": []})
    assert "_decisions" not in result
