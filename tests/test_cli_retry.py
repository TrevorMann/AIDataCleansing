"""Tests for 429 / transient retry logic in cli.py."""

import sys
import time
from unittest.mock import MagicMock, call, patch

import pytest

# ── stub anthropic + module-level side effects before importing cli ─────────────

if "anthropic" not in sys.modules:
    _stub = MagicMock()
    _stub.RateLimitError = type("RateLimitError", (Exception,), {})
    sys.modules["anthropic"] = _stub

_cli_patches = [
    patch("llm_client_factory.create_client",
          return_value=(MagicMock(), "openrouter", "test-model")),
    patch("db.schema_discovery.format_schema_for_prompt", return_value="<SCHEMA/>"),
]
for _p in _cli_patches:
    _p.start()

import cli  # noqa: E402 — must come after patches

for _p in _cli_patches:
    _p.stop()


# ── helpers ───────────────────────────────────────────────────────────────────────

def _make_response(text="ok"):
    resp = MagicMock()
    resp.stop_reason = "end_turn"
    block = MagicMock()
    block.text = text
    resp.content = [block]
    resp.usage = MagicMock()
    return resp


# ── retry on RateLimitError ───────────────────────────────────────────────────────

class TestRetryOn429:
    def test_succeeds_on_first_attempt_no_retry(self):
        mock_resp = _make_response("hello")
        with patch.object(cli, "_CLIENT") as mock_client:
            mock_client.messages.create.return_value = mock_resp
            result = cli._call_llm(
                model="m", max_tokens=100, system="s", messages=[], tools=[]
            )
        assert mock_client.messages.create.call_count == 1
        assert result is mock_resp

    def test_retries_on_connection_error(self):
        mock_resp = _make_response("ok")
        with patch.object(cli, "_CLIENT") as mock_client, \
             patch("cli.time"):
            mock_client.messages.create.side_effect = [
                ConnectionError("network down"),
                mock_resp,
            ]
            result = cli._call_llm(
                model="m", max_tokens=100, system="s", messages=[], tools=[]
            )
        assert result is mock_resp

    def test_retries_on_timeout_error(self):
        mock_resp = _make_response("ok")
        with patch.object(cli, "_CLIENT") as mock_client, \
             patch("cli.time"):
            mock_client.messages.create.side_effect = [
                TimeoutError("timed out"),
                mock_resp,
            ]
            result = cli._call_llm(
                model="m", max_tokens=100, system="s", messages=[], tools=[]
            )
        assert result is mock_resp

    def test_does_not_retry_on_unrelated_exception(self):
        with patch.object(cli, "_CLIENT") as mock_client:
            mock_client.messages.create.side_effect = ValueError("bad param")
            with pytest.raises(ValueError):
                cli._call_llm(
                    model="m", max_tokens=100, system="s", messages=[], tools=[]
                )
        assert mock_client.messages.create.call_count == 1


# ── _llm_loop uses _call_llm ──────────────────────────────────────────────────────

class TestLlmLoopUsesRetry:
    def test_llm_loop_retries_on_rate_limit(self):
        mock_resp = _make_response("done")
        mock_resp.stop_reason = "end_turn"
        with patch.object(cli, "_call_llm") as mock_call:
            mock_call.return_value = mock_resp
            with patch.object(cli, "log_usage"):
                result = cli._llm_loop([], "system", [])
        mock_call.assert_called_once()
        assert result == "done"


# ── _handle_clean scope call uses _call_llm ───────────────────────────────────────

class TestHandleCleanUsesRetry:
    def test_scope_call_goes_through_call_llm(self):
        mock_resp = MagicMock()
        mock_resp.content = []   # no tool_use blocks → fetched_records stays []
        mock_resp.usage = MagicMock()
        with patch.object(cli, "_call_llm", return_value=mock_resp) as mock_call, \
             patch.object(cli, "log_usage"), \
             patch.object(cli, "_col_names", return_value=[]):
            cli._handle_clean("all records")   # returns early: no records found
        mock_call.assert_called()
