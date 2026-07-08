"""Tiered, env-driven LLM client wrapper around the Anthropic SDK.

Three tiers — fast / standard / deep — each independently configurable via env.
The same code runs against gpt-oss-20b:free (OpenRouter), Haiku 4.5 (OpenRouter
or native), and Sonnet/Opus (native) without changes; only env vars differ.

cache_control={"type":"ephemeral"} is added at this layer (on system + tools
blocks) when the backend supports it, so callers don't need to know about caching.
"""
from __future__ import annotations

import logging
import os
import time
from dataclasses import dataclass
from typing import Any

try:
    from anthropic import Anthropic, RateLimitError as AnthropicRateLimitError
except ModuleNotFoundError:
    class AnthropicRateLimitError(Exception):  # type: ignore[no-redef]
        """Stub for tests without the anthropic package."""

    class Anthropic:  # type: ignore[override]
        """Minimal fallback used in tests when the anthropic package is absent."""

        def __init__(self, *args, **kwargs):
            self.args = args
            self.kwargs = kwargs
            self.messages = _AnthropicMessagesStub()


    class _AnthropicMessagesStub:
        def create(self, *args, **kwargs):
            raise ModuleNotFoundError(
                "anthropic package is required to perform live LLM calls"
            )


logger = logging.getLogger(__name__)


# Backend token → (model_id, base_url, supports_cache_control)
# When base_url is None the SDK uses the default api.anthropic.com endpoint.
_BACKEND_TABLE = {
    "gpt-oss":          ("openai/gpt-oss-20b:free",       "https://openrouter.ai/api", False),
    "haiku-or":         ("anthropic/claude-haiku-4.5",    "https://openrouter.ai/api", True),
    "anthropic-haiku":  ("claude-haiku-4-5-20251001",     None,                        True),
    "anthropic-sonnet": ("claude-sonnet-4-6",             None,                        True),
    "anthropic-opus":   ("claude-opus-4-7",               None,                        True),
}


_CACHE_THRESHOLD_TOKENS = 4096  # Haiku 4.5 floor; see spec §5.5


class LLMUnavailableError(RuntimeError):
    """Raised when an LLM call fails after retries."""


def _log_usage(model: str, usage: Any) -> None:
    """Log token usage + cache metrics for every LLM call (cost tracking)."""
    if usage is None:
        return
    logger.info(
        "[%s] input=%s cache_creation=%s cache_read=%s output=%s",
        model,
        getattr(usage, "input_tokens", "?"),
        getattr(usage, "cache_creation_input_tokens", 0),
        getattr(usage, "cache_read_input_tokens", 0),
        getattr(usage, "output_tokens", "?"),
    )


@dataclass
class LLMClient:
    sdk: Anthropic
    model: str
    supports_cache_control: bool
    base_url: str | None

    def messages_create(
        self,
        *,
        system: str,
        messages: list[dict],
        tools: list[dict],
        max_tokens: int = 2048,
    ) -> Any:
        """Single LLM call surface. Retries 3× with backoff on transient errors."""
        sys_arg, tools_arg = self._apply_cache_control(system, tools)

        last_exc: Exception | None = None
        for attempt in range(3):
            try:
                resp = self.sdk.messages.create(
                    model=self.model,
                    max_tokens=max_tokens,
                    system=sys_arg,
                    messages=messages,
                    tools=tools_arg,
                )
                _log_usage(self.model, getattr(resp, "usage", None))
                return resp
            except (ConnectionError, TimeoutError, AnthropicRateLimitError) as e:
                last_exc = e
                if attempt < 2:
                    time.sleep(0.5 * (2 ** attempt))
        raise LLMUnavailableError(f"LLM call failed after 3 attempts: {last_exc}")

    def _apply_cache_control(
        self, system: str, tools: list[dict]
    ) -> tuple[Any, list[dict]]:
        """Add cache_control breakpoints to system + final tool when supported."""
        if not self.supports_cache_control:
            return system, tools

        system_blocks = [{"type": "text", "text": system,
                          "cache_control": {"type": "ephemeral"}}]
        if tools:
            tools_with_cache = list(tools)
            last = dict(tools_with_cache[-1])
            last["cache_control"] = {"type": "ephemeral"}
            tools_with_cache[-1] = last
        else:
            tools_with_cache = tools
        return system_blocks, tools_with_cache


@dataclass
class Clients:
    fast:     LLMClient
    standard: LLMClient
    deep:     LLMClient


def _build_one(backend_token: str) -> LLMClient:
    if backend_token not in _BACKEND_TABLE:
        raise ValueError(f"Unknown LLM backend: {backend_token!r}. "
                         f"Valid: {sorted(_BACKEND_TABLE)}")
    model, base_url, cache = _BACKEND_TABLE[backend_token]

    api_key = (os.getenv("OPENROUTER_API_KEY")
               if base_url == "https://openrouter.ai/api"
               else os.getenv("ANTHROPIC_API_KEY"))
    if not api_key:
        env_var = ("OPENROUTER_API_KEY" if base_url == "https://openrouter.ai/api"
                   else "ANTHROPIC_API_KEY")
        raise ValueError(f"{env_var} not set; required for backend {backend_token!r}")

    sdk = Anthropic(base_url=base_url, api_key=api_key) if base_url else Anthropic(api_key=api_key)
    return LLMClient(sdk=sdk, model=model, supports_cache_control=cache, base_url=base_url)


def build_client_for_tier(tier: str) -> LLMClient:
    """Return a single-tier client. Used by skills that need lazy initialization."""
    if tier not in ("fast", "standard", "deep"):
        raise ValueError(f"Unknown tier: {tier!r}. Valid: fast, standard, deep")
    return getattr(build_clients(), tier)


def build_clients() -> Clients:
    """Construct the tiered client bundle from env vars."""
    default = os.getenv("LLM_BACKEND_DEFAULT", "gpt-oss")
    fast     = os.getenv("LLM_BACKEND_FAST", default)
    standard = os.getenv("LLM_BACKEND_STANDARD", default)
    deep     = os.getenv("LLM_BACKEND_DEEP", default)
    return Clients(
        fast=_build_one(fast),
        standard=_build_one(standard),
        deep=_build_one(deep),
    )


def warn_if_under_cache_threshold(client: LLMClient, system: str,
                                  tools: list[dict], tier_name: str) -> None:
    """Startup-time check: warn if cached payload won't reach the 4096-token floor.

    Uses len(text)//4 as a fast token estimate; precise tokenization is not
    worth a tiktoken dependency for a startup warning.
    """
    if not client.supports_cache_control:
        return
    payload = system + str(tools)
    estimated_tokens = len(payload) // 4
    if estimated_tokens < _CACHE_THRESHOLD_TOKENS:
        logger.warning(
            "system+tools for tier %s estimated at ~%d tokens (<%d) — "
            "caching will not engage on Haiku 4.5",
            tier_name, estimated_tokens, _CACHE_THRESHOLD_TOKENS,
        )
