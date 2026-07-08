# Code Review — 2026-05-20

Full codebase audit. Focus: dead code, active bugs, legacy debt, simplification opportunities.
Review covered all ~205 Python/YAML/SQL files; 138 tests verified passing.

---

## 1. Active Bugs (fix before next release)

### BUG-1: `WebSearchCache.get_or_search()` does not exist

**File:** `cleaning/cache.py` + `skills/_common/web_search_enricher/web_search_enricher.py:140`

`web_search_enricher.py` calls `self.cache.get_or_search(query)`, but `WebSearchCache` only exposes `web_search_cached(query)`. Web search is silently skipped in production because the test uses a Mock that auto-creates the method.

**Fix:** Add a `get_or_search` alias on `WebSearchCache`, or rename `web_search_cached` to `get_or_search`.

```python
# In cleaning/cache.py — add:
def get_or_search(self, query: str, max_results: int = 5) -> str:
    return self.web_search_cached(query, max_results)
```

---

### BUG-2: `build_client_for_tier()` does not exist in `cleaning/llm_client.py`

**File:** `skills/_common/skill_planner/skill_planner.py:34-35`

```python
from cleaning.llm_client import build_client_for_tier   # ImportError at runtime
self._llm = build_client_for_tier(self.config.get("tier", "fast"))
```

`cleaning/llm_client.py` exports `build_clients()` (returns `Clients` bundle) but not `build_client_for_tier`. The skill planner cannot run its lazy initialization path.

**Fix:** Add `build_client_for_tier` to `cleaning/llm_client.py`:

```python
def build_client_for_tier(tier: str) -> LLMClient:
    if tier not in ("fast", "standard", "deep"):
        raise ValueError(f"Unknown tier: {tier!r}. Valid: fast, standard, deep")
    return getattr(build_clients(), tier)
```

---

### BUG-3: `cleaning/flags.py` imports functions that don't exist in `db_helpers.py`

**File:** `cleaning/flags.py:11`

```python
from db_helpers import insert_flag, query_flags   # neither function exists
```

`db_helpers.py` has no `insert_flag` or `query_flags` function. This file cannot be imported. However, it has zero callers — see Dead Code section.

---

## 2. Dead Code (safe to delete)

These files have zero callers and were one-off development scripts or abandoned prototypes.

| File | Why safe to delete |
|------|-------------------|
| `data_cleaning_api_test.py` | Manual one-off test of OpenRouter API. Not imported by anything. Replaced by `tests/`. |
| `debug_api.py` | Debug script from early development, writes to `debug_output.txt`. Not imported. |
| `test_direct.py` | HTTP test of OpenRouter endpoint. Not imported. |
| `test_sdk.py` | SDK endpoint format test. Not imported. |
| `setup_sample_data.py` | Inserts 16 hardcoded records. Replaced by `scripts/init_data.py` + seeder framework. |
| `data_cleaning/clean_data_workflow.py` | Menu-driven demo CLI, duplicates `multi_turn_conversation.py` logic. Not imported. |
| `cleaning/municipality_data.py` | Shapefile/FSA loading stubs. Zero callers. Replaced by `seeders/real_estate/wikipedia_fsa.py`. |
| `cleaning/flags.py` | Typed flag system prototype. Zero callers. Imports functions that don't exist in `db_helpers`. |
| `data.sqlite` | Empty (0 bytes) file in project root. |

**Action:** Delete all of the above. Run `python3 -m pytest tests/ -v` after each deletion to confirm no hidden dependencies.

---

## 3. Legacy Debt — Deprecate with Plan

These files work but belong to a pre-`skills/` architecture. They share a root-level DB layer (`database.py`, `db_helpers.py`, `schema_discovery.py`) that duplicates the modern `db/` module.

### Priority ordering

**P1 — Fix before adding features:**

| File | Issue | Migration target |
|------|-------|-----------------|
| `scope_interpreter.py:4` | Imports `from schema_discovery import format_schema_for_prompt` (root-level legacy) | `from db.sqlite_schema_discovery import format_schema_for_prompt` |
| `guardrails.py:12` | `VALID_COUNTRIES = {'CA', 'USA', 'NL', 'MX', 'JP'}` hardcodes real-estate countries. Not domain-agnostic. | Move valid-country list to domain config or pass in from caller |

**P2 — Migrate when touching `multi_turn_conversation.py`:**

`multi_turn_conversation.py` is the interactive CLI entry point (keep and modernize per decision). It currently imports six legacy modules. When modernizing, replace these imports:

| Current import | Replace with |
|----------------|-------------|
| `from database import get_db_connection, init_db` | `from db.connection import get_db_connection` + `from db.sqlite_init import init_db` |
| `from db_helpers import ...` | `from db.sqlite_helpers import ...` |
| `from schema_discovery import format_schema_for_prompt` | `from db.sqlite_schema_discovery import format_schema_for_prompt` |
| `from data_cleaning_agent import DataCleaningAgent` | Use `cleaning.orchestrator_v2.OrchestrationTeam` directly |
| `from validate_data_quality import ...` | Inline the two checks used, or move to `db/` |
| `from skill_router import detect_skill, load_skill` | Either wire to `SkillRegistry` or keep if Claude IDE skill injection is needed |

**P3 — Eventually consolidate:**

| File | Status | Notes |
|------|--------|-------|
| `database.py` | Legacy — duplicates `db/sqlite_init.py` | 11 callers, all via P2 migration |
| `db_helpers.py` | Legacy — duplicates `db/sqlite_helpers.py` | 8 callers |
| `schema_discovery.py` | Legacy — duplicates `db/sqlite_schema_discovery.py` | 5 callers |
| `data_cleaning_agent.py` | Legacy orchestrator — superseded by `orchestrator_v2.py` | 2 callers |
| `validate_data_quality.py` | Rule-based pre-filter — useful logic but imports legacy DB layer | 2 callers |
| `pre_cleaner.py` | Deterministic regex cleaning — lightweight and correct, but only called by legacy agent | 1 caller |

---

## 4. Simplification Opportunities

### 4a. One-line re-export files — remove if `skills.yaml` can reference `_common` directly

These files exist only as pass-throughs and add indirection with no value:

- `skills/real_estate/address_standardizer/address_standardizer.py` — 1 line: `from skills._common...import AddressStandardizer`
- `skills/real_estate/record_linker/record_linker.py` — 1 line re-export
- `skills/real_estate/spell_checker/spell_checker.py` — 1 line re-export
- `skills/sports_ticketing/record_linker/record_linker.py` — 1 line re-export

**Check:** If `SkillRegistry` loads skills by module path from `skills.yaml`, you can point `real_estate/skills.yaml` directly at `skills._common.spell_checker.spell_checker.SpellChecker` and delete the wrapper files. Verify by checking how `skills.yaml` declares class paths.

### 4b. `cleaning/municipality_resolver.py` → move logic into `MunicipalityAuthorityAgent` skill

`skills/real_estate/municipality_authority/municipality_authority.py` already imports and wraps `MunicipalityResolver`. The resolver is only ever called from that one skill. The two-file indirection adds no value; the resolver logic should live directly in the skill.

### 4c. `db/pg_vector.py` usage check

`cleaning/cache.py` imports `from db.pg_vector import search_cache_lookup, search_cache_store`. Verify `pg_vector.py` still exports those functions (the module was added for vector search; the cache integration may have drifted).

### 4d. `prompts/research.py` known drift problem

Per `CLAUDE.md`: "`research.py` country notes are a manual copy — they will drift from the country files." This is documented technical debt. The long-term fix is extracting `SHORT_RULES` from each country file and having `research.py` import them. Not urgent but worth scheduling.

---

## 5. Test Coverage Gaps

### 5a. `multi_turn_conversation.py` — zero test coverage

The main interactive CLI has no tests. At minimum, add tests for:
- Tool dispatch (validate_phone, web_search, insert/update/delete/query)
- Guardrail enforcement (age range, country, protected fields)
- Two-phase workflow (pre-clean + research round-trip)

### 5b. `guardrails.py` — only tested indirectly via `multi_turn_conversation`

Add a dedicated `tests/test_guardrails.py` with positive/negative cases per function.

### 5c. `cleaning/cache.py` — zero test coverage

`WebSearchCache` has no unit tests. Add tests for: cache hit (no Tavily call), cache miss (Tavily called), PG cache layer, error result not cached, `stats()` accuracy.

### 5d. Test environment notes

Four tests are excluded from CI in the system Python environment due to missing packages:
- `tests/test_metadata_annotation.py` — requires `psycopg`
- `tests/test_scope_interpreter.py` — requires `anthropic`
- `tests/test_skill_registry.py::test_audit_entry_model` — requires `pydantic`
- `tests/test_skill_registry.py::test_baseskill_audit_accumulation` — requires `pydantic`

These pass in the project's `.venv-win` (Windows) environment. They are not code bugs but the README claim of "163 tests, all passing" should be updated to reflect the actual count and environment requirement.

---

## 6. Architecture Observations (not bugs, worth knowing)

### Dual entry points are intentional

| Entry point | When to use |
|-------------|-------------|
| `multi_turn_conversation.py` | Interactive CLI for humans; step-through testing with conversation history |
| `cleaning/orchestrator_v2.OrchestrationTeam` | Programmatic API; embed in apps, batch jobs, tests |

These serve different purposes and should coexist. Modernizing `multi_turn_conversation.py` to import from `db/` and `skills/` will unify the stack without removing the CLI.

### `cleaning/` module is not legacy — it's the core shared library

Despite the directory name, `cleaning/` is actively used:
- `orchestrator_v2.py` — primary pipeline orchestrator
- `llm_client.py` — Anthropic SDK wrapper used by scripts and skills
- `cache.py` — web search cache (once BUG-1 is fixed)
- `spell_corrections_data.py` — DB-backed spell correction loader
- `municipality_resolver.py`, `nominatim_client.py`, `confidence_scorer.py` — supporting services

Only `municipality_data.py`, `flags.py` are dead within it.

---

## Summary Checklist

**Fix now (bugs):**
- [ ] Add `get_or_search()` to `WebSearchCache` in `cleaning/cache.py`
- [ ] Add `build_client_for_tier()` to `cleaning/llm_client.py`
- [ ] Delete `cleaning/flags.py` (broken imports, zero callers)

**Delete (dead code):**
- [ ] `data_cleaning_api_test.py`
- [ ] `debug_api.py`
- [ ] `test_direct.py`
- [ ] `test_sdk.py`
- [ ] `setup_sample_data.py`
- [ ] `data_cleaning/clean_data_workflow.py`
- [ ] `cleaning/municipality_data.py`
- [ ] `data.sqlite`

**Deprecate with plan:**
- [ ] `scope_interpreter.py` — update import to `db.sqlite_schema_discovery`
- [ ] `guardrails.py` — remove hardcoded country list, make domain-configurable
- [ ] `multi_turn_conversation.py` — modernize imports (P2 work)
- [ ] `database.py`, `db_helpers.py`, `schema_discovery.py` — deprecate after P2

**Add tests:**
- [ ] `tests/test_guardrails.py` — positive/negative cases
- [ ] `tests/test_cache.py` — `WebSearchCache` unit tests
- [ ] `tests/test_multi_turn.py` — CLI tool dispatch + guardrail integration
