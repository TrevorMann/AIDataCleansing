import os
import tempfile
from unittest.mock import MagicMock
from database import init_db
from scope_interpreter import ScopeInterpreter


def _make_db():
    tmpdir = tempfile.mkdtemp()
    db_path = os.path.join(tmpdir, "test.db")
    init_db(db_path)
    return db_path


def _mock_client(json_text: str):
    """Build a mock client whose messages.create returns json_text as text."""
    block = MagicMock()
    block.text = json_text
    block.type = "text"

    usage = MagicMock()
    usage.input_tokens = 10
    usage.output_tokens = 5
    usage.cache_read_input_tokens = 0
    usage.cache_creation_input_tokens = 0

    response = MagicMock()
    response.content = [block]
    response.usage = usage

    client = MagicMock()
    client.messages.create.return_value = response
    return client


def test_interpret_returns_filter_dict():
    db = _make_db()
    client = _mock_client('{"country": "USA"}')
    interp = ScopeInterpreter(client, "anthropic", "claude-haiku-4-5-20251001")
    result = interp.interpret("clean US data", "real_estate", db)
    assert result == {"country": "USA"}


def test_interpret_returns_empty_dict_for_all():
    db = _make_db()
    client = _mock_client('{}')
    interp = ScopeInterpreter(client, "anthropic", "claude-haiku-4-5-20251001")
    result = interp.interpret("clean all data", "real_estate", db)
    assert result == {}


def test_interpret_returns_none_when_llm_returns_null():
    db = _make_db()
    client = _mock_client('null')
    interp = ScopeInterpreter(client, "anthropic", "claude-haiku-4-5-20251001")
    result = interp.interpret("clean purple unicorn data", "real_estate", db)
    assert result is None


def test_interpret_returns_none_on_invalid_json():
    db = _make_db()
    client = _mock_client('not valid json at all')
    interp = ScopeInterpreter(client, "anthropic", "claude-haiku-4-5-20251001")
    result = interp.interpret("clean US data", "real_estate", db)
    assert result is None


def test_interpret_calls_messages_create_once():
    db = _make_db()
    client = _mock_client('{"country": "CA"}')
    interp = ScopeInterpreter(client, "anthropic", "claude-haiku-4-5-20251001")
    interp.interpret("clean canadian data", "real_estate", db)
    assert client.messages.create.call_count == 1


def test_interpret_passes_schema_in_user_message():
    db = _make_db()
    client = _mock_client('{}')
    interp = ScopeInterpreter(client, "anthropic", "claude-haiku-4-5-20251001")
    interp.interpret("clean all data", "real_estate", db)
    call_kwargs = client.messages.create.call_args
    messages = call_kwargs.kwargs.get("messages") or call_kwargs[1].get("messages") or call_kwargs[0][3]
    user_content = messages[0]["content"]
    assert "Schema" in user_content
    assert "clean all data" in user_content


def test_interpret_returns_none_when_llm_returns_non_dict():
    db = _make_db()
    client = _mock_client('["CA", "USA"]')
    interp = ScopeInterpreter(client, "anthropic", "claude-haiku-4-5-20251001")
    result = interp.interpret("clean data", "real_estate", db)
    assert result is None
