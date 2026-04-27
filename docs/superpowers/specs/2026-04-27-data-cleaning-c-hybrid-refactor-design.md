# Data Cleaning Refactor — C-Hybrid Design

**Date:** 2026-04-27
**Status:** Approved (brainstorming complete, ready for implementation plan)
**Scope:** Refactor of `multi_turn_conversation.py` and supporting modules into a `cleaning/` subpackage with per-record country routing, an escalation sub-agent for hard cases, a queryable `flags` table, tiered LLM clients, and a programmatic-first public API.
**Companion doc:** `2026-04-27-data-cleaning-a-migration-design.md` describes the future migration from C-Hybrid to A (per-country sub-agents).

---

## 1. Goals

- Eliminate the silent-Canada-default bug where mixed-country batches all run through the Canada research prompt.
- Make flagging queryable so unresolved records can be reviewed systematically.
- Collapse five duplicated `handle_<country>_cleaning` methods into one dispatcher.
- Remove dead code (unused phone-validation tools, legacy menu workflow, debug scripts).
- Draw a clean `CleaningAgent` boundary so a future migration to per-country sub-agents (option A) is mechanical, not a rewrite.
- Support a staged model strategy: `gpt-oss-20b:free` now → `Haiku 4.5` via OpenRouter for vetting → native Anthropic for production, with no code changes between stages — only env vars.
- Allow per-component model tiers so escalation can use a stronger model than bulk cleaning.

## 2. Non-goals

- Package-wide reorganisation into `src/`. Files stay flat at the project root except for the new `cleaning/` subpackage.
- Type-hint sweep across pre-existing modules.
- Rewrite of `validate_data_quality.py`. Its overlap with `pre_cleaner.needs_research` is left for a future cleanup.
- Real-time auto-routing based on per-record complexity. Tier→component mapping is fixed at orchestrator construction time.
- A web UI or HTTP service. The system stays a Python library plus a REPL.

## 3. Locked decisions

| Area | Decision |
|---|---|
| Architecture | C-Hybrid: Python pre-clean → shared per-country research agent → escalation sub-agent for hard cases. |
| Routing | Per-record auto-routing; user keyword (`CLEAN Canadian data`) overrides. Records grouped by country before research. Unknown country → escalation. |
| Flagging | Separate `flags` table; one record can carry many flags. |
| Model/API | Single env-driven config surface. Default: `gpt-oss-20b:free` via OpenRouter (Anthropic SDK). Staged plan supports `Haiku 4.5` (OpenRouter) and native Anthropic later. |
| LLM tiers | Three named tiers — `fast`, `standard`, `deep` — each independently configurable. Components are assigned a tier; assignment is fixed at orchestrator construction. |
| `cache_control` | Added at the LLMClient layer on `system` and `tools` blocks when the backend supports it. Silently omitted otherwise. |
| Scope | Tight + dead-code removal + duplicate-truth consolidation. |
| Interface | Programmatic-first: `run_cleaning_workflow(query, *, country_override=None) -> CleaningRunReport`. REPL is a thin wrapper at `multi_turn_conversation.py`. |
| Module layout | All new code lives in `cleaning/` subpackage. `pre_cleaner.py` moves into it. |

## 4. Architecture

### 4.1 Pipeline

```
run_cleaning_workflow(query, *, country_override=None, db_path=None, clients=None)
  │
  ├─ 1. interpret_query     → {country_filter, scope, limit}    [clients.fast]
  ├─ 2. fetch_records       → list[raw_record]                   [DB]
  ├─ 3. pre_clean_batch     → list[pre_cleaned], list[needs_research]   [Python]
  ├─ 4. group_by_country    → {country_code: list[records]}      [per-record routing]
  │       │
  │       └─ for each country group:
  │            5. CleaningAgent(country, prompt, tools, cache, escalator).process(records)   [clients.standard]
  │                  │
  │                  ├─ web_search via WebSearchCache
  │                  ├─ if needs_escalation(output):
  │                  │    → escalator.investigate(record, country, flag_hints, prior_search_log)   [clients.deep]
  │                  └─ return list[CleaningOutput]
  │
  ├─ 6. merge_results       → reconcile pre-cleaned + agent + escalation
  ├─ 7. persist             → cleaned_data + flags + audit_log   [single transaction per record]
  └─ 8. return CleaningRunReport
```

### 4.2 Module layout

```
cleaning/
  __init__.py          # public API: run_cleaning_workflow, CleaningRunReport, AdHocConversation, Flag, FlagType
  agent.py             # CleaningAgent class — generic per-country agent (the A-migration boundary)
  orchestrator.py      # interpret/fetch/group/dispatch/merge/persist
  escalation.py        # EscalationAgent — focused investigation for hard cases
  cache.py             # WebSearchCache + the Tavily call function
  llm_client.py        # build_clients(), Clients dataclass, LLMClient
  flags.py             # FlagType enum, Flag dataclass, persist_flags(), query_unresolved()
  conversation.py      # AdHocConversation — REPL chat with full CRUD tool access
  pre_cleaner.py       # MOVED from project root — deterministic Python cleaning
  types.py             # shared dataclasses: CleaningOutput, SearchHit, CleaningRunReport
prompts/               # unchanged in shape; content tightened
multi_turn_conversation.py  # ~120 lines, REPL only, calls into cleaning.run_cleaning_workflow
database.py            # adds flags table to init_db()
db_helpers.py          # adds flags CRUD
guardrails.py          # VALID_COUNTRIES becomes the single source of truth for canonical codes
```

### 4.3 Files removed in this refactor

- `data_cleaning/clean_data_workflow.py` — legacy menu-based path, superseded.
- `debug_api.py`, `debug_output.txt`, `test_direct.py`, `test_sdk.py` — ad-hoc debug scripts.
- `validate_na_phone`, `validate_eu_phone`, `format_na_phone` (Python functions and tool definitions) — `pre_cleaner.format_phone` is the single source of truth.
- `detect_country_scope` (in `multi_turn_conversation.py`) and the country-detection block inside `interpret_user_query` (in `data_cleaning_agent.py`) — replaced by one `cleaning.orchestrator.detect_country_filter` plus `pre_cleaner.get_country_code` for per-record resolution.
- `data_cleaning_agent.py` — its useful parts (`interpret_user_query`, `fetch_data_for_query`, `parse_research_response`, `merge_results`, `save_cleaned_results`, `generate_report`) move into `cleaning/orchestrator.py`. The format-for-Claude / parse-cleaned-response paths used by the dead workflow are dropped.

## 5. Component design

### 5.1 `CleaningAgent` — the migration boundary

```python
# cleaning/agent.py

@dataclass
class CleaningOutput:
    cleaned_record: dict          # the merged result, keyed by raw_data_id
    flags: list[Flag]             # zero or more flags raised for this record
    search_log: list[SearchHit]   # what the agent looked up — preserved for escalation reuse

class CleaningAgent:
    """
    Cleans records for ONE country. Self-contained: holds its own message history,
    its own system prompt, its own tool list. Knows nothing about siblings, the
    orchestrator, or the database. Pure function from records → CleaningOutputs.
    """

    def __init__(
        self,
        country_code: str,                    # 'CA', 'USA', 'NL', 'MX', 'JP'
        system_prompt: str,                   # built by prompts.build_system_prompt(country_code, schema)
        research_prompt_builder: Callable,    # prompts.research.build_research_prompt(country_code, table)
        tools: list[dict],                    # tool definitions this agent can call
        llm_client: LLMClient,                # one of clients.standard
        web_cache: WebSearchCache,            # shared across agents — dedupes Tavily calls
        escalator: EscalationAgent,           # injected; called when a record can't be resolved
        max_rounds: int = 20,                 # rescue cap (load-bearing for gpt-oss)
    ): ...

    def process(self, records: list[dict]) -> list[CleaningOutput]:
        """
        Run the research loop for this batch (already filtered to one country).
        For each record that comes back LOW confidence or unresolved, call
        self.escalator.investigate(...) and merge.
        Returns one CleaningOutput per input record.
        """
```

**Owns** (encapsulated, never leaks):
- Its own `messages` list. No shared state across siblings.
- Its own `search_count`, `tool_round` counters.
- Its own rescue path (force-final-output if `max_rounds` exhausted).

**Receives via constructor** (so they can be swapped/mocked):
- The LLM client, web cache, escalator, tools, prompts.

**Returns**: a flat list of `CleaningOutput`s. Does *not* mutate shared state. Does *not* write to the DB. The orchestrator persists.

**Anti-patterns explicitly avoided**:
- Shared mutable conversation state across countries (the current `try/finally` system_prompt swap pattern).
- Country branching inside the agent loop (current `country_scope or 'CA'` defaults).

### 5.2 `EscalationAgent` — hard cases

```python
# cleaning/escalation.py

class EscalationAgent:
    """
    Investigates a single record that the country agent couldn't resolve.
    Receives the parent's search_log so it doesn't repeat searches.
    Returns an updated CleaningOutput + flags. Does NOT touch the DB.
    """
    def __init__(self, llm_client: LLMClient, web_cache: WebSearchCache,
                 tools: list[dict], max_rounds: int = 10): ...

    def investigate(
        self,
        record: dict,
        country_code: str | None,           # may be None — that's the case to investigate
        flag_hints: list[FlagType],         # what to focus on
        prior_search_log: list[SearchHit],  # don't redo these
    ) -> CleaningOutput: ...
```

**Triggered by `needs_escalation(output: CleaningOutput) -> list[FlagType]`** (in `cleaning/agent.py`), a pure function returning the list of flag types so the escalator knows what to investigate. Conditions:

- `country` empty or not in `VALID_COUNTRIES` after pre-clean → `UNKNOWN_COUNTRY`
- Postal code `N/A` or marked `?` after research → `POSTAL_UNRESOLVED`
- Municipality `N/A` after research → `MUNICIPALITY_UNRESOLVED`
- `validation_notes` contain confidence `LOW` → `LOW_CONFIDENCE_RESEARCH`
- Cross-region mismatch (e.g. Canada postal first letter doesn't match province) → `CROSS_REGION_MISMATCH`
- Multiple FSA/ZIP candidates with no confident winner → `POSTAL_AMBIGUOUS`

**Key behaviors**:
- **Per-record, not per-batch.** Expensive per call but rare (~5% of records).
- **Prior-search-log injection.** Escalator starts with the parent's transcript in its messages, then prompts itself to resolve `flag_hints` without re-running prior searches.
- **Returns flags whether it succeeds or not.** Even successful resolution carries `RESOLVED_AFTER_ESCALATION` (severity `INFO`) so the audit trail records that this record needed extra work.
- **Uses `clients.deep`** — the strongest tier — because escalation handles the cases that most benefit from stronger reasoning.

### 5.3 `flags` table + `FlagType` enum

```sql
CREATE TABLE flags (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    raw_data_id     INTEGER NOT NULL,
    cleaned_data_id INTEGER,                                 -- nullable; flag may exist before cleaned row
    flag_type       TEXT NOT NULL,                           -- enum string, see FlagType
    severity        TEXT NOT NULL,                           -- 'INFO' | 'WARN' | 'NEEDS_REVIEW' | 'BLOCKED'
    reason          TEXT NOT NULL,                           -- free-text explanation
    raised_by       TEXT NOT NULL,                           -- 'pre-cleaner' | 'agent:CA' | 'escalator' | 'guardrail'
    raised_at       TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    resolved_at     TIMESTAMP,
    resolved_by     TEXT,
    resolution_note TEXT,
    FOREIGN KEY (raw_data_id) REFERENCES raw_data(id),
    FOREIGN KEY (cleaned_data_id) REFERENCES cleaned_data(id)
);
CREATE INDEX idx_flags_unresolved ON flags(resolved_at) WHERE resolved_at IS NULL;
```

```python
class FlagType(str, Enum):
    UNKNOWN_COUNTRY            = 'unknown_country'
    CROSS_REGION_MISMATCH      = 'cross_region_mismatch'
    POSTAL_UNRESOLVED          = 'postal_unresolved'
    POSTAL_AMBIGUOUS           = 'postal_ambiguous'
    MUNICIPALITY_UNRESOLVED    = 'municipality_unresolved'
    LOW_CONFIDENCE_RESEARCH    = 'low_confidence_research'
    GUARDRAIL_BLOCKED          = 'guardrail_blocked'
    RESOLVED_AFTER_ESCALATION  = 'resolved_after_escalation'
```

A separate table (not a boolean column on `cleaned_data`) is required because a single record can carry multiple flags simultaneously (e.g. cross-region mismatch *and* low-confidence research). A boolean would lose information; analytics queries (`SELECT flag_type, COUNT(*) FROM flags WHERE resolved_at IS NULL GROUP BY flag_type`) become trivial.

### 5.4 `WebSearchCache`

```python
# cleaning/cache.py

class WebSearchCache:
    """
    Dedupes Tavily calls within a workflow run. Keyed by normalized query.
    In-memory by default; SQLite-backed extension point if needed.
    Thread-safe (matters when A migration enables parallel agents).
    """
    def get(self, query: str) -> str | None: ...
    def put(self, query: str, result: str) -> None: ...
    def web_search_cached(self, query: str, max_results: int = 5) -> str: ...
    def stats(self) -> dict:  # {hits, misses, queries_cached}
        ...
```

**Key behaviors**:
- Query normalization: lowercase, collapse whitespace, strip trailing punctuation. So `"M6H Toronto postal"` and `"m6h  toronto  postal "` hit the same entry.
- Shared across all agents in a workflow run. Constructed once by the orchestrator, injected into every `CleaningAgent` and the `EscalationAgent`.
- Per-run lifetime by default (in-memory dict). SQLite-backed extension is a one-class addition with the same interface.
- Errors from Tavily are *not* cached — failed calls retry on next request.
- Stats logged at end of run as part of `CleaningRunReport.cache_stats`.
- **Thread-safe from day one.** `get`/`put`/`web_search_cached` guard mutation with a `threading.Lock`. The cache is single-threaded under C-Hybrid, but the lock is required from the start so that the A migration's parallelism is safe without changing this class.

**Replaces**: the `web_search` function currently at module scope in `multi_turn_conversation.py`. The tool definition Claude sees is unchanged; only the implementation routes through the cache.

### 5.5 Tiered LLM clients

```python
# cleaning/llm_client.py

@dataclass
class LLMClient:
    sdk: Anthropic                  # the underlying SDK (always the Anthropic SDK shape)
    model: str
    supports_cache_control: bool    # True for haiku-or, anthropic-*; False for gpt-oss
    base_url: str | None

    def messages_create(self, *, system, messages, tools, max_tokens=2048):
        """
        Single call surface. Adds cache_control breakpoints to system + tools blocks
        when supports_cache_control=True. Silently omits them otherwise.
        Retries 3× with backoff on transient errors. Raises LLMUnavailableError on persistent failure.
        """

@dataclass
class Clients:
    fast:     LLMClient   # cheap, fast — query interpretation
    standard: LLMClient   # the workhorse — per-country research agents
    deep:     LLMClient   # slow, smart — escalation sub-agent

def build_clients() -> Clients:
    """
    Reads env and constructs a tiered client bundle. Each tier is independently
    configured. All tiers default to LLM_BACKEND_DEFAULT if not overridden, so a
    learning setup uses one env var; production tunes per tier.
    """
```

**Env config**:

```bash
LLM_BACKEND_DEFAULT=gpt-oss            # fallback for any tier not set
LLM_BACKEND_FAST=gpt-oss               # optional override
LLM_BACKEND_STANDARD=haiku-or          # optional override
LLM_BACKEND_DEEP=anthropic-sonnet      # optional override

# Recognized backend tokens:
#   gpt-oss              → OpenRouter, openai/gpt-oss-20b:free
#   haiku-or             → OpenRouter, anthropic/claude-haiku-4.5
#   anthropic-haiku      → api.anthropic.com, claude-haiku-4-5-20251001
#   anthropic-sonnet     → api.anthropic.com, claude-sonnet-4-6
#   anthropic-opus       → api.anthropic.com, claude-opus-4-7
```

**Tier-to-component assignment** (orchestrator-controlled, fixed at construction):

| Component | Tier | Rationale |
|---|---|---|
| `interpret_query` | `fast` | One-shot keyword extraction. |
| `CleaningAgent` (per-country research) | `standard` | Bulk of the work; Haiku-class is the sweet spot. |
| `EscalationAgent` | `deep` | Few calls, multi-step reasoning, premium pays off on the small slice. |
| `AdHocConversation` (REPL chat) | `standard` | Mostly CRUD/query; Haiku-class fits. |
| Pre-cleaner | none | Pure Python. |

**Staged plan rendered as env config**:

| Stage | LLM_BACKEND_FAST | LLM_BACKEND_STANDARD | LLM_BACKEND_DEEP |
|---|---|---|---|
| Now (learning) | gpt-oss | gpt-oss | gpt-oss |
| Vetting | gpt-oss | haiku-or | haiku-or |
| Production | anthropic-haiku | anthropic-haiku | anthropic-sonnet |

**Caching behavior**:
- `cache_control={"type": "ephemeral"}` is added to the `system` block and the `tools` block when `supports_cache_control=True`. Portable form (works on OpenRouter→Anthropic and native Anthropic).
- 4096-token threshold check is performed **at startup**, not per-request. When `build_clients()` constructs a tier with `supports_cache_control=True`, it estimates the token count of the system+tools blocks once and logs a startup-time warning if under threshold (`"system+tools for tier <name> below 4096 tokens — caching will not engage on Haiku 4.5"`). Token estimation uses `len(text) // 4` as a fast approximation; precise tokenization is not worth the dependency. Per-request runtime checks are not performed.
- Top-level `cache_control` kwarg from the current code is removed (silent no-op).

### 5.6 Public API + REPL

```python
# cleaning/__init__.py

from cleaning.orchestrator import run_cleaning_workflow
from cleaning.types import CleaningRunReport, CleaningOutput
from cleaning.flags import Flag, FlagType
from cleaning.conversation import AdHocConversation

__all__ = ['run_cleaning_workflow', 'CleaningRunReport', 'CleaningOutput',
           'Flag', 'FlagType', 'AdHocConversation']
```

```python
def run_cleaning_workflow(
    query: str,
    *,
    country_override: str | None = None,   # 'CA' | 'USA' | 'NL' | 'MX' | 'JP' | None
    db_path: str | None = None,            # defaults to config.DB_PATH
    clients: Clients | None = None,        # defaults to build_clients()
) -> CleaningRunReport: ...

@dataclass
class CleaningRunReport:
    records_processed: int
    cleaned_count: int
    flagged_count: int
    flags_by_type: dict[FlagType, int]
    cache_stats: dict                      # {hits, misses, queries_cached}
    timing: dict                           # per-step seconds
    flag_summary: list[dict]               # one line per flag for the report
    errors: list[dict]                     # per-record persistence failures
    summary_text: str                      # human-readable report
```

The function is **synchronous, no globals, no print statements**. All printing happens in the REPL/CLI wrapper. Tests assert on the dataclass; scripts persist or chart it.

**REPL** (`multi_turn_conversation.py`, ~120 lines, kept at project root for backward compatibility):

```python
def main():
    clients = build_clients()
    convo = AdHocConversation(clients=clients, db_path=DB_PATH)
    while True:
        cmd = read_multiline()
        if cmd == 'QUIT':              break
        if cmd == 'HISTORY':           convo.show_history(); continue
        if cmd.startswith('CLEAN'):
            report = run_cleaning_workflow(cmd[5:].strip() or '', clients=clients)
            print(report.summary_text)
            continue
        # everything else → ad-hoc conversation (the tool-use loop)
        print(convo.send(cmd))
```

`display_message`, `get_multiline_input`, `show_conversation_history` helpers live here too. No business logic.

### 5.7 `AdHocConversation`

The cleaned-up version of today's `send_message` loop. Separate from the cleaning workflow because it serves a different purpose: ad-hoc questions like "show me record 5", "delete record 12", "insert this new contact."

```python
# cleaning/conversation.py

class AdHocConversation:
    def __init__(self, *, clients: Clients, db_path: str): ...
    def send(self, user_input: str) -> str: ...
    def show_history(self) -> None: ...
```

Uses `clients.standard` (Haiku-tier — straightforward CRUD/query). Owns its own message history.

### 5.8 Per-component tool lists

| Component | Tools given |
|---|---|
| `CleaningAgent` (research) | `[web_search]` only |
| `EscalationAgent` (hard cases) | `[web_search]` only |
| `AdHocConversation` (REPL chat) | `[web_search, query_records, insert_record, update_record, delete_record]` |

**Rationale for restricting cleaning agents to `web_search`**: today every Claude call gets the full tool list including CRUD. A cleaning agent could write to the DB itself, bypassing the orchestrator's save path, the audit log, the merge step, and the flags pipeline. This actively undermines deterministic persistence, the audit trail, and reproducibility. By restricting the cleaning agents to `web_search` only, the orchestrator becomes the sole writer.

CRUD tool implementations and `_build_table_properties` / `_column_names` move into `cleaning/conversation.py` (only used by `AdHocConversation` now).

## 6. Data flow (end-to-end, mixed batch)

User: `CLEAN all uncleaned data`

1. **interpret_query** (`clients.fast`) → `{country_filter: None, scope: 'all_uncleaned'}`. No country override.
2. **fetch_records** → 100 records: 40 CA, 25 USA, 20 NL, 10 MX, 5 unknown.
3. **pre_clean_batch** → all 100 normalized (name/city/case/abbrev/phone/postal-spacing). 30 records fully resolved by Python (postal complete + municipality present), 70 need research, 5 of those have no country.
4. **group_by_country** → `{'CA': [...32], 'USA': [...18], 'NL': [...12], 'MX': [...8], None: [...5]}`. Records grouped after pre-clean; the 30 fully-resolved skip step 5 entirely.
5. **For each non-None group, instantiate `CleaningAgent(country=…, llm_client=clients.standard, …)` and call `.process(group)`**. Each agent runs its own research loop with the per-country prompt and `web_search` tool. **Inside `.process()`**, after the research loop completes for each record, the agent itself calls `needs_escalation(output)` and, if non-empty, calls `self.escalator.investigate(...)` with `prior_search_log` so it doesn't re-run searches. Cache hits dedupe shared FSA/ZIP lookups across records. The 5 unknown-country records skip the per-country agents and go straight to a dedicated escalation pass driven by the orchestrator.
6. **merge_results** → per-record final dict, combining pre-cleaner output with the agent's CleaningOutput (which may already include escalation results).
7. **persist** → for each record, one transaction writing `cleaned_data` + any `flags` + the `audit_log` rows. Failures roll back that record only.
8. **Return `CleaningRunReport`** with counts, timing, cache stats, flags-by-type, and human-readable summary.

**Escalation trigger lives in the agent, not the orchestrator.** The agent owns the search log and is best positioned to hand it off to the escalator without re-serialising. The orchestrator's only escalation responsibility is the unknown-country case (records with no country → no per-country agent → orchestrator dispatches them to the escalator directly).

REPL prints `report.summary_text`.

## 7. Error handling

| Failure | Behavior |
|---|---|
| Tavily API error | Error string returned to model as tool result; not cached; retried next request. |
| LLM request fails (network/5xx) | `LLMClient` retries 3× with backoff. Persistent failure raises `LLMUnavailableError`; orchestrator records failure in `CleaningRunReport.errors` and continues with remaining batches. |
| Tool execution raises | Caught in tool dispatcher, returned as error string to model. Exception logged. |
| Model output unparseable | Records get `LOW_CONFIDENCE_RESEARCH` flag (reason: `"model output unparseable"`); escalated. |
| `max_rounds` hit on agent | Force-final-output rescue runs (load-bearing for gpt-oss). If rescue still fails to produce parseable output, batch escalates. |
| Escalation also fails | Record persisted with pre-cleaner values only; flags `POSTAL_UNRESOLVED` / `MUNICIPALITY_UNRESOLVED` at severity `NEEDS_REVIEW`. Nothing lost. |
| DB write fails | One transaction per record (`cleaned_data` + flags + audit_log together). Failure rolls back that record only; orchestrator continues. |
| Guardrail block on AdHoc CRUD | Returned as `"GUARDRAIL BLOCKED: ..."` string + `GUARDRAIL_BLOCKED` flag persisted. |
| Unknown country | `UNKNOWN_COUNTRY` flag raised at routing; record sent straight to escalator (which tries to infer country from address/city/postal pattern). |

**Principle: nothing is silently dropped.** Every unresolvable case lands in the `flags` table with `NEEDS_REVIEW` or higher severity. `CleaningRunReport.flags_by_type` makes this visible.

## 8. Testing strategy

### 8.1 Unit tests (no LLM, no network, no DB) — fast, run on every change

- All `pre_cleaner` functions (expand existing).
- All `guardrails` functions.
- `WebSearchCache` (normalization, hits, misses, stats, error non-caching).
- `Flag` dataclass + `FlagType` enum.
- `needs_escalation` predicate (golden cases per flag type).
- `interpret_query`, `group_by_country`, `detect_country_filter`.
- `parse_research_response` against golden fixtures (well-formed, malformed, empty).
- `merge_results` against fixtures.

### 8.2 Integration tests (in-memory SQLite, mocked LLM) — catch wiring bugs

- Full pre-clean → group → mock-research → merge → persist for a mixed batch.
- Flag persistence end-to-end (raise → query → resolve).
- `CleaningAgent.process` with `LLMClient.messages_create` mocked to return canned tool-use sequences.
- `EscalationAgent.investigate` with prior-search-log injection verified (no duplicate searches).
- `CleaningRunReport` shape and counts.

### 8.3 Live LLM tests (gated by env var, opt-in) — periodic, manual

- One golden record per country processed end-to-end against `LLM_BACKEND_DEFAULT=gpt-oss`.
- Skipped by default (`pytest -m llm` to run).
- Asserts cleaned values are *plausible*: fuzzy match on municipality, exact on postal format.

### 8.4 Mocking boundaries

- `LLMClient.messages_create` — single boundary for all LLM mocking.
- `WebSearchCache._tavily_call` — single boundary for all Tavily mocking.
- DB uses in-memory `:memory:` SQLite.

Pre-existing tests in `tests/` are reviewed and ported. Tests targeting removed functions are deleted.

## 9. Migration boundary discipline

Three properties of this design make the future C → A migration mechanical (see `2026-04-27-data-cleaning-a-migration-design.md`):

1. **`CleaningAgent` is country-fixed at construction time** — no internal branching on country.
2. **No shared mutable state across agents** — each owns its own messages, counters, search log.
3. **Agents return data; the orchestrator persists** — agents never write to the DB directly.

If any of these three is violated during implementation, the migration cost rises sharply. They are non-negotiable design invariants.

## 10. Implementation sequence (high-level, for the writing-plans step)

The implementation plan will refine ordering, but the natural sequence is:

1. New `cleaning/` subpackage skeleton + move `pre_cleaner.py` (no behavior change yet).
2. Add `flags` table to `database.py`; add `flags` CRUD to `db_helpers.py`; build `cleaning/flags.py`.
3. Build `cleaning/llm_client.py` with `build_clients()` and tiered config.
4. Build `cleaning/cache.py` (`WebSearchCache` + the moved Tavily call).
5. Build `cleaning/types.py` (shared dataclasses).
6. Build `cleaning/agent.py` (`CleaningAgent` + `needs_escalation` predicate).
7. Build `cleaning/escalation.py` (`EscalationAgent`).
8. Build `cleaning/orchestrator.py` (interpret/fetch/group/dispatch/merge/persist + `run_cleaning_workflow`).
9. Build `cleaning/conversation.py` (`AdHocConversation` + CRUD tools).
10. Rewrite `multi_turn_conversation.py` as the thin REPL wrapper.
11. Delete dead code (`data_cleaning/`, debug scripts, removed phone tools, `data_cleaning_agent.py`, `detect_country_scope`).
12. Port and expand tests.
13. Final smoke test against gpt-oss-20b on a mixed-country batch.
