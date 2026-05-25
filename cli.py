#!/usr/bin/env python3
"""Interactive data-cleaning chatbot CLI.

Free-form Q&A with AI Models (CRUD + web search tools).
CLEAN [query] — LLM scopes records via query_records, then runs OrchestrationTeam.

Commands:
  CLEAN [query]  — run the cleaning pipeline on records matching the query
  QUIT           — exit
  (anything else) — free-form Q&A with Claude
"""

import json
import os
import sys
import time
import urllib.parse
import urllib.request

try:
    from anthropic import RateLimitError as _RateLimitError
except ImportError:
    _RateLimitError = type("RateLimitError", (Exception,), {})  # type: ignore[assignment,misc]

_MAX_RETRIES = 3
_RETRY_BACKOFF_BASE = 0.5  # seconds; doubles each attempt: 0.5, 1.0, 2.0

from config import DB_PATH
from db.schema_discovery import format_schema_for_prompt, get_table_schema, get_column_metadata
from db.sqlite_helpers import (
    delete_raw_data, get_cleaned_data_for_raw, get_raw_data_by_id,
    insert_raw_data, query_records, update_cleaned_data, update_raw_data,
)
from guardrails import (
    GuardrailError, check_age, check_country, check_delete_confirmation,
    check_delete_not_bulk, check_nl_phone_format, check_no_wildcard_update,
    check_protected_fields, check_usa_state,
)
from llm_client_factory import (
    ANTHROPIC, OPENROUTER, build_message_kwargs, build_system_param,
    create_client, log_usage,
)
from prompts import build_system_prompt

_CLIENT, _BACKEND, _MODEL = create_client()
_DB_SCHEMA = format_schema_for_prompt(DB_PATH)

_ACTIVE_DOMAIN = os.getenv("ACTIVE_DOMAIN", "real_estate")

_SQLITE_TO_JSON_TYPE = {
    "TEXT": "string", "INTEGER": "integer", "REAL": "number",
    "NUMERIC": "number", "BLOB": "string", "TIMESTAMP": "string",
}
_AUTO_MANAGED = {
    "id", "imported_at", "cleaned_at", "imported_by", "cleaned_by",
    "applied_at", "applied_by",
}

print(f"[LLM] backend={_BACKEND}  model={_MODEL}")
print(f"[domain] {_ACTIVE_DOMAIN}  |  db={DB_PATH}")


# ── helpers ───────────────────────────────────────────────────────────────────

def _table_props(table: str, exclude: set | None = None) -> dict:
    excl = (exclude or set()) | _AUTO_MANAGED
    cols = get_table_schema(DB_PATH, table)
    descs = get_column_metadata(DB_PATH, table)
    props = {}
    for col in cols:
        if col["name"] in excl:
            continue
        json_type = _SQLITE_TO_JSON_TYPE.get(col["type"].upper().split("(")[0], "string")
        entry = {"type": json_type}
        if col["name"] in descs:
            entry["description"] = descs[col["name"]]
        props[col["name"]] = entry
    return props


def _col_names(table: str, exclude: set | None = None) -> list[str]:
    excl = (exclude or set()) | _AUTO_MANAGED
    return [c["name"] for c in get_table_schema(DB_PATH, table) if c["name"] not in excl]


def _web_search(query: str, max_results: int = 5) -> str:
    api_key = os.getenv("TAVILY_API_KEY")
    if not api_key:
        return "Error: TAVILY_API_KEY not set."
    try:
        payload = json.dumps({
            "api_key": api_key, "query": query,
            "max_results": max_results, "search_depth": "basic", "include_answer": True,
        }).encode()
        req = urllib.request.Request(
            "https://api.tavily.com/search", data=payload,
            headers={"Content-Type": "application/json"}, method="POST",
        )
        with urllib.request.urlopen(req, timeout=10) as resp:
            data = json.loads(resp.read().decode())
        parts = []
        if data.get("answer"):
            parts.append(f"Summary: {data['answer']}\n")
        for i, r in enumerate(data.get("results", [])[:max_results], 1):
            parts.append(f"{i}. {r.get('title','')}\n   {r.get('content','')[:300]}\n   {r.get('url','')}")
        return "\n".join(parts) or f"No results for: {query}"
    except Exception as e:
        return f"Web search failed: {e}"


# ── tool definitions ──────────────────────────────────────────────────────────

def _define_tools() -> list:
    return [
        {
            "name": "web_search",
            "description": "Search the web to verify addresses, postal codes, and municipalities.",
            "input_schema": {
                "type": "object",
                "properties": {
                    "query": {"type": "string"},
                    "max_results": {"type": "integer", "default": 5},
                },
                "required": ["query"],
            },
        },
        {
            "name": "query_records",
            "description": (
                "Search and filter records in the database. Returns up to 50 records. "
                f"raw_data columns: {_col_names('raw_data')}. "
                f"cleaned_data columns: {_col_names('cleaned_data')}."
            ),
            "input_schema": {
                "type": "object",
                "properties": {
                    "table": {"type": "string", "enum": ["raw_data", "cleaned_data", "audit_log"]},
                    "filters": {"type": "object", "description": "Key-value pairs to filter by, e.g. {\"country\": \"CA\"}"},
                    "limit": {"type": "integer", "description": "Max records (default 50, max 50)"},
                },
                "required": ["table"],
            },
        },
        {
            "name": "insert_record",
            "description": "Insert a new record into raw_data.",
            "input_schema": {
                "type": "object",
                "properties": _table_props("raw_data"),
                "required": ["name"],
            },
        },
        {
            "name": "update_record",
            "description": (
                "Update specific fields on a raw_data or cleaned_data record by ID. "
                f"raw_data editable fields: {_col_names('raw_data')}. "
                f"cleaned_data editable fields: {_col_names('cleaned_data', exclude={'raw_data_id'})}."
            ),
            "input_schema": {
                "type": "object",
                "properties": {
                    "table": {"type": "string", "enum": ["raw_data", "cleaned_data"]},
                    "record_id": {"type": "integer"},
                    "fields": {"type": "object", "description": "Fields to update"},
                },
                "required": ["table", "record_id", "fields"],
            },
        },
        {
            "name": "delete_record",
            "description": "Delete a raw_data record by ID. Requires confirm='yes'.",
            "input_schema": {
                "type": "object",
                "properties": {
                    "record_id": {"type": "integer"},
                    "confirm": {"type": "string", "description": "Must be 'yes'"},
                    "override_cleaned_check": {"type": "boolean"},
                },
                "required": ["record_id", "confirm"],
            },
        },
    ]


# ── tool execution ────────────────────────────────────────────────────────────

def _execute_tool(name: str, inp: dict) -> str:
    if name == "web_search":
        result = _web_search(inp["query"], inp.get("max_results", 5))
        print(f"     🔍 {inp['query']}")
        return result

    if name == "query_records":
        table = inp.get("table", "raw_data")
        filters = inp.get("filters") or {}
        limit = min(inp.get("limit", 50), 50)
        try:
            records = query_records(DB_PATH, table, filters, limit)
        except ValueError as e:
            return f"Query error: {e}"
        if not records:
            return f"No records in {table}" + (f" matching {filters}" if filters else ".")
        lines = [f"Found {len(records)} record(s) in {table}:"]
        for r in records:
            lines.append(f"  {dict(r)}")
        return "\n".join(lines)

    if name == "insert_record":
        try:
            check_age(inp.get("age"))
            check_country(inp.get("country"))
        except GuardrailError as e:
            return f"GUARDRAIL BLOCKED: {e}"
        row_id = insert_raw_data(DB_PATH, name=inp["name"], age=inp.get("age"),
            city=inp.get("city"), address=inp.get("address"),
            postal_code=inp.get("postal_code"), municipality=inp.get("municipality"),
            state_province=inp.get("state_province"), country=inp.get("country"),
            phone=inp.get("phone"), imported_by="cli")
        return f"Inserted record ID {row_id}: {inp['name']}"

    if name == "update_record":
        table = inp.get("table", "raw_data")
        record_id = inp.get("record_id")
        fields = inp.get("fields", {})
        try:
            check_no_wildcard_update(fields)
            check_protected_fields(fields, table)
            if "age" in fields:
                check_age(fields["age"])
            if "country" in fields:
                check_country(fields["country"])
        except GuardrailError as e:
            return f"GUARDRAIL BLOCKED: {e}"
        current = (get_raw_data_by_id(DB_PATH, record_id) if table == "raw_data"
                   else (query_records(DB_PATH, "cleaned_data", {"id": record_id}, 1) or [None])[0])
        if not current:
            return f"Record {record_id} not found in {table}."
        country = fields.get("country", current.get("country", ""))
        try:
            if country in ("USA", "United States") and "state_province" in fields:
                check_usa_state(fields["state_province"])
            if country in ("NL", "Netherlands") and "phone" in fields:
                check_nl_phone_format(fields["phone"])
        except GuardrailError as e:
            return f"GUARDRAIL BLOCKED: {e}"
        try:
            updated = update_raw_data(DB_PATH, record_id, fields) if table == "raw_data" \
                      else update_cleaned_data(DB_PATH, record_id, fields)
        except ValueError as e:
            return f"GUARDRAIL BLOCKED: {e}"
        return f"Updated {table} record {record_id}: {list(fields.keys())}." if updated \
               else f"Record {record_id} not found in {table}."

    if name == "delete_record":
        record_id = inp.get("record_id")
        try:
            check_delete_not_bulk(record_id)
            check_delete_confirmation(inp.get("confirm", ""))
        except GuardrailError as e:
            return f"GUARDRAIL BLOCKED: {e}"
        cleaned = get_cleaned_data_for_raw(DB_PATH, record_id)
        if cleaned and not inp.get("override_cleaned_check"):
            return (f"GUARDRAIL BLOCKED: Record {record_id} has {len(cleaned)} cleaned_data "
                    f"entries. Set override_cleaned_check=true to force.")
        return f"Deleted record {record_id}." if delete_raw_data(DB_PATH, record_id) \
               else f"Record {record_id} not found."

    return f"Unknown tool: {name}"


# ── LLM call with retry ───────────────────────────────────────────────────────

def _call_llm(*, model: str, max_tokens: int, system, messages: list,
              tools: list, **kwargs):
    """Call the LLM, retrying up to _MAX_RETRIES times on transient errors."""
    last_exc: Exception | None = None
    for attempt in range(_MAX_RETRIES):
        try:
            return _CLIENT.messages.create(
                model=model, max_tokens=max_tokens,
                system=system, messages=messages, tools=tools, **kwargs,
            )
        except (ConnectionError, TimeoutError, _RateLimitError) as e:
            last_exc = e
            wait = _RETRY_BACKOFF_BASE * (2 ** attempt)
            print(f"  ⚠ LLM error ({type(e).__name__}), retrying in {wait:.1f}s "
                  f"(attempt {attempt + 1}/{_MAX_RETRIES})")
            if attempt < _MAX_RETRIES - 1:
                time.sleep(wait)
    raise RuntimeError(f"LLM call failed after {_MAX_RETRIES} attempts: {last_exc}")


# ── LLM loop ─────────────────────────────────────────────────────────────────

def _llm_loop(messages: list, system: str | list, tools: list) -> str:
    """Run tool-use loop until stop_reason != tool_use. Returns final text."""
    kwargs = build_message_kwargs(_BACKEND)
    while True:
        resp = _call_llm(
            model=_MODEL, max_tokens=2048,
            system=system, messages=messages, tools=tools, **kwargs,
        )
        log_usage(_BACKEND, resp.usage)

        if resp.stop_reason != "tool_use":
            return next((b.text for b in resp.content if hasattr(b, "text")), "")

        messages.append({"role": "assistant", "content": resp.content})
        tool_results = []
        for block in resp.content:
            if not (hasattr(block, "type") and block.type == "tool_use"):
                continue
            result = _execute_tool(block.name, getattr(block, "input", {}))
            print(f"  🔧 {block.name} → {result[:120]}")
            tool_results.append({"type": "tool_result", "tool_use_id": block.id, "content": result})
        messages.append({"role": "user", "content": tool_results})


# ── CLEAN flow ────────────────────────────────────────────────────────────────

def _handle_clean(query: str) -> None:
    from cleaning.orchestrator_v2 import OrchestrationTeam, run_cleaning_workflow_v2
    from skills.registry import SkillRegistry

    print(f"\nScoping records for: {query!r}")

    # Step 1: LLM interprets the query and calls query_records to get matching records
    scope_system = build_system_param(
        _BACKEND,
        "You are a record selector. The user describes which records to clean. "
        "Call query_records exactly once with appropriate filters to fetch those records, then stop. "
        f"Available raw_data columns: {_col_names('raw_data')}.",
    )
    scope_messages = [{"role": "user", "content": f"Find records to clean: {query}"}]
    kwargs = build_message_kwargs(_BACKEND)
    tools = _define_tools()

    fetched_records: list = []
    resp = _call_llm(
        model=_MODEL, max_tokens=512,
        system=scope_system, messages=scope_messages, tools=tools, **kwargs,
    )
    log_usage(_BACKEND, resp.usage)

    for block in resp.content:
        if hasattr(block, "type") and block.type == "tool_use" and block.name == "query_records":
            inp = getattr(block, "input", {})
            table = inp.get("table", "raw_data")
            filters = inp.get("filters") or {}
            limit = min(inp.get("limit", 50), 50)
            try:
                fetched_records = query_records(DB_PATH, table, filters, limit)
                print(f"  Scoped {len(fetched_records)} record(s) from {table} with filters {filters}")
            except ValueError as e:
                print(f"  Query error: {e}")
            break

    if not fetched_records:
        print("  No records found — nothing to clean.")
        return

    # Step 2: Run pipeline
    print(f"\nRunning pipeline on {len(fetched_records)} record(s) [domain={_ACTIVE_DOMAIN}]...")
    report = run_cleaning_workflow_v2(list(fetched_records), domain=_ACTIVE_DOMAIN, verbose=True)

    print(f"\n{'='*60}")
    print(f"RESULTS: {report.summary_text}")
    print(f"Timing:")
    for phase, secs in report.timing.items():
        print(f"  {phase:<35} {secs:.2f}s")
    if report.errors:
        print(f"\nErrors: {report.errors}")
    print(f"{'='*60}\n")


# ── main loop ─────────────────────────────────────────────────────────────────

def run() -> None:
    system_prompt = build_system_prompt("CA", schema=_DB_SCHEMA)
    system_param = build_system_param(_BACKEND, system_prompt)
    tools = _define_tools()
    messages: list = []

    print(f"\n{'='*60}")
    print("Data Cleaning CLI")
    print("  CLEAN [query]  — run the cleaning pipeline")
    print("  QUIT           — exit")
    print("  (anything else) — ask PikkaDataBot")
    print(f"{'='*60}\n")

    while True:
        try:
            user_input = input("You: ").strip()
        except (EOFError, KeyboardInterrupt):
            print("\nBye.")
            break

        if not user_input:
            continue

        upper = user_input.upper()
        if upper in ("QUIT", "EXIT", "Q"):
            print("Bye.")
            break

        if upper.startswith("CLEAN"):
            query = user_input[5:].strip() or "all uncleaned records"
            _handle_clean(query)
            continue

        # Free-form Q&A
        messages.append({"role": "user", "content": user_input})
        response = _llm_loop(messages, system_param, tools)
        messages.append({"role": "assistant", "content": response})
        print(f"\nPikkaDataBot: {response}\n")


if __name__ == "__main__":
    run()
