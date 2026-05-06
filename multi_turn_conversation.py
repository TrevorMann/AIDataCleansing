import os
import re
import json
import time
import urllib.request
import urllib.parse
from anthropic import Anthropic
from database import init_db, get_db_connection
from db_helpers import (
    insert_raw_data, get_all_raw_data, insert_cleaned_data, insert_audit_log,
    update_raw_data, update_cleaned_data, delete_raw_data,
    get_cleaned_data_for_raw, query_records, get_raw_data_by_id,
)
from schema_discovery import format_schema_for_prompt
from data_cleaning_agent import DataCleaningAgent
from guardrails import (
    GuardrailError, check_age, check_country, check_protected_fields,
    check_no_wildcard_update, check_delete_confirmation, check_delete_not_bulk,
    check_usa_state, check_nl_phone_format,
)
from prompts import build_system_prompt
from skill_router import detect_skill, load_skill
from llm_client_factory import create_client, build_system_param, build_message_kwargs, log_usage, OPENROUTER, ANTHROPIC

# Read .env file without requiring python-dotenv
def load_env():
    """Load environment variables from .env file."""
    env_path = os.path.join(os.path.dirname(__file__), '.env')
    if os.path.exists(env_path):
        with open(env_path, 'r') as f:
            for line in f:
                line = line.strip()
                if line and not line.startswith('#') and '=' in line:
                    key, value = line.split('=', 1)
                    os.environ[key] = value

load_env()

# Initialize database
DB_PATH = os.getenv("DB_PATH", "data/cleaning.db")
os.makedirs(os.path.dirname(DB_PATH), exist_ok=True)
init_db(DB_PATH)
DB_SCHEMA = format_schema_for_prompt(DB_PATH)


def validate_na_phone(phone: str) -> bool:
    """Validate North American (US/Canada/Mexico) phone numbers.

    Accepts formats:
    - (123) 456-7890
    - 123-456-7890
    - 1231234567
    - +1-123-456-7890

    Returns True if valid, False otherwise.
    """
    if not phone or not isinstance(phone, str):
        return False

    # Remove common formatting characters
    cleaned = re.sub(r'[\s\-\(\)\.+]', '', phone)

    # Must be 10 digits (country code 1 is optional)
    if cleaned.startswith('1'):
        cleaned = cleaned[1:]

    # Check if exactly 10 digits and starts with valid area code
    if len(cleaned) != 10 or not cleaned.isdigit():
        return False

    # Area code cannot start with 0 or 1
    if cleaned[0] in ['0', '1']:
        return False

    return True


def validate_eu_phone(phone: str) -> bool:
    """Validate European phone numbers.

    Accepts formats:
    - +44 20 XXXX XXXX (UK)
    - +33 X XX XX XX XX (France)
    - +49 XXX XXXXXXX (Germany)
    - +39 XXX XXXXXX (Italy)
    - Etc. for other EU countries

    Returns True if valid, False otherwise.
    """
    if not phone or not isinstance(phone, str):
        return False

    # Remove common formatting characters
    cleaned = re.sub(r'[\s\-\(\)\.]', '', phone)

    # Must start with + and country code
    if not cleaned.startswith('+'):
        return False

    # Remove the +
    cleaned = cleaned[1:]

    # Must have 2-3 digit country code + at least 6 more digits
    if len(cleaned) < 8 or not cleaned.isdigit():
        return False

    # Valid EU country codes are typically 1-3 digits
    country_code = cleaned[:3]
    if len(country_code) < 2:
        return False

    return True




def format_na_phone(phone: str) -> str:
    """Format North American phone number to (123) 456-7890 format.

    Returns formatted phone or 'N/A' if invalid.
    """
    if not validate_na_phone(phone):
        return 'N/A'

    # Clean the phone number
    cleaned = re.sub(r'[\s\-\(\)\.+]', '', phone)
    if cleaned.startswith('1'):
        cleaned = cleaned[1:]

    # Format as (123) 456-7890
    return f"({cleaned[:3]}) {cleaned[3:6]}-{cleaned[6:10]}"


def web_search(query: str, max_results: int = 5) -> str:
    """Search the web using Tavily API. Returns formatted results."""
    api_key = os.getenv("TAVILY_API_KEY")
    if not api_key:
        return "Error: TAVILY_API_KEY not set in environment."

    try:
        payload = json.dumps({
            "api_key": api_key,
            "query": query,
            "max_results": max_results,
            "search_depth": "basic",
            "include_answer": True,
        }).encode("utf-8")

        req = urllib.request.Request(
            "https://api.tavily.com/search",
            data=payload,
            headers={"Content-Type": "application/json"},
            method="POST"
        )

        with urllib.request.urlopen(req, timeout=10) as resp:
            data = json.loads(resp.read().decode())

        parts = []
        if data.get("answer"):
            parts.append(f"Summary: {data['answer']}\n")

        for i, r in enumerate(data.get("results", [])[:max_results], 1):
            parts.append(
                f"{i}. {r.get('title', 'No title')}\n"
                f"   {r.get('content', '')[:300]}\n"
                f"   URL: {r.get('url', '')}"
            )

        return "\n".join(parts) if parts else f"No results found for: {query}"

    except Exception as e:
        return f"Web search failed: {e}. Query: {query}"


# ── LLM client — PATH A (OpenRouter) or PATH B (Anthropic) ───────────────────
# Set LLM_BACKEND=openrouter|anthropic in .env, or let it auto-detect from keys.
# See llm_client_factory.py for full documentation on each path.
_CLIENT, _BACKEND, _MODEL = create_client()

from prompts.domain_registry import get_active_domain, get_domain_config
_ACTIVE_DOMAIN = get_active_domain() or "real_estate"
_DOMAIN_CONFIG = get_domain_config(_ACTIVE_DOMAIN)
print(f"[LLM] backend={_BACKEND}  model={_MODEL}")
print(f"[domain] active={_ACTIVE_DOMAIN}  label={_DOMAIN_CONFIG.get('label', '')}")
print(f"[domain] sub_categories={_DOMAIN_CONFIG.get('sub_categories', [])}")

SYSTEM_PROMPT = build_system_prompt('CA', schema=DB_SCHEMA)


def detect_country_scope(user_query: str) -> str | None:
    """
    Return the canonical country code from a user query, or None if ambiguous/multi-country.
    Returns: 'CA', 'USA', 'NL', 'MX', 'JP', or None
    """
    query_lower = user_query.lower()

    if 'north american' in query_lower:
        return None

    country_keywords = {
        'CA': ['canadian', 'canada'],
        'USA': ['american', 'usa', 'united states', 'u.s.'],
        'NL': ['dutch', 'netherlands', 'holland', 'european', 'europe'],
        'MX': ['mexican', 'mexico'],
        'JP': ['japanese', 'japan'],
    }

    matched = [code for code, keywords in country_keywords.items()
               if any(kw in query_lower for kw in keywords)]

    return matched[0] if len(matched) == 1 else None


_SQLITE_TO_JSON_TYPE = {
    'TEXT': 'string',
    'INTEGER': 'integer',
    'REAL': 'number',
    'NUMERIC': 'number',
    'BLOB': 'string',
    'TIMESTAMP': 'string',
}

# Columns that are auto-managed and should never appear in insert/update tool schemas
_AUTO_MANAGED = {'id', 'imported_at', 'cleaned_at', 'imported_by', 'cleaned_by', 'applied_at', 'applied_by'}


def _build_table_properties(table_name: str, exclude: set = None) -> dict:
    """
    Build a JSON Schema properties dict from the live DB schema + column_metadata descriptions.
    Adding a column to the table or updating its description in column_metadata is enough —
    no Python changes needed.
    """
    from schema_discovery import get_table_schema, get_column_metadata
    exclude = (exclude or set()) | _AUTO_MANAGED
    columns = get_table_schema(DB_PATH, table_name)
    descriptions = get_column_metadata(DB_PATH, table_name)
    props = {}
    for col in columns:
        if col['name'] in exclude:
            continue
        sqlite_type = col['type'].upper().split('(')[0].strip()
        json_type = _SQLITE_TO_JSON_TYPE.get(sqlite_type, 'string')
        entry = {'type': json_type}
        if col['name'] in descriptions:
            entry['description'] = descriptions[col['name']]
        props[col['name']] = entry
    return props


def _column_names(table_name: str, exclude: set = None) -> list[str]:
    """Return editable column names for a table (excluding auto-managed cols)."""
    from schema_discovery import get_table_schema
    exclude = (exclude or set()) | _AUTO_MANAGED
    return [c['name'] for c in get_table_schema(DB_PATH, table_name) if c['name'] not in exclude]


class DataCleaningConversation:
    """Helper class for managing multi-turn conversations with hybrid approach."""

    def __init__(self, system_prompt: str):
        self.system_prompt = system_prompt
        self.messages = []
        self.turn_count = 0

    def define_tools(self) -> list:
        """Define tools for Claude to call. Schemas for CRUD tools are built from the live DB schema."""
        return [
            {
                "name": "validate_na_phone",
                "description": "Validate if a phone number is North American (US/Canada/Mexico) format. Returns true/false.",
                "input_schema": {
                    "type": "object",
                    "properties": {
                        "phone": {
                            "type": "string",
                            "description": "The phone number to validate (e.g., '416-555-0123' or '(416) 555-0123')"
                        }
                    },
                    "required": ["phone"]
                }
            },
            {
                "name": "validate_eu_phone",
                "description": "Validate if a phone number is European format. Returns true/false.",
                "input_schema": {
                    "type": "object",
                    "properties": {
                        "phone": {
                            "type": "string",
                            "description": "The phone number to validate (e.g., '+44 20 7123 4567')"
                        }
                    },
                    "required": ["phone"]
                }
            },
            {
                "name": "format_na_phone",
                "description": "Format a North American phone number to (123) 456-7890 format. Returns formatted number or 'N/A' if invalid.",
                "input_schema": {
                    "type": "object",
                    "properties": {
                        "phone": {
                            "type": "string",
                            "description": "The phone number to format"
                        }
                    },
                    "required": ["phone"]
                }
            },
            {
                "name": "web_search",
                "description": "Search the web to verify addresses, postal codes, and municipalities. Use this to confirm that a postal code matches an address/city, find the real estate municipality for a postal code or FSA, or resolve ambiguous addresses.",
                "input_schema": {
                    "type": "object",
                    "properties": {
                        "query": {
                            "type": "string",
                            "description": "Search query, e.g. 'M6H 1E7 Toronto Municipality real estate', '25 Muir Avenue M6H Toronto postal code', 'V6B 2W9 Vancouver municipality'"
                        },
                        "max_results": {
                            "type": "integer",
                            "description": "Max results to return (default 5)",
                            "default": 5
                        }
                    },
                    "required": ["query"]
                }
            },
            {
                "name": "insert_record",
                "description": "Insert a new record into the raw_data table.",
                "input_schema": {
                    "type": "object",
                    "properties": _build_table_properties('raw_data'),
                    "required": ["name"]
                }
            },
            {
                "name": "update_record",
                "description": (
                    "Update specific fields on a raw_data or cleaned_data record by ID. "
                    "Only specify the fields you want to change. "
                    f"raw_data editable fields: {_column_names('raw_data')}. "
                    f"cleaned_data editable fields: {_column_names('cleaned_data', exclude={'raw_data_id'})}."
                ),
                "input_schema": {
                    "type": "object",
                    "properties": {
                        "table": {"type": "string", "enum": ["raw_data", "cleaned_data"], "description": "Which table to update"},
                        "record_id": {"type": "integer", "description": "The ID of the record to update"},
                        "fields": {"type": "object", "description": "Key-value pairs of fields to update"}
                    },
                    "required": ["table", "record_id", "fields"]
                }
            },
            {
                "name": "delete_record",
                "description": "Delete a raw_data record by ID. Will fail if the record has been cleaned unless override is set. Requires confirm='yes'.",
                "input_schema": {
                    "type": "object",
                    "properties": {
                        "record_id": {"type": "integer", "description": "ID of the raw_data record to delete"},
                        "confirm": {"type": "string", "description": "Must be exactly 'yes' to proceed"},
                        "override_cleaned_check": {"type": "boolean", "description": "Set true to allow deleting records that have cleaned_data entries. Default false."}
                    },
                    "required": ["record_id", "confirm"]
                }
            },
            {
                "name": "query_records",
                "description": (
                    "Search and filter records in the database. Returns up to 50 records. "
                    f"raw_data columns: {_column_names('raw_data')}. "
                    f"cleaned_data columns: {_column_names('cleaned_data')}."
                ),
                "input_schema": {
                    "type": "object",
                    "properties": {
                        "table": {"type": "string", "enum": ["raw_data", "cleaned_data", "audit_log"], "description": "Table to query"},
                        "filters": {"type": "object", "description": "Optional key-value pairs to filter by. Example: {\"country\": \"CA\"}"},
                        "limit": {"type": "integer", "description": "Max records to return (default 50, max 50)"}
                    },
                    "required": ["table"]
                }
            },
        ]

    def execute_tool(self, tool_name: str, tool_input: dict) -> str:
        """Execute a tool function and return the result as a string."""
        if tool_name == "validate_na_phone":
            result = validate_na_phone(tool_input["phone"])
            return f"Valid North American phone: {result}"
        elif tool_name == "validate_eu_phone":
            result = validate_eu_phone(tool_input["phone"])
            return f"Valid European phone: {result}"
        elif tool_name == "format_na_phone":
            result = format_na_phone(tool_input["phone"])
            return f"Formatted NA phone: {result}"
        elif tool_name == "web_search":
            query = tool_input.get("query", "")
            max_results = tool_input.get("max_results", 5)
            result = web_search(query, max_results)
            print(f"     🔍 Search: {query}")
            print(f"     📄 Result preview: {result[:200]}...")
            return result
        elif tool_name == "insert_record":
            return self._execute_insert_record(tool_input)
        elif tool_name == "update_record":
            return self._execute_update_record(tool_input)
        elif tool_name == "delete_record":
            return self._execute_delete_record(tool_input)
        elif tool_name == "query_records":
            return self._execute_query_records(tool_input)
        else:
            return f"Unknown tool: {tool_name}"

    def _execute_insert_record(self, tool_input: dict) -> str:
        try:
            check_age(tool_input.get('age'))
            check_country(tool_input.get('country'))
        except GuardrailError as e:
            return f"GUARDRAIL BLOCKED: {e}"

        row_id = insert_raw_data(
            DB_PATH,
            name=tool_input['name'],
            age=tool_input.get('age'),
            city=tool_input.get('city'),
            address=tool_input.get('address'),
            postal_code=tool_input.get('postal_code'),
            municipality=tool_input.get('municipality'),
            state_province=tool_input.get('state_province'),
            country=tool_input.get('country'),
            phone=tool_input.get('phone'),
            imported_by='claude-assistant',
        )
        return f"Inserted record ID {row_id}: {tool_input['name']}"

    def _execute_update_record(self, tool_input: dict) -> str:
        table = tool_input.get('table', 'raw_data')
        record_id = tool_input.get('record_id')
        fields = tool_input.get('fields', {})

        try:
            check_no_wildcard_update(fields)
            check_protected_fields(fields, table)
            if 'age' in fields:
                check_age(fields['age'])
            if 'country' in fields:
                check_country(fields['country'])
        except GuardrailError as e:
            return f"GUARDRAIL BLOCKED: {e}"

        # Fetch current record to apply country-specific rules
        if table == 'raw_data':
            current = get_raw_data_by_id(DB_PATH, record_id)
        else:
            results = query_records(DB_PATH, 'cleaned_data', {'id': record_id}, limit=1)
            current = results[0] if results else None

        if not current:
            return f"Record ID {record_id} not found in {table}."

        effective_country = fields.get('country', current.get('country', ''))

        try:
            if effective_country in ('USA', 'United States') and 'state_province' in fields:
                check_usa_state(fields['state_province'])
            if effective_country in ('NL', 'Netherlands') and 'phone' in fields:
                check_nl_phone_format(fields['phone'])
        except GuardrailError as e:
            return f"GUARDRAIL BLOCKED: {e}"

        canada_warning = ""
        if effective_country in ('CA', 'Canada') and 'postal_code' in fields:
            canada_warning = " WARNING: Canada postal_code changed — ensure web_search was used to verify first."

        try:
            if table == 'raw_data':
                updated = update_raw_data(DB_PATH, record_id, fields)
            else:
                updated = update_cleaned_data(DB_PATH, record_id, fields)
        except ValueError as e:
            return f"GUARDRAIL BLOCKED: {e}"

        if updated:
            return f"Updated {table} record ID {record_id}: fields {list(fields.keys())} changed.{canada_warning}"
        return f"No record found with ID {record_id} in {table}."

    def _execute_delete_record(self, tool_input: dict) -> str:
        record_id = tool_input.get('record_id')
        confirm = tool_input.get('confirm', '')
        override = tool_input.get('override_cleaned_check', False)

        try:
            check_delete_not_bulk(record_id)
            check_delete_confirmation(confirm)
        except GuardrailError as e:
            return f"GUARDRAIL BLOCKED: {e}"

        cleaned_entries = get_cleaned_data_for_raw(DB_PATH, record_id)
        if cleaned_entries and not override:
            return (
                f"GUARDRAIL BLOCKED: Record ID {record_id} has {len(cleaned_entries)} cleaned_data "
                f"entries. Set override_cleaned_check=true to force deletion."
            )

        deleted = delete_raw_data(DB_PATH, record_id)
        if deleted:
            return f"Deleted raw_data record ID {record_id}."
        return f"No record found with ID {record_id} in raw_data."

    def _execute_query_records(self, tool_input: dict) -> str:
        table = tool_input.get('table', 'raw_data')
        filters = tool_input.get('filters') or {}
        limit = min(tool_input.get('limit', 50), 50)

        try:
            records = query_records(DB_PATH, table, filters, limit)
        except ValueError as e:
            return f"Query error: {e}"

        if not records:
            return f"No records found in {table}" + (f" with filters: {filters}" if filters else ".")

        lines = [f"Found {len(records)} record(s) in {table}:"]
        for r in records:
            lines.append(f"  {r}")
        return "\n".join(lines)

    def handle_canada_cleaning(self, user_query: str) -> str:
        original = self.system_prompt
        self.system_prompt = build_system_prompt('CA', schema=DB_SCHEMA)
        try:
            return self._run_cleaning_workflow(user_query, country_scope='CA')
        finally:
            self.system_prompt = original

    def handle_usa_cleaning(self, user_query: str) -> str:
        original = self.system_prompt
        self.system_prompt = build_system_prompt('USA', schema=DB_SCHEMA)
        try:
            return self._run_cleaning_workflow(user_query, country_scope='USA')
        finally:
            self.system_prompt = original

    def handle_europe_cleaning(self, user_query: str) -> str:
        original = self.system_prompt
        self.system_prompt = build_system_prompt('NL', schema=DB_SCHEMA)
        try:
            return self._run_cleaning_workflow(user_query, country_scope='NL')
        finally:
            self.system_prompt = original

    def handle_mexico_cleaning(self, user_query: str) -> str:
        original = self.system_prompt
        self.system_prompt = build_system_prompt('MX', schema=DB_SCHEMA)
        try:
            return self._run_cleaning_workflow(user_query, country_scope='MX')
        finally:
            self.system_prompt = original

    def handle_japan_cleaning(self, user_query: str) -> str:
        original = self.system_prompt
        self.system_prompt = build_system_prompt('JP', schema=DB_SCHEMA)
        try:
            return self._run_cleaning_workflow(user_query, country_scope='JP')
        finally:
            self.system_prompt = original

    def preprocess_data(self, user_input: str) -> str:
        """Pre-process data before sending to Claude. Extract and clean what we can."""
        # This is where we'd extract phone numbers and validate them locally
        # For now, we'll keep it simple and just return the input
        # You could add regex to extract phones and pre-validate them
        return user_input

    def send_message(self, user_input: str) -> str:
        """
        Send a message with hybrid approach:
        1. Pre-process locally where possible
        2. Detect active skill and inject its instructions into the system prompt
        3. Let the model make decisions (tool calls loop until done)

        PATH A (OpenRouter): system prompt is a plain string.
        PATH B (Anthropic):  system prompt is a list of typed blocks with cache_control.

        Logs usage metrics for cost tracking and cache hit analysis.
        """
        # Step 1: Pre-process the input
        preprocessed = self.preprocess_data(user_input)

        # Step 2: Skill detection — inject matching SKILL.md into system prompt
        skill_name = detect_skill(preprocessed)
        if skill_name:
            print(f"  [skill] activated: {skill_name}")
            skill_content = load_skill(skill_name)
        else:
            skill_content = ""

        # Step 3: Build backend-specific system param
        # PATH A (openrouter): plain string  |  PATH B (anthropic): list of blocks
        system_param = build_system_param(_BACKEND, self.system_prompt, skill_content)

        # Step 4: Add user message
        self.messages.append({
            "role": "user",
            "content": preprocessed
        })

        # Step 5: Build backend-specific message kwargs (budget_tokens for Anthropic, etc)
        message_kwargs = build_message_kwargs(_BACKEND)

        # Step 6: Loop until model stops calling tools
        while True:
            response = _CLIENT.messages.create(
                model=_MODEL,
                max_tokens=2048,
                system=system_param,
                messages=self.messages,
                tools=self.define_tools(),
                **message_kwargs
            )
            # Log usage for cost tracking
            log_usage(_BACKEND, response.usage)


            
            # Check if Claude called any tools

            # If no tool calls, Claude is done - return the response
            if response.stop_reason != "tool_use":
                #if not tool_calls:
                # Extract text response
                text_response = next(
                    (block.text for block in response.content if hasattr(block, 'text')),
                    "No response"
                )

                self.messages.append({
                    "role": "assistant",
                    "content": response.content
                })
                return text_response

            # Step 4: Handle tool calls
            # Add Claude's response (which includes tool_use blocks)
            self.messages.append({
                "role": "assistant",
                "content": response.content
            })

            tool_calls = [block for block in response.content if hasattr(block, 'type') and block.type == "tool_use"]

            # Execute each tool and collect results
            tool_results = []
            for tool_use in tool_calls:
                try:
                    tool_result = self.execute_tool(tool_use.name, tool_use.input)
                    tool_results.append({
                        "type": "tool_result",
                        "tool_use_id": tool_use.id,
                        "content": tool_result,
                        "status": "success"  # You could add error handling to set this to "error" if something goes wrong
                    })
                    print(f"  🔧 Tool called: {tool_use.name}")
                    print(f"     Input: {tool_use.input}")
                    print(f"     Result: {tool_result}")
                except Exception as e:
                    error_message = f"Error executing tool {tool_use.name}: {e}"
                    tool_results.append({
                        "type": "tool_result",
                        "tool_use_id": tool_use.id,
                        "content": error_message,
                        "status": "error"
                    })
                    print(f"  ❌ {error_message}")

            # Send tool results back to Claude
            self.messages.append({
                "role": "user",
                "content": tool_results
            })
            # Loop continues - Claude will process results and either call more tools or finish

    def display_message(self, role: str, content: str, turn: int = None):
        """Display a message nicely."""
        if turn:
            print(f"\n{'=' * 70}")
            print(f"TURN {turn}")
            print(f"{'=' * 70}")
        print(f"\n{role.upper()}:")
        print(content)

    def get_multiline_input(self, prompt: str = "Your message (type 'END' on a new line to finish):\n") -> str:
        """Get multi-line input from user."""
        print(f"\n{prompt}")
        lines = []
        while True:
            line = input()
            if line.strip().upper() == "END":
                break
            lines.append(line)
        return "\n".join(lines)

    def show_conversation_history(self):
        """Display the full conversation history."""
        print(f"\n{'=' * 70}")
        print("CONVERSATION HISTORY")
        print(f"{'=' * 70}")
        print(f"Total exchanges: {len(self.messages)}")
        print(f"Total turns: {self.turn_count}\n")
        for i, msg in enumerate(self.messages, 1):
            role = msg["role"].upper()
            content = msg["content"][:80] + "..." if len(msg["content"]) > 80 else msg["content"]
            print(f"{i}. {role}: {content}")

    def validate_phone(self, phone: str) -> dict:
        """Validate a phone number and return results."""
        na_valid = validate_na_phone(phone)
        eu_valid = validate_eu_phone(phone)

        result = {
            "phone": phone,
            "na_valid": na_valid,
            "eu_valid": eu_valid,
            "na_formatted": format_na_phone(phone) if na_valid else "N/A",
        }

        if na_valid:
            result["type"] = "North American"
            result["formatted"] = format_na_phone(phone)
        elif eu_valid:
            result["type"] = "European"
            result["formatted"] = phone  # Already in +XX format
        else:
            result["type"] = "Invalid"
            result["formatted"] = "N/A"

        return result

    def get_raw_data_for_cleaning(self) -> list:
        """Fetch uncleaned raw data from database."""
        return get_all_raw_data(DB_PATH)

    def save_cleaned_record(self, raw_data_id: int, cleaned_data: dict, validation_notes: str, user: str = "claude-assistant"):
        """Save cleaned data and create audit log entries."""
        cleaned_id = insert_cleaned_data(
            DB_PATH,
            raw_data_id=raw_data_id,
            name=cleaned_data.get('name'),
            age=cleaned_data.get('age'),
            city=cleaned_data.get('city'),
            address=cleaned_data.get('address'),
            postal_code=cleaned_data.get('postal_code'),
            municipality=cleaned_data.get('municipality'),
            state_province=cleaned_data.get('state_province'),
            country=cleaned_data.get('country'),
            phone=cleaned_data.get('phone'),
            validation_notes=validation_notes,
            cleaned_by=user
        )

        # Log transformations if needed
        if cleaned_data.get('transformations'):
            for transformation in cleaned_data['transformations']:
                insert_audit_log(
                    DB_PATH,
                    raw_data_id=raw_data_id,
                    cleaned_data_id=cleaned_id,
                    rule_applied=transformation.get('rule'),
                    description=transformation.get('description'),
                    applied_by=user
                )

        return cleaned_id

    def handle_cleaning_request(self, user_query: str) -> str:
        """Route cleaning request to the appropriate country-specific handler."""
        country = detect_country_scope(user_query)
        route_map = {
            'CA': self.handle_canada_cleaning,
            'USA': self.handle_usa_cleaning,
            'NL': self.handle_europe_cleaning,
            'MX': self.handle_mexico_cleaning,
            'JP': self.handle_japan_cleaning,
        }
        if country and country in route_map:
            print(f"  Routing to {country} handler...")
            return route_map[country](user_query)
        print("  No specific country detected — using generic workflow...")
        return self._run_cleaning_workflow(user_query)

    def _run_cleaning_workflow(self, user_query: str, country_scope: str = None) -> str:
        """
        Two-phase pipeline:
          Phase 1 — Python pre-cleaner handles everything deterministic
                    (casing, country/state expansion, phone formatting, postal spacing)
          Phase 2 — Claude is called only for records that still need
                    postal verification or municipality lookup via web search
        """
        from prompts.research import build_research_prompt
        workflow_start = time.time()
        agent = DataCleaningAgent(DB_PATH)

        # Step 1: Interpret query
        print(f"\n{'='*80}")
        print("STEP 1: INTERPRETING QUERY")
        print(f"{'='*80}")
        t = time.time()
        filters = agent.interpret_user_query(user_query)
        step1_elapsed = time.time() - t
        print(f"  Query: '{user_query}'  →  filters: {filters}")
        print(f"  ⏱️  {step1_elapsed:.2f}s\n")

        # Step 2: Fetch records
        print(f"{'='*80}")
        print("STEP 2: FETCHING RECORDS")
        print(f"{'='*80}")
        t = time.time()
        records = agent.fetch_data_for_query(filters)
        step2_elapsed = time.time() - t
        if not records:
            return "❌ No records found matching your query."
        print(f"  ✅ {len(records)} records  ⏱️  {step2_elapsed:.2f}s\n")

        # Step 3: Deterministic pre-cleaning (Python only, no API call)
        print(f"{'='*80}")
        print("STEP 3: PYTHON PRE-CLEANING (deterministic)")
        print(f"{'='*80}")
        t = time.time()
        pre_cleaned, needs_research = agent.pre_clean_batch(records)
        step3_elapsed = time.time() - t
        python_only = len(pre_cleaned) - len(needs_research)
        print(f"  ✅ {python_only} records fully cleaned by Python")
        print(f"  🔍 {len(needs_research)} records need Claude (postal/municipality research)")
        for r in pre_cleaned:
            changes = r.get('_pre_clean_changes', [])
            if changes:
                print(f"    ID {r['id']}: {'; '.join(changes)}")
        print(f"  ⏱️  {step3_elapsed:.2f}s\n")

        # Step 4 & 5: Claude research (only if needed)
        step4_elapsed = 0.0
        step5_elapsed = 0.0
        research_results = {}

        if needs_research:
            # Step 4: Format compact research table
            print(f"{'='*80}")
            print("STEP 4: FORMATTING RESEARCH REQUEST FOR CLAUDE")
            print(f"{'='*80}")
            t = time.time()
            research_table = agent.format_research_batch(needs_research)
            research_prompt = build_research_prompt(country_scope or 'CA', research_table)
            step4_elapsed = time.time() - t
            print(f"  Sending {len(needs_research)}/{len(records)} records to Claude")
            print(f"  Prompt size: {len(research_prompt):,} chars  ⏱️  {step4_elapsed:.2f}s\n")

            # Step 5: Claude does postal/municipality research
            print(f"{'='*80}")
            print("STEP 5: CLAUDE RESEARCH (postal + municipality only)")
            print(f"{'='*80}")
            t = time.time()

            # Use a temporary message list so research doesn't pollute conversation history
            research_messages = [{"role": "user", "content": research_prompt}]
            claude_response = "No response"
            search_count = 0
            tool_round = 0
            max_rounds = 20

            # Focused system prompt: base + domain/country rules only (no DB schema = shorter prompt)
            research_system = build_system_prompt(sub=country_scope or 'CA', schema='')

            # PATH A (openrouter): system is plain string, no caching
            # PATH B (anthropic):  system is list of blocks; base prompt cached via cache_control
            research_system_param = build_system_param(_BACKEND, research_system)

            # Build backend-specific message kwargs
            research_message_kwargs = build_message_kwargs(_BACKEND)

            while tool_round < max_rounds:
                tool_round += 1
                response = _CLIENT.messages.create(
                    model=_MODEL,
                    max_tokens=2048,
                    system=research_system_param,
                    messages=research_messages,
                    tools=self.define_tools(),
                    **research_message_kwargs
                )
                log_usage(_BACKEND, response.usage)
                tool_calls = [b for b in response.content if hasattr(b, 'type') and b.type == "tool_use"]

                print(f"  Round {tool_round}: {len(tool_calls)} tool calls, stop={response.stop_reason}")
                for block in response.content:
                    if block.type == "tool_use" and block.name == "web_search":
                        search_count += 1
                        print(f"    🔍 Search #{search_count}: {getattr(block, 'input', {}).get('query', '')}")

                if not tool_calls:
                    claude_response = next(
                        (b.text for b in response.content if hasattr(b, 'text')), "No response"
                    )
                    break

                research_messages.append({"role": "assistant", "content": response.content})
                tool_results = []
                for tool_use in tool_calls:
                    result = self.execute_tool(tool_use.name, getattr(tool_use, 'input', {}))
                    tool_results.append({"type": "tool_result", "tool_use_id": tool_use.id, "content": result})
                research_messages.append({"role": "user", "content": tool_results})

            if claude_response == "No response":
                print("  ⚠️  Round cap hit — forcing final output...")
                final = _CLIENT.messages.create(
                    model=_MODEL,
                    max_tokens=2048,
                    system=research_system_param,
                    messages=research_messages + [{"role": "user", "content":
                        "Research complete. Now return ONLY the table: | ID | Postal Code | Municipality | Validation Notes |"}],
                    **research_message_kwargs
                )
                log_usage(_BACKEND, final.usage)
                claude_response = next(
                    (b.text for b in final.content if hasattr(b, 'text')), "No response"
                )

            step5_elapsed = time.time() - t
            print(f"  🔍 {search_count} searches, {tool_round} rounds  ⏱️  {step5_elapsed:.2f}s\n")

            # Step 6: Parse Claude's 4-column response
            print(f"{'='*80}")
            print("STEP 6: PARSING RESEARCH RESULTS")
            print(f"{'='*80}")
            research_results = agent.parse_research_response(claude_response)
            print(f"  ✅ Parsed results for {len(research_results)} records")
            if not research_results:
                print(f"  ⚠️  Parse returned 0 results. Raw response:\n{claude_response[:400]}")

        # Step 7: Merge pre-cleaned + research → cleaned_results
        print(f"{'='*80}")
        print("STEP 7: MERGING AND SAVING")
        print(f"{'='*80}")
        t = time.time()
        agent.merge_results(pre_cleaned, research_results)
        batch_index = {r['id']: r for r in records}
        for r in agent.cleaned_results:
            orig = batch_index.get(r['raw_data_id'], {})
            src = 'Python+Claude' if r['raw_data_id'] in research_results else 'Python only'
            print(f"  ID {r['raw_data_id']} ({src}): "
                  f"postal {orig.get('postal_code')} → {r.get('postal_code')}  "
                  f"municipality → {r.get('municipality')}")
        saved_count = agent.save_cleaned_results()
        step7_elapsed = time.time() - t
        print(f"\n  ✅ Saved {saved_count} records  ⏱️  {step7_elapsed:.2f}s\n")

        # Timing summary
        total_elapsed = time.time() - workflow_start
        print(f"{'='*80}")
        print("⏱️  TIMING SUMMARY")
        print(f"{'='*80}")
        print(f"  Step 1 Interpret query:      {step1_elapsed:6.2f}s")
        print(f"  Step 2 Fetch records:        {step2_elapsed:6.2f}s")
        print(f"  Step 3 Python pre-clean:     {step3_elapsed:6.2f}s  ({python_only} records done, no API)")
        if needs_research:
            print(f"  Step 4 Format research:      {step4_elapsed:6.2f}s")
            print(f"  Step 5 Claude research:      {step5_elapsed:6.2f}s  ({len(needs_research)} records, {search_count} searches) ⭐")
        print(f"  Step 7 Merge + save:         {step7_elapsed:6.2f}s")
        print(f"  {'─'*40}")
        print(f"  TOTAL                        {total_elapsed:6.2f}s  ({total_elapsed/len(records):.3f}s/record)")
        print(f"{'='*80}\n")

        report = agent.generate_report()
        return report + f"\n✅ {saved_count} records saved ({python_only} Python-only, {len(research_results)} with Claude research).\n"

    def run_interactive(self):
        """Run interactive conversation mode with hybrid approach."""
        print(f"{'=' * 70}")
        print("DATA CLEANING CONVERSATION (HYBRID: Pre-Process + Tool Use + DB Integration)")
        print(f"{'=' * 70}")
        print("System: Data cleaning expert for real estate")
        print("\nWorkflow:")
        print("  1️⃣  Ask a question or start a cleaning workflow")
        print("  2️⃣  Claude validates & cleans data")
        print("  3️⃣  Results automatically saved to database\n")
        print("Commands:")
        print("  • CLEAN [query] - Start automated cleaning workflow")
        print("    Examples: 'CLEAN Canadian data', 'CLEAN North American data'")
        print("  • HISTORY - View conversation history")
        print("  • QUIT - Exit\n")

        while True:
            self.turn_count += 1
            user_input = self.get_multiline_input(
                f"Turn {self.turn_count} - Your message (type 'END' on new line to submit):"
            )

            if user_input.strip().upper() == "QUIT":
                print("\nGoodbye!")
                break
            elif user_input.strip().upper() == "HISTORY":
                self.show_conversation_history()
                self.turn_count -= 1
                continue
            elif user_input.strip().upper().startswith("CLEAN"):
                # Extract the cleaning query (e.g., "CLEAN Canadian data" -> "Canadian data")
                clean_query = user_input[5:].strip()
                if not clean_query:
                    print("\n📋 Available cleaning queries:")
                    print("  CLEAN Canadian data")
                    print("  CLEAN US data")
                    print("  CLEAN Mexican data")
                    print("  CLEAN North American data")
                    print("  CLEAN all uncleaned data")
                    self.turn_count -= 1
                    continue

                print(f"\n{'='*70}")
                print(f"CLEANING WORKFLOW: {clean_query}")
                print(f"{'='*70}")

                result = self.handle_cleaning_request(clean_query)
                print(result)
                continue
            elif user_input.strip().upper() == "LOAD_FROM_DB":
                raw_records = self.get_raw_data_for_cleaning()
                if raw_records:
                    print(f"\nFound {len(raw_records)} raw records in database:")
                    for i, record in enumerate(raw_records, 1):
                        print(f"  {i}. ID {record['id']}: {record['name']} ({record['city']})")
                else:
                    print("\nNo raw data in database. Use INSERT command to add data.")
                self.turn_count -= 1
                continue
            elif not user_input.strip():
                print("Please enter a message.")
                self.turn_count -= 1
                continue

            print(f"\n[Processing with hybrid approach...]")
            response = self.send_message(user_input)
            self.display_message("assistant", response)


def test_phone_validation():
    """Test the phone validation functions."""
    print(f"\n{'=' * 70}")
    print("PHONE VALIDATION TEST")
    print(f"{'=' * 70}\n")

    test_numbers = [
        # North American
        "(416) 555-0123",
        "416-555-0123",
        "4165550123",
        "+1-416-555-0123",
        # European
        "+44 20 7123 4567",
        "+33 1 23 45 67 89",
        "+49 30 12345678",
        # Invalid
        "123456",
        "invalid",
        "555-1234",
    ]

    conversation = DataCleaningConversation(system_prompt=SYSTEM_PROMPT)

    for phone in test_numbers:
        result = conversation.validate_phone(phone)
        print(f"Phone: {result['phone']}")
        print(f"  Type: {result['type']}")
        print(f"  Formatted: {result['formatted']}")
        print()


if __name__ == "__main__":
    import sys

    if len(sys.argv) > 1 and sys.argv[1] == "test":
        test_phone_validation()
    else:
        conversation = DataCleaningConversation(system_prompt=SYSTEM_PROMPT)
        conversation.run_interactive()
