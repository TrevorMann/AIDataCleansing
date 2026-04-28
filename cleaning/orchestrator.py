"""Orchestrator for the cleaning workflow.

This module's responsibilities:
- Interpret the user query into filters
- Fetch matching raw records
- Pre-clean (deterministic)
- Group records by country (per-record auto-routing)
- Dispatch each group to a CleaningAgent
- Merge results
- Persist to cleaned_data + flags + audit_log
- Return a CleaningRunReport

The main entrypoint run_cleaning_workflow() is added in Task 12 of the plan;
this file currently exposes the helpers needed by it.
"""
from __future__ import annotations

import logging
from typing import Optional

from cleaning.flags import Flag
from cleaning.pre_cleaner import get_country_code, pre_clean_record, needs_research
from cleaning.types import CleaningOutput
from db_helpers import query_records
from validate_data_quality import get_records_needing_cleaning


logger = logging.getLogger(__name__)


_KEYWORD_TO_COUNTRY = {
    "canadian": "CA", "canada": "CA",
    "american": "USA", "usa": "USA", "united states": "USA", "u.s.": "USA",
    "dutch": "NL", "netherlands": "NL", "holland": "NL",
    "mexican": "MX", "mexico": "MX",
    "japanese": "JP", "japan": "JP",
}


def detect_country_filter(query: str, override: Optional[str] = None) -> Optional[str]:
    """Return canonical country code if the query unambiguously names one country.

    `override` wins over keyword detection. "north american" / "european" /
    "all uncleaned" return None (per-record auto-routing).
    """
    if override:
        return override
    q = (query or "").lower()
    if "north american" in q or "european" in q or "all" in q or "uncleaned" in q:
        return None
    matched = {code for kw, code in _KEYWORD_TO_COUNTRY.items() if kw in q}
    return matched.pop() if len(matched) == 1 else None


def interpret_query(query: str) -> dict:
    """Parse the user query into a filters dict (country, scope, limit).

    Uses Python keyword regex rather than an LLM call — fast, free, and
    sufficient for the simple trigger vocabulary. An LLM upgrade path exists
    if query complexity grows beyond keyword matching.
    """
    q = (query or "").lower()
    filters: dict = {}
    code = detect_country_filter(query)
    if code:
        filters["country"] = code
    if "all" in q or "uncleaned" in q or "dirty" in q:
        filters["scope"] = "all_uncleaned"
    if "first" in q:
        filters["scope"] = "first_batch"
        filters["limit"] = 5
    return filters


def fetch_records(db_path: str, filters: dict) -> list[dict]:
    """Fetch raw records from DB filtered by country / scope.

    Country filtering uses pre_cleaner.get_country_code so that 'Canada', 'CA',
    'CDN', etc. all match the same canonical code.

    When filters["scope"] == "all_uncleaned", only records that do NOT already
    have a cleaned_data row are returned. This prevents re-processing on queries
    like "CLEAN all uncleaned data".
    """
    import sqlite3
    records = query_records(db_path, table="raw_data", filters={}, limit=50)

    if filters.get("scope") == "all_uncleaned":
        conn = sqlite3.connect(db_path)
        try:
            already_cleaned = {
                row[0] for row in conn.execute(
                    "SELECT DISTINCT raw_data_id FROM cleaned_data"
                ).fetchall()
            }
        finally:
            conn.close()
        records = [r for r in records if r["id"] not in already_cleaned]

    if "country" in filters:
        target = filters["country"]
        records = [r for r in records if get_country_code(r.get("country", "")) == target]
    if "limit" in filters:
        records = records[: filters["limit"]]
    return records


def group_by_country(records: list[dict]) -> dict[Optional[str], list[dict]]:
    """Group records by canonical country code. Unknown country → key None."""
    groups: dict[Optional[str], list[dict]] = {}
    for r in records:
        code = get_country_code(r.get("country") or "")
        groups.setdefault(code, []).append(r)
    return groups


def merge_results(
    pre_cleaned: list[dict], agent_outputs: list[CleaningOutput],
) -> list[dict]:
    """Combine pre-clean changes with agent/escalation output for persistence.

    Returns a list of dicts ready for insert_cleaned_data + audit_log generation.
    """
    by_id = {out.cleaned_record["id"]: out for out in agent_outputs}
    merged: list[dict] = []
    for record in pre_cleaned:
        out = by_id.get(record["id"])
        result = dict(record)
        result["raw_data_id"] = record["id"]
        if out is not None:
            for k in ("postal_code", "municipality", "validation_notes",
                      "country", "state_province"):
                if out.cleaned_record.get(k):
                    result[k] = out.cleaned_record[k]
        # Combine pre-clean change log with agent's notes
        pre_changes = record.get("_pre_clean_changes", [])
        existing_notes = result.get("validation_notes", "")
        parts = []
        if pre_changes:
            parts.append("Pre-cleaned: " + "; ".join(pre_changes))
        if existing_notes:
            parts.append(existing_notes)
        result["validation_notes"] = " | ".join(parts) if parts else ""
        result["_flags"] = out.flags if out else []
        merged.append(result)
    return merged
