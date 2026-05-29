"""Shared fixtures for cleaning/ tests."""
import os
import tempfile
from unittest.mock import MagicMock
import pytest


@pytest.fixture
def tmp_db(monkeypatch):
    """Yield a path to a fresh, isolated SQLite DB initialized with the full schema.

    Pins DB_BACKEND=sqlite for the duration of the test so the db.helpers and
    db.schema_discovery dispatchers route to the SQLite backend regardless of the
    .env default (which is postgres). Only DB_BACKEND is overridden; all other
    config values are left intact and the change is reverted after the test.
    """
    import config
    from db.sqlite_init import init_db

    config.get_config_value("DB_BACKEND")  # ensure _ENV_CACHE is populated
    monkeypatch.setitem(config._ENV_CACHE, "DB_BACKEND", "sqlite")

    with tempfile.TemporaryDirectory() as tmpdir:
        path = os.path.join(tmpdir, "test.db")
        init_db(path)
        yield path


@pytest.fixture
def mock_llm():
    """Return a MagicMock standing in for cleaning.llm_client.LLMClient.

    Default behavior: messages_create returns a MagicMock with stop_reason='end_turn'
    and content=[]; tests override .messages_create.side_effect or .return_value as needed.
    """
    client = MagicMock()
    client.model = "mock-model"
    client.supports_cache_control = False
    response = MagicMock()
    response.stop_reason = "end_turn"
    response.content = []
    client.messages_create.return_value = response
    return client


@pytest.fixture
def mock_tavily(monkeypatch):
    """Patch the underlying Tavily call so WebSearchCache misses don't hit the network.

    Tests can mutate the returned MagicMock's return_value or side_effect.
    Returns the mock so tests can assert call counts.
    """
    from cleaning import cache
    fake = MagicMock(return_value="MOCKED TAVILY RESULT")
    monkeypatch.setattr(cache, "_tavily_call", fake)
    return fake
