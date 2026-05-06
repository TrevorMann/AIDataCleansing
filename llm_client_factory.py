"""
LLM client factory — two supported backends with best-practice configuration.

══════════════════════════════════════════════════════════════════════════════
PATH A — OPENROUTER
══════════════════════════════════════════════════════════════════════════════
Uses OpenRouter's OpenAI-compatible endpoint via the Anthropic SDK.
No prompt caching (cache_control is silently ignored by OpenRouter).
Supports any model available on OpenRouter (Claude, GPT, Llama, Mistral, …).

Required env vars:
  OPENROUTER_API_KEY=sk-or-...
  LLM_BACKEND=openrouter          # or omit — auto-detected if key is present

Optional:
  OPENROUTER_MODEL=anthropic/claude-haiku-4-5   # default if unset

Usage in messages.create():
  system = <plain string>         # no cache_control blocks
  model  = OPENROUTER model name  # e.g. "anthropic/claude-haiku-4-5"
  timeout = 30 (set via build_message_kwargs)
  headers are auto-injected via client setup

══════════════════════════════════════════════════════════════════════════════
PATH B — ANTHROPIC (direct)
══════════════════════════════════════════════════════════════════════════════
Direct Anthropic API. Supports prompt caching via cache_control blocks on the
system prompt, which cuts costs significantly on repeated base-prompt tokens.

Required env vars:
  ANTHROPIC_API_KEY=sk-ant-...
  LLM_BACKEND=anthropic           # or omit — fallback when no OpenRouter key

Optional:
  ANTHROPIC_MODEL=claude-haiku-4-5-20251001   # default if unset
  ANTHROPIC_BUDGET_TOKENS=100000  # optional safety limit on input+output

Usage in messages.create():
  system = [                      # list of blocks enables caching
      {"type": "text", "text": base_prompt, "cache_control": {"type": "ephemeral"}},
      {"type": "text", "text": skill_content},   # if a skill is active
  ]
  model  = Anthropic model ID     # e.g. "claude-haiku-4-5-20251001"
  timeout = 30 (set via build_message_kwargs)
  budget_tokens = ANTHROPIC_BUDGET_TOKENS (optional, set via build_message_kwargs)

Usage tracking:
  After messages.create(), call log_usage(response.usage) to see:
  - input_tokens, cache_creation_input_tokens, cache_read_input_tokens, output_tokens
  - cache_hit_rate for cost optimization

══════════════════════════════════════════════════════════════════════════════
BACKEND SELECTION
══════════════════════════════════════════════════════════════════════════════
  1. LLM_BACKEND env var (explicit): "openrouter" | "anthropic"
  2. Auto-detect: OPENROUTER_API_KEY present → openrouter, else → anthropic
"""

import os
import logging
from pathlib import Path
from anthropic import Anthropic

logger = logging.getLogger(__name__)


def _load_dotenv() -> None:
    """Load .env from project root into os.environ (no-op if already set or file missing)."""
    env_path = Path(__file__).parent / ".env"
    if not env_path.exists():
        return
    with open(env_path) as f:
        for line in f:
            line = line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            key, value = line.split("=", 1)
            key = key.strip()
            # Never overwrite values already set in the environment
            if key not in os.environ:
                os.environ[key] = value.strip()


_load_dotenv()

# ── Backend constants ──────────────────────────────────────────────────────────
OPENROUTER = "openrouter"
ANTHROPIC  = "anthropic"

_OPENROUTER_BASE_URL = "https://openrouter.ai/api"
_REQUEST_TIMEOUT = 30  # seconds

_DEFAULT_MODELS: dict[str, str] = {
    OPENROUTER: "anthropic/claude-haiku-4-5",
    ANTHROPIC:  "claude-haiku-4-5-20251001",
}


def get_backend() -> str:
    """Resolve backend from env. Returns 'openrouter' or 'anthropic'."""
    explicit = os.getenv("LLM_BACKEND", "").lower().strip()
    if explicit in (OPENROUTER, ANTHROPIC):
        return explicit
    # Auto-detect
    if os.getenv("OPENROUTER_API_KEY"):
        return OPENROUTER
    return ANTHROPIC


def create_client() -> tuple[Anthropic, str, str]:
    """
    Create and return (client, backend, model).

    backend is 'openrouter' or 'anthropic' — use it to decide how to format
    the system prompt (plain string vs list of cache_control blocks).

    OpenRouter: Sets X-Title header for tracking + 30s timeout.
    Anthropic: Sets 30s timeout + optional budget_tokens limit.
    """
    backend = get_backend()

    if backend == OPENROUTER:
        api_key = os.getenv("OPENROUTER_API_KEY")
        if not api_key:
            raise EnvironmentError(
                "OPENROUTER_API_KEY not set. Add it to .env or set LLM_BACKEND=anthropic."
            )
        # OpenRouter recommends X-Title header for request identification
        default_headers = {
            "X-Title": "AI Data Cleaning Pipeline",
            "HTTP-Referer": "https://github.com/anthropics/anthropic-sdk-python",
        }
        client = Anthropic(
            base_url=_OPENROUTER_BASE_URL,
            api_key=api_key,
            default_headers=default_headers,
            timeout=_REQUEST_TIMEOUT,
        )
        model = os.getenv("OPENROUTER_MODEL", _DEFAULT_MODELS[OPENROUTER])

    else:  # ANTHROPIC
        api_key = os.getenv("ANTHROPIC_API_KEY")
        if not api_key:
            raise EnvironmentError(
                "ANTHROPIC_API_KEY not set. Add it to .env or set LLM_BACKEND=openrouter."
            )
        client = Anthropic(api_key=api_key, timeout=_REQUEST_TIMEOUT)
        model = os.getenv("ANTHROPIC_MODEL", _DEFAULT_MODELS[ANTHROPIC])

    return client, backend, model


def build_system_param(backend: str, base_system: str, skill_content: str = "") -> str | list:
    """
    Build the value for messages.create(system=...) for the given backend.

    PATH A — OpenRouter: returns a plain string (concatenated if skill active).
    PATH B — Anthropic:  returns a list of typed blocks.
                         base_system gets cache_control so it is cached across turns.
                         skill_content is a second block (not cached — changes per turn).
    """
    if backend == OPENROUTER:
        # ── PATH A: plain string, no cache_control ────────────────────────────
        if skill_content:
            return f"{base_system}\n\n# Active Skill Instructions\n\n{skill_content}"
        return base_system

    else:
        # ── PATH B: typed blocks with prompt caching ──────────────────────────
        blocks: list[dict] = [
            {
                "type": "text",
                "text": base_system,
                "cache_control": {"type": "ephemeral"},  # cache the expensive base prompt
            }
        ]
        if skill_content:
            blocks.append({"type": "text", "text": f"# Active Skill Instructions\n\n{skill_content}"})
        return blocks


def build_message_kwargs(backend: str) -> dict:
    """
    Build kwargs for messages.create() that are backend-specific.

    PATH A — OpenRouter: returns {} (timeout already set in client)
    PATH B — Anthropic:  returns dict with optional budget_tokens safety limit
    """
    if backend == OPENROUTER:
        return {}

    else:  # ANTHROPIC
        kwargs = {}
        budget = os.getenv("ANTHROPIC_BUDGET_TOKENS")
        if budget:
            try:
                kwargs["budget_tokens"] = int(budget)
            except ValueError:
                logger.warning(f"Invalid ANTHROPIC_BUDGET_TOKENS={budget}, ignoring")
        return kwargs


def log_usage(backend: str, usage) -> None:
    """
    Log token usage and cache metrics for cost tracking.

    PATH A — OpenRouter: logs input/output tokens only
    PATH B — Anthropic:  logs input, cache hits, cache creation, output tokens + cache rate
    """
    if backend == OPENROUTER:
        logger.info(
            f"[OpenRouter] tokens: input={usage.input_tokens}, output={usage.output_tokens}"
        )
    else:  # ANTHROPIC
        cache_read = getattr(usage, "cache_read_input_tokens", 0)
        cache_creation = getattr(usage, "cache_creation_input_tokens", 0)
        input_tokens = usage.input_tokens

        cache_hit_rate = 0.0
        if cache_read > 0:
            total_cache_relevant = cache_read + cache_creation
            cache_hit_rate = (cache_read / total_cache_relevant * 100) if total_cache_relevant > 0 else 0.0

        logger.info(
            f"[Anthropic] input={input_tokens} "
            f"cache_creation={cache_creation} cache_read={cache_read} "
            f"cache_rate={cache_hit_rate:.1f}% "
            f"output={usage.output_tokens}"
        )
