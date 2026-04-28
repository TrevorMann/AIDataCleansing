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
import time
from typing import Optional

from cleaning.agent import CleaningAgent
from cleaning.cache import WebSearchCache
from cleaning.escalation import EscalationAgent
from cleaning.flags import FlagType, persist_flags
from cleaning.llm_client import Clients, build_clients
from cleaning.pre_cleaner import get_country_code, pre_clean_record
from cleaning.types import CleaningOutput, CleaningRunReport
from config import DB_PATH as DEFAULT_DB_PATH
from db_helpers import insert_audit_log, insert_cleaned_data, query_records
from prompts import build_system_prompt
from prompts.research import build_research_prompt
from schema_discovery import format_schema_for_prompt


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


_WEB_SEARCH_TOOL = {
    "name": "web_search",
    "description": "Search the web to verify addresses, postal codes, and municipalities.",
    "input_schema": {
        "type": "object",
        "properties": {
            "query": {"type": "string", "description": "Search query"},
            "max_results": {"type": "integer", "default": 5},
        },
        "required": ["query"],
    },
}


def persist_outputs(db_path: str, merged: list[dict]) -> tuple[int, list[dict]]:
    """Persist each merged record + its flags + audit log entries.

    Each record is persisted in its own transaction (one record's failure does
    not roll back the rest). Returns (saved_count, errors).
    """
    saved = 0
    errors: list[dict] = []
    for r in merged:
        try:
            cleaned_id = insert_cleaned_data(
                db_path,
                raw_data_id=r["raw_data_id"],
                name=r.get("name"),
                age=r.get("age"),
                city=r.get("city"),
                address=r.get("address"),
                postal_code=r.get("postal_code"),
                municipality=r.get("municipality"),
                state_province=r.get("state_province"),
                country=r.get("country"),
                phone=r.get("phone"),
                validation_notes=r.get("validation_notes", ""),
                cleaned_by="cleaning-workflow",
            )
            for change in r.get("_pre_clean_changes", []):
                insert_audit_log(
                    db_path, raw_data_id=r["raw_data_id"], cleaned_data_id=cleaned_id,
                    rule_applied="pre_clean", description=change,
                    applied_by="pre-cleaner",
                )
            persist_flags(
                db_path, raw_data_id=r["raw_data_id"], cleaned_data_id=cleaned_id,
                flags=r.get("_flags", []),
            )
            saved += 1
        except Exception as e:
            errors.append({"raw_data_id": r.get("raw_data_id"), "error": str(e)})
    return saved, errors


def run_cleaning_workflow(
    query: str,
    *,
    country_override: Optional[str] = None,
    db_path: Optional[str] = None,
    clients: Optional[Clients] = None,
) -> CleaningRunReport:
    """Public entrypoint. See spec §5.6."""
    db_path = db_path or DEFAULT_DB_PATH
    clients = clients or build_clients()
    timing: dict[str, float] = {}

    # 1. Interpret
    t = time.time()
    filters = interpret_query(query)
    if country_override:
        filters["country"] = country_override
    timing["interpret"] = time.time() - t

    # 2. Fetch
    t = time.time()
    records = fetch_records(db_path, filters)
    timing["fetch"] = time.time() - t
    if not records:
        return _empty_report(timing, "No records found matching your query.")

    # 3. Pre-clean
    t = time.time()
    pre_cleaned = [pre_clean_record(r) for r in records]
    timing["pre_clean"] = time.time() - t

    # 4. Group by country
    t = time.time()
    groups = group_by_country(pre_cleaned)
    timing["group"] = time.time() - t

    # 5. Dispatch
    t = time.time()
    schema = format_schema_for_prompt(db_path)
    web_cache = WebSearchCache()
    escalator = EscalationAgent(
        llm_client=clients.deep, web_cache=web_cache, tools=[_WEB_SEARCH_TOOL],
    )

    agent_outputs: list[CleaningOutput] = []
    for code, batch in groups.items():
        if code is None:
            # Unknown-country bypass: orchestrator dispatches to escalator directly
            for rec in batch:
                out = escalator.investigate(
                    record=rec, country_code=None,
                    flag_hints=[FlagType.UNKNOWN_COUNTRY],
                    prior_search_log=[],
                )
                agent_outputs.append(out)
            continue
        agent = CleaningAgent(
            country_code=code,
            system_prompt=build_system_prompt(code, schema=schema),
            research_prompt_builder=build_research_prompt,
            tools=[_WEB_SEARCH_TOOL],
            llm_client=clients.standard,
            web_cache=web_cache,
            escalator=escalator,
        )
        agent_outputs.extend(agent.process(batch))
    timing["research"] = time.time() - t

    # 6. Merge
    t = time.time()
    merged = merge_results(pre_cleaned, agent_outputs)
    timing["merge"] = time.time() - t

    # 7. Persist
    t = time.time()
    saved, errors = persist_outputs(db_path, merged)
    timing["persist"] = time.time() - t

    # 8. Build report
    flags_by_type: dict[str, int] = {}
    flag_summary: list[dict] = []
    for r in merged:
        for f in r.get("_flags", []):
            flags_by_type[f.flag_type.value] = flags_by_type.get(f.flag_type.value, 0) + 1
            flag_summary.append({"raw_data_id": r["raw_data_id"],
                                 "flag_type": f.flag_type.value,
                                 "severity": f.severity.value, "reason": f.reason})

    stats = web_cache.stats()
    summary_text = (
        f"Cleaned {saved}/{len(records)} records. "
        f"{len(flag_summary)} flag(s) raised. "
        f"Cache: {stats['hits']} hits / "
        f"{stats['misses']} misses. "
        f"Total: {sum(timing.values()):.2f}s."
    )
    return CleaningRunReport(
        records_processed=len(records),
        cleaned_count=saved,
        flagged_count=len(flag_summary),
        flags_by_type=flags_by_type,
        cache_stats=stats,
        timing=timing,
        flag_summary=flag_summary,
        errors=errors,
        summary_text=summary_text,
    )


def _empty_report(timing: dict, message: str) -> CleaningRunReport:
    return CleaningRunReport(
        records_processed=0, cleaned_count=0, flagged_count=0,
        flags_by_type={}, cache_stats={"hits": 0, "misses": 0, "queries_cached": 0},
        timing=timing, flag_summary=[], errors=[], summary_text=message,
    )
