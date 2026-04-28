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
