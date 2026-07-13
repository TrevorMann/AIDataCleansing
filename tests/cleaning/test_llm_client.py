"""Tests for cleaning.llm_client."""
from unittest.mock import MagicMock, patch
import pytest


def test_build_clients_default_all_tiers_use_default_backend(monkeypatch):
    from cleaning.llm_client import build_clients
    monkeypatch.setenv("LLM_BACKEND_DEFAULT", "gpt-oss")
    monkeypatch.setenv("OPENROUTER_API_KEY", "fake")
    monkeypatch.delenv("LLM_BACKEND_FAST", raising=False)
    monkeypatch.delenv("LLM_BACKEND_STANDARD", raising=False)
    monkeypatch.delenv("LLM_BACKEND_DEEP", raising=False)
    clients = build_clients()
    assert clients.fast.model == "openai/gpt-oss-20b:free"
    assert clients.standard.model == "openai/gpt-oss-20b:free"
    assert clients.deep.model == "openai/gpt-oss-20b:free"
    assert clients.fast.supports_cache_control is False


def test_build_clients_per_tier_override(monkeypatch):
    from cleaning.llm_client import build_clients
    monkeypatch.setenv("LLM_BACKEND_DEFAULT", "gpt-oss")
    monkeypatch.setenv("LLM_BACKEND_DEEP", "anthropic-sonnet")
    monkeypatch.setenv("OPENROUTER_API_KEY", "fake")
    monkeypatch.setenv("ANTHROPIC_API_KEY", "fake")
    clients = build_clients()
    assert clients.fast.model == "openai/gpt-oss-20b:free"
    assert clients.deep.model == "claude-sonnet-4-6"
    assert clients.deep.supports_cache_control is True


def test_unknown_backend_raises(monkeypatch):
    from cleaning.llm_client import build_clients
    monkeypatch.setenv("LLM_BACKEND_DEFAULT", "made-up")
    monkeypatch.setenv("OPENROUTER_API_KEY", "fake")
    with pytest.raises(ValueError, match="Unknown LLM backend"):
        build_clients()


def test_bedrock_backend_uses_configured_model_id(monkeypatch):
    from cleaning.llm_client import build_clients
    monkeypatch.setenv("LLM_BACKEND_DEFAULT", "bedrock-haiku")
    monkeypatch.setenv("BEDROCK_HAIKU_MODEL_ID", "anthropic.claude-haiku-4-5-v1:0")
    monkeypatch.setenv("AWS_REGION", "us-east-1")
    with patch("cleaning.llm_client.AnthropicBedrock") as mock_bedrock_cls:
        mock_bedrock_cls.return_value = MagicMock()
        clients = build_clients()
    mock_bedrock_cls.assert_called_with(aws_region="us-east-1", aws_profile=None)
    assert clients.fast.model == "anthropic.claude-haiku-4-5-v1:0"
    assert clients.fast.supports_cache_control is True


def test_bedrock_backend_missing_model_id_raises(monkeypatch):
    from cleaning.llm_client import build_clients
    monkeypatch.setenv("LLM_BACKEND_DEFAULT", "bedrock-sonnet")
    monkeypatch.delenv("BEDROCK_SONNET_MODEL_ID", raising=False)
    with pytest.raises(ValueError, match="BEDROCK_SONNET_MODEL_ID not set"):
        build_clients()


def test_messages_create_calls_sdk_with_args():
    from cleaning.llm_client import LLMClient
    sdk = MagicMock()
    sdk.messages.create.return_value = "ok"
    client = LLMClient(sdk=sdk, model="m", supports_cache_control=False, base_url=None)
    result = client.messages_create(
        system="sys", messages=[{"role": "user", "content": "hi"}], tools=[],
    )
    assert result == "ok"
    args, kwargs = sdk.messages.create.call_args
    assert kwargs["model"] == "m"
    assert kwargs["max_tokens"] == 2048
    # Without cache support, system is passed as a plain string.
    assert kwargs["system"] == "sys"


def test_messages_create_adds_cache_control_when_supported():
    from cleaning.llm_client import LLMClient
    sdk = MagicMock()
    sdk.messages.create.return_value = "ok"
    client = LLMClient(sdk=sdk, model="m", supports_cache_control=True, base_url=None)
    client.messages_create(system="sys", messages=[], tools=[{"name": "t"}])
    _, kwargs = sdk.messages.create.call_args
    assert isinstance(kwargs["system"], list)
    assert kwargs["system"][0]["cache_control"] == {"type": "ephemeral"}
    assert kwargs["tools"][-1].get("cache_control") == {"type": "ephemeral"}


def test_messages_create_retries_then_raises():
    from cleaning.llm_client import LLMClient, LLMUnavailableError
    sdk = MagicMock()
    sdk.messages.create.side_effect = ConnectionError("boom")
    client = LLMClient(sdk=sdk, model="m", supports_cache_control=False, base_url=None)
    with pytest.raises(LLMUnavailableError):
        client.messages_create(system="s", messages=[], tools=[])
    assert sdk.messages.create.call_count == 3
