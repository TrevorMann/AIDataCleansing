"""Ad-hoc conversation loop for the REPL.

Separate from the cleaning workflow because it serves a different purpose:
ad-hoc questions like 'show me record 5', 'delete record 12', 'insert this
new contact'. Has full CRUD tool access; cleaning agents do NOT (see spec §5.8).
"""
from __future__ import annotations

import logging
from typing import Any

from cleaning.llm_client import Clients
from db_helpers import (
    delete_raw_data, get_cleaned_data_for_raw, get_raw_data_by_id,
    insert_raw_data, query_records, update_cleaned_data, update_raw_data,
)
from guardrails import (
    GuardrailError, check_age, check_country, check_delete_confirmation,
    check_delete_not_bulk, check_nl_phone_format, check_no_wildcard_update,
    check_protected_fields, check_usa_state,
)
from schema_discovery import get_column_metadata, get_table_schema


logger = logging.getLogger(__name__)


_SQLITE_TO_JSON_TYPE = {
    "TEXT": "string", "INTEGER": "integer", "REAL": "number",
    "NUMERIC": "number", "BLOB": "string", "TIMESTAMP": "string",
}
_AUTO_MANAGED = {
    "id", "imported_at", "cleaned_at", "imported_by", "cleaned_by",
    "applied_at", "applied_by",
}


def _build_table_properties(db_path: str, table_name: str,
                            exclude: set | None = None) -> dict:
    exclude = (exclude or set()) | _AUTO_MANAGED
    columns = get_table_schema(db_path, table_name)
    descriptions = get_column_metadata(db_path, table_name)
    props = {}
    for col in columns:
        if col["name"] in exclude:
            continue
        sqlite_type = col["type"].upper().split("(")[0].strip()
        json_type = _SQLITE_TO_JSON_TYPE.get(sqlite_type, "string")
        entry = {"type": json_type}
        if col["name"] in descriptions:
            entry["description"] = descriptions[col["name"]]
        props[col["name"]] = entry
    return props


def _column_names(db_path: str, table_name: str,
                  exclude: set | None = None) -> list[str]:
    exclude = (exclude or set()) | _AUTO_MANAGED
    return [c["name"] for c in get_table_schema(db_path, table_name)
            if c["name"] not in exclude]


_SYSTEM_PROMPT = """You are a helpful data assistant for a real estate cleaning database.
You can answer questions about records, search the web, and modify the database
when explicitly asked. Use the tools provided. Be concise."""


class AdHocConversation:
    def __init__(self, *, clients: Clients, db_path: str):
        self.clients = clients
        self.db_path = db_path
        self.messages: list[dict] = []
        self.tools = self._build_tools()

    def _build_tools(self) -> list[dict]:
        return [
            {
                "name": "web_search",
                "description": "Search the web for verification.",
                "input_schema": {
                    "type": "object",
                    "properties": {"query": {"type": "string"},
                                   "max_results": {"type": "integer", "default": 5}},
                    "required": ["query"],
                },
            },
            {
                "name": "insert_record",
                "description": "Insert a new record into raw_data.",
                "input_schema": {
                    "type": "object",
                    "properties": _build_table_properties(self.db_path, "raw_data"),
                    "required": ["name"],
                },
            },
            {
                "name": "update_record",
                "description": (
                    "Update fields on raw_data or cleaned_data by ID. "
                    f"raw_data fields: {_column_names(self.db_path, 'raw_data')}. "
                    f"cleaned_data fields: {_column_names(self.db_path, 'cleaned_data', exclude={'raw_data_id'})}."
                ),
                "input_schema": {
                    "type": "object",
                    "properties": {
                        "table": {"type": "string", "enum": ["raw_data", "cleaned_data"]},
                        "record_id": {"type": "integer"},
                        "fields": {"type": "object"},
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
                        "confirm": {"type": "string"},
                        "override_cleaned_check": {"type": "boolean"},
                    },
                    "required": ["record_id", "confirm"],
                },
            },
            {
                "name": "query_records",
                "description": "Search and filter records.",
                "input_schema": {
                    "type": "object",
                    "properties": {
                        "table": {"type": "string",
                                  "enum": ["raw_data", "cleaned_data", "audit_log"]},
                        "filters": {"type": "object"},
                        "limit": {"type": "integer"},
                    },
                    "required": ["table"],
                },
            },
        ]

    def send(self, user_input: str) -> str:
        self.messages.append({"role": "user", "content": user_input})
        while True:
            resp = self.clients.standard.messages_create(
                system=_SYSTEM_PROMPT, messages=self.messages, tools=self.tools,
            )
            if resp.stop_reason != "tool_use":
                text = next((b.text for b in resp.content if hasattr(b, "text")), "")
                self.messages.append({"role": "assistant", "content": resp.content})
                return text

            self.messages.append({"role": "assistant", "content": resp.content})
            tool_calls = [b for b in resp.content
                          if hasattr(b, "type") and b.type == "tool_use"]
            results = []
            for tc in tool_calls:
                result = self._execute_tool(tc.name, tc.input)
                results.append({"type": "tool_result",
                                "tool_use_id": tc.id, "content": result})
            self.messages.append({"role": "user", "content": results})

    def show_history(self) -> None:
        print(f"\n{'=' * 70}\nCONVERSATION HISTORY ({len(self.messages)} messages)\n{'=' * 70}")
        for i, m in enumerate(self.messages, 1):
            content_repr = m["content"] if isinstance(m["content"], str) else "<blocks>"
            preview = (content_repr[:80] + "...") if len(str(content_repr)) > 80 else content_repr
            print(f"{i}. {m['role'].upper()}: {preview}")

    def _execute_tool(self, name: str, args: dict) -> str:
        try:
            if name == "web_search":
                from cleaning.cache import WebSearchCache
                cache = WebSearchCache()
                return cache.web_search_cached(args.get("query", ""),
                                               args.get("max_results", 5))
            if name == "insert_record":
                return self._insert_record(args)
            if name == "update_record":
                return self._update_record(args)
            if name == "delete_record":
                return self._delete_record(args)
            if name == "query_records":
                return self._query_records(args)
        except Exception as e:
            return f"Tool error: {e}"
        return f"Unknown tool: {name}"

    def _insert_record(self, args: dict) -> str:
        try:
            check_age(args.get("age"))
            check_country(args.get("country"))
        except GuardrailError as e:
            return f"GUARDRAIL BLOCKED: {e}"
        rid = insert_raw_data(
            self.db_path, name=args["name"], age=args.get("age"),
            city=args.get("city"), address=args.get("address"),
            postal_code=args.get("postal_code"),
            municipality=args.get("municipality"),
            state_province=args.get("state_province"),
            country=args.get("country"), phone=args.get("phone"),
            imported_by="adhoc-conversation",
        )
        return f"Inserted record ID {rid}: {args['name']}"

    def _update_record(self, args: dict) -> str:
        table = args.get("table", "raw_data")
        record_id = args.get("record_id")
        fields = args.get("fields", {})
        try:
            check_no_wildcard_update(fields)
            check_protected_fields(fields, table)
            if "age" in fields:
                check_age(fields["age"])
            if "country" in fields:
                check_country(fields["country"])
        except GuardrailError as e:
            return f"GUARDRAIL BLOCKED: {e}"
        current = (get_raw_data_by_id(self.db_path, record_id) if table == "raw_data"
                   else (query_records(self.db_path, "cleaned_data", {"id": record_id}, 1)
                         or [None])[0])
        if not current:
            return f"Record ID {record_id} not found in {table}."
        eff_country = fields.get("country", current.get("country", ""))
        try:
            if eff_country in ("USA", "United States") and "state_province" in fields:
                check_usa_state(fields["state_province"])
            if eff_country in ("NL", "Netherlands") and "phone" in fields:
                check_nl_phone_format(fields["phone"])
        except GuardrailError as e:
            return f"GUARDRAIL BLOCKED: {e}"
        try:
            updated = (update_raw_data(self.db_path, record_id, fields)
                       if table == "raw_data"
                       else update_cleaned_data(self.db_path, record_id, fields))
        except ValueError as e:
            return f"GUARDRAIL BLOCKED: {e}"
        return (f"Updated {table} record ID {record_id}: {list(fields.keys())} changed."
                if updated else f"No record found with ID {record_id} in {table}.")

    def _delete_record(self, args: dict) -> str:
        record_id = args.get("record_id")
        try:
            check_delete_not_bulk(record_id)
            check_delete_confirmation(args.get("confirm", ""))
        except GuardrailError as e:
            return f"GUARDRAIL BLOCKED: {e}"
        cleaned_entries = get_cleaned_data_for_raw(self.db_path, record_id)
        if cleaned_entries and not args.get("override_cleaned_check", False):
            return (f"GUARDRAIL BLOCKED: Record ID {record_id} has "
                    f"{len(cleaned_entries)} cleaned_data entries. "
                    f"Set override_cleaned_check=true to force.")
        deleted = delete_raw_data(self.db_path, record_id)
        return (f"Deleted raw_data record ID {record_id}." if deleted
                else f"No record found with ID {record_id}.")

    def _query_records(self, args: dict) -> str:
        table = args.get("table", "raw_data")
        filters = args.get("filters") or {}
        limit = min(args.get("limit", 50), 50)
        try:
            records = query_records(self.db_path, table, filters, limit)
        except ValueError as e:
            return f"Query error: {e}"
        if not records:
            return f"No records found in {table}" + (f" with filters: {filters}" if filters else ".")
        lines = [f"Found {len(records)} record(s) in {table}:"]
        for r in records:
            lines.append(f"  {r}")
        return "\n".join(lines)
