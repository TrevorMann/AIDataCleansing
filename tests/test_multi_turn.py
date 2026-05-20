"""Tests for multi_turn_conversation.py — tool dispatch and guardrail enforcement.

multi_turn_conversation.py has module-level side effects (DB init, LLM client setup)
so we patch those before import via sys.modules and explicit patch.start() calls.
All DB I/O and LLM calls are mocked — no real DB or API keys needed.
"""
import sys
import importlib
from unittest.mock import MagicMock, patch

# ── stub missing packages at module level so imports don't fail ─────────────
if 'anthropic' not in sys.modules:
    _stub = MagicMock()
    _stub.Anthropic = MagicMock
    _stub.RateLimitError = Exception
    sys.modules['anthropic'] = _stub

# ── patch module-level side effects before multi_turn_conversation is imported
_module_patches = [
    patch('db.sqlite_init.init_db'),
    patch('db.schema_discovery.format_schema_for_prompt', return_value='<SCHEMA/>'),
    patch('llm_client_factory.create_client', return_value=(MagicMock(), 'anthropic', 'claude-test')),
    patch('prompts.domain_registry.get_active_domain', return_value='real_estate'),
    patch('prompts.domain_registry.get_domain_config', return_value={'label': 'Real Estate', 'sub_categories': []}),
    patch('prompts.build_system_prompt', return_value='system prompt'),
]
for _p in _module_patches:
    _p.start()

import multi_turn_conversation  # noqa: E402 — must come after patches
from multi_turn_conversation import DataCleaningConversation  # noqa: E402

for _p in _module_patches:
    _p.stop()

import pytest


# ── fixtures ───────────────────────────────────────────────────────────────────

@pytest.fixture
def conv():
    return DataCleaningConversation(system_prompt="test system prompt")


# ── phone validation tools ─────────────────────────────────────────────────────

class TestPhoneValidationTools:
    def test_validate_na_phone_valid(self, conv):
        result = conv.execute_tool("validate_na_phone", {"phone": "(416) 555-0123"})
        assert "True" in result

    def test_validate_na_phone_invalid(self, conv):
        result = conv.execute_tool("validate_na_phone", {"phone": "not-a-phone"})
        assert "False" in result

    def test_validate_eu_phone_valid(self, conv):
        result = conv.execute_tool("validate_eu_phone", {"phone": "+44 20 1234 5678"})
        assert "True" in result

    def test_validate_eu_phone_invalid(self, conv):
        result = conv.execute_tool("validate_eu_phone", {"phone": "416-555-0123"})
        assert "False" in result

    def test_format_na_phone_valid(self, conv):
        result = conv.execute_tool("format_na_phone", {"phone": "4165550123"})
        assert "(416) 555-0123" in result

    def test_format_na_phone_invalid_returns_na(self, conv):
        result = conv.execute_tool("format_na_phone", {"phone": "bad"})
        assert "N/A" in result


# ── web_search tool ────────────────────────────────────────────────────────────

class TestWebSearchTool:
    def test_web_search_returns_result(self, conv):
        with patch.object(multi_turn_conversation, 'web_search', return_value="search results"):
            result = conv.execute_tool("web_search", {"query": "Toronto postal code"})
        assert "search results" in result

    def test_web_search_passes_max_results(self, conv):
        with patch.object(multi_turn_conversation, 'web_search', return_value="r") as mock_ws:
            conv.execute_tool("web_search", {"query": "q", "max_results": 3})
        mock_ws.assert_called_once_with("q", 3)


# ── insert_record tool + guardrails ───────────────────────────────────────────

class TestInsertRecordTool:
    def test_insert_valid_record(self, conv):
        with patch.object(multi_turn_conversation, 'insert_raw_data', return_value=42):
            result = conv.execute_tool("insert_record", {"name": "Alice", "country": "CA"})
        assert "42" in result

    def test_insert_blocks_invalid_age(self, conv):
        result = conv.execute_tool("insert_record", {"name": "Bob", "age": 200})
        assert "GUARDRAIL BLOCKED" in result

    def test_insert_blocks_unrecognizable_country(self, conv):
        result = conv.execute_tool("insert_record", {"name": "Bob", "country": "XYZNOTACOUNTRY999"})
        assert "GUARDRAIL BLOCKED" in result

    def test_insert_passes_full_country_name(self, conv):
        with patch.object(multi_turn_conversation, 'insert_raw_data', return_value=1):
            result = conv.execute_tool("insert_record", {"name": "Alice", "country": "Canada"})
        assert "GUARDRAIL BLOCKED" not in result

    def test_insert_blocks_zero_age(self, conv):
        result = conv.execute_tool("insert_record", {"name": "Bob", "age": 0})
        assert "GUARDRAIL BLOCKED" in result


# ── update_record tool + guardrails ──────────────────────────────────────────

class TestUpdateRecordTool:
    def test_update_valid_fields(self, conv):
        with (
            patch.object(multi_turn_conversation, 'get_raw_data_by_id',
                         return_value={"id": 1, "name": "Alice", "country": "CA"}),
            patch.object(multi_turn_conversation, 'update_raw_data', return_value=True),
        ):
            result = conv.execute_tool("update_record", {
                "table": "raw_data", "record_id": 1, "fields": {"city": "Toronto"}
            })
        assert "GUARDRAIL BLOCKED" not in result

    def test_update_blocks_protected_field(self, conv):
        result = conv.execute_tool("update_record", {
            "table": "raw_data", "record_id": 1, "fields": {"id": 99}
        })
        assert "GUARDRAIL BLOCKED" in result

    def test_update_blocks_empty_fields(self, conv):
        result = conv.execute_tool("update_record", {
            "table": "raw_data", "record_id": 1, "fields": {}
        })
        assert "GUARDRAIL BLOCKED" in result

    def test_update_blocks_usa_state_abbreviation(self, conv):
        with patch.object(multi_turn_conversation, 'get_raw_data_by_id',
                          return_value={"id": 1, "name": "Bob", "country": "USA"}):
            result = conv.execute_tool("update_record", {
                "table": "raw_data", "record_id": 1,
                "fields": {"state_province": "CA"}  # abbreviation, should block
            })
        assert "GUARDRAIL BLOCKED" in result

    def test_update_passes_usa_full_state_name(self, conv):
        with (
            patch.object(multi_turn_conversation, 'get_raw_data_by_id',
                         return_value={"id": 1, "name": "Bob", "country": "USA"}),
            patch.object(multi_turn_conversation, 'update_raw_data', return_value=True),
        ):
            result = conv.execute_tool("update_record", {
                "table": "raw_data", "record_id": 1,
                "fields": {"state_province": "California"}
            })
        assert "GUARDRAIL BLOCKED" not in result

    def test_update_blocks_nl_phone_without_country_code(self, conv):
        with patch.object(multi_turn_conversation, 'get_raw_data_by_id',
                          return_value={"id": 1, "country": "NL"}):
            result = conv.execute_tool("update_record", {
                "table": "raw_data", "record_id": 1,
                "fields": {"phone": "020 123 4567"}  # missing +31
            })
        assert "GUARDRAIL BLOCKED" in result

    def test_update_record_not_found(self, conv):
        with patch.object(multi_turn_conversation, 'get_raw_data_by_id', return_value=None):
            result = conv.execute_tool("update_record", {
                "table": "raw_data", "record_id": 999, "fields": {"city": "X"}
            })
        assert "not found" in result.lower()


# ── delete_record tool + guardrails ──────────────────────────────────────────

class TestDeleteRecordTool:
    def test_delete_valid(self, conv):
        with (
            patch.object(multi_turn_conversation, 'get_cleaned_data_for_raw', return_value=[]),
            patch.object(multi_turn_conversation, 'delete_raw_data', return_value=True),
        ):
            result = conv.execute_tool("delete_record", {"record_id": 1, "confirm": "yes"})
        assert "Deleted" in result

    def test_delete_blocked_without_confirm(self, conv):
        result = conv.execute_tool("delete_record", {"record_id": 1, "confirm": "no"})
        assert "GUARDRAIL BLOCKED" in result

    def test_delete_blocked_non_int_id(self, conv):
        result = conv.execute_tool("delete_record", {"record_id": "all", "confirm": "yes"})
        assert "GUARDRAIL BLOCKED" in result

    def test_delete_blocked_when_cleaned_data_exists(self, conv):
        with patch.object(multi_turn_conversation, 'get_cleaned_data_for_raw',
                          return_value=[{"id": 1}]):
            result = conv.execute_tool("delete_record", {"record_id": 1, "confirm": "yes"})
        assert "GUARDRAIL BLOCKED" in result

    def test_delete_override_allows_cleaning_records(self, conv):
        with (
            patch.object(multi_turn_conversation, 'get_cleaned_data_for_raw',
                         return_value=[{"id": 1}]),
            patch.object(multi_turn_conversation, 'delete_raw_data', return_value=True),
        ):
            result = conv.execute_tool("delete_record", {
                "record_id": 1, "confirm": "yes", "override_cleaned_check": True
            })
        assert "GUARDRAIL BLOCKED" not in result


# ── query_records tool ────────────────────────────────────────────────────────

class TestQueryRecordsTool:
    def test_query_returns_records(self, conv):
        with patch.object(multi_turn_conversation, 'query_records',
                          return_value=[{"id": 1, "name": "Alice"}]):
            result = conv.execute_tool("query_records", {"table": "raw_data"})
        assert "Alice" in result

    def test_query_empty_returns_no_records_message(self, conv):
        with patch.object(multi_turn_conversation, 'query_records', return_value=[]):
            result = conv.execute_tool("query_records", {"table": "raw_data"})
        assert "No records" in result

    def test_query_error_propagated(self, conv):
        with patch.object(multi_turn_conversation, 'query_records',
                          side_effect=ValueError("bad table")):
            result = conv.execute_tool("query_records", {"table": "bad_table"})
        assert "error" in result.lower()


# ── unknown tool ───────────────────────────────────────────────────────────────

class TestUnknownTool:
    def test_unknown_tool_returns_error_message(self, conv):
        result = conv.execute_tool("nonexistent_tool", {})
        assert "Unknown tool" in result


# ── conversation state ─────────────────────────────────────────────────────────

class TestConversationState:
    def test_initial_state(self, conv):
        assert conv.messages == []
        assert conv.turn_count == 0

    def test_system_prompt_stored(self, conv):
        assert conv.system_prompt == "test system prompt"
