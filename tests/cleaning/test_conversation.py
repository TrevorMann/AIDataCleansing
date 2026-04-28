"""Tests for cleaning.conversation.AdHocConversation."""
from unittest.mock import MagicMock


def _text_response(text):
    block = MagicMock(); block.type = "text"; block.text = text
    del block.name
    resp = MagicMock(); resp.content = [block]; resp.stop_reason = "end_turn"
    return resp


def test_adhoc_send_returns_assistant_text(tmp_db):
    from cleaning.conversation import AdHocConversation
    from cleaning.llm_client import Clients, LLMClient
    sdk = MagicMock()
    sdk.messages.create.return_value = _text_response("Hello, how can I help?")
    fake = LLMClient(sdk=sdk, model="m", supports_cache_control=False, base_url=None)
    clients = Clients(fast=fake, standard=fake, deep=fake)
    convo = AdHocConversation(clients=clients, db_path=tmp_db)
    out = convo.send("Hi")
    assert "Hello, how can I help?" in out


def test_adhoc_send_appends_to_message_history(tmp_db):
    from cleaning.conversation import AdHocConversation
    from cleaning.llm_client import Clients, LLMClient
    sdk = MagicMock()
    sdk.messages.create.return_value = _text_response("ok")
    fake = LLMClient(sdk=sdk, model="m", supports_cache_control=False, base_url=None)
    clients = Clients(fast=fake, standard=fake, deep=fake)
    convo = AdHocConversation(clients=clients, db_path=tmp_db)
    convo.send("first message")
    convo.send("second message")
    assert len(convo.messages) >= 4  # 2 user + 2 assistant minimum


def test_adhoc_send_dispatches_tool_and_returns_final_text(tmp_db):
    """send() handles a tool_use response: dispatches tool, sends result, gets final text."""
    from cleaning.conversation import AdHocConversation
    from cleaning.llm_client import Clients, LLMClient

    # First response: tool_use
    tool_block = MagicMock()
    tool_block.type = "tool_use"
    tool_block.name = "query_records"
    tool_block.id = "tool_1"
    tool_block.input = {"table": "raw_data"}
    tool_resp = MagicMock()
    tool_resp.content = [tool_block]
    tool_resp.stop_reason = "tool_use"

    # Second response: final text
    final_block = MagicMock(); final_block.type = "text"; final_block.text = "Done."
    del final_block.name
    final_resp = MagicMock(); final_resp.content = [final_block]; final_resp.stop_reason = "end_turn"

    sdk = MagicMock()
    sdk.messages.create.side_effect = [tool_resp, final_resp]
    fake = LLMClient(sdk=sdk, model="m", supports_cache_control=False, base_url=None)
    clients = Clients(fast=fake, standard=fake, deep=fake)
    convo = AdHocConversation(clients=clients, db_path=tmp_db)
    result = convo.send("show records")
    assert result == "Done."
    # messages: user, assistant(tool_use), user(tool_result), assistant(final)
    assert len(convo.messages) == 4


def test_adhoc_insert_guardrail_blocks_invalid_age(tmp_db):
    """_insert_record returns GUARDRAIL BLOCKED when age fails check."""
    from cleaning.conversation import AdHocConversation
    from cleaning.llm_client import Clients, LLMClient
    sdk = MagicMock()
    fake = LLMClient(sdk=sdk, model="m", supports_cache_control=False, base_url=None)
    clients = Clients(fast=fake, standard=fake, deep=fake)
    convo = AdHocConversation(clients=clients, db_path=tmp_db)
    result = convo._insert_record({"name": "test", "age": -5})
    assert "GUARDRAIL BLOCKED" in result


def test_adhoc_delete_guardrail_blocks_without_confirm(tmp_db):
    """_delete_record returns GUARDRAIL BLOCKED when confirm != 'yes'."""
    from db_helpers import insert_raw_data
    from cleaning.conversation import AdHocConversation
    from cleaning.llm_client import Clients, LLMClient
    rid = insert_raw_data(tmp_db, name="to_delete", country="CA")
    sdk = MagicMock()
    fake = LLMClient(sdk=sdk, model="m", supports_cache_control=False, base_url=None)
    clients = Clients(fast=fake, standard=fake, deep=fake)
    convo = AdHocConversation(clients=clients, db_path=tmp_db)
    result = convo._delete_record({"record_id": rid, "confirm": "no"})
    assert "GUARDRAIL BLOCKED" in result
