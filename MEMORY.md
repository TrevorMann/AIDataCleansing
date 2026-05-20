# Project Memory

Decisions, rationale, and known trade-offs. Updated when architecture choices are made.

---

## Architecture Decisions

### AD-1: Two entry points — CLI and programmatic API

**Decision (2026-05-20):** Keep `multi_turn_conversation.py` as the interactive CLI for human-driven testing and step-through debugging. Keep `cleaning/orchestrator_v2.OrchestrationTeam` as the programmatic API for applications and batch processing.

**Rationale:** The two serve different audiences. `OrchestrationTeam` has no interactive loop, no tool dispatch UI, and no conversation history — it is a clean function call. `multi_turn_conversation.py` wraps the same underlying skills with a conversational layer suitable for manual testing.

**Implication:** When adding features, implement them in the skill or orchestrator first, then wire up CLI exposure in `multi_turn_conversation.py`. Never add logic only to the CLI.

---

### AD-2: `cleaning/` is the shared core library, not legacy

**Decision (2026-05-20):** The `cleaning/` directory is the shared core library for the pipeline. It is not legacy, despite the generic name. It contains:
- `orchestrator_v2.py` — primary pipeline runner
- `llm_client.py` — Anthropic SDK wrapper
- `cache.py` — web search cache
- `spell_corrections_data.py` — DB-backed spell loader
- `municipality_resolver.py`, `nominatim_client.py`, `confidence_scorer.py`

Only `municipality_data.py` and `flags.py` are dead within it (see AD-5).

---

### AD-3: Both SQLite and PostgreSQL are supported backends

**Decision (2026-05-20):** SQLite for local development, PostgreSQL for production.

**Implication:** The `db/` module must maintain both `db/sqlite_*` and `db/pg_*` files. Any new DB-touching feature needs to work via `db/connection.py` backend switch, not hard-code Postgres. Tests should not require a live database.

---

### AD-4: No hardcoded data in skill source files

**Rationale:** Hardcoded domain dictionaries (spell corrections, FSA→municipality, query templates) break multi-domain reuse. All domain data lives in DB seeded via the seeder framework. Lock tests in `tests/cleaning/test_spell_corrections.py` and `tests/test_full_agent_pipeline.py` enforce this.

---

### AD-5: Legacy root-level DB files are deprecated, not deleted

**Decision (2026-05-20):** `database.py`, `db_helpers.py`, `schema_discovery.py` (root-level) duplicate functionality in `db/`. They are not deleted immediately because `multi_turn_conversation.py` imports from them and that modernization work is tracked separately (see Known Debt below).

**Target state:** After `multi_turn_conversation.py` is modernized to import from `db/`, delete the root-level files.

---

### AD-6: `WebSearchCache` is the canonical web search cache

**Decision (2026-05-20):** `cleaning/cache.py::WebSearchCache` is the canonical implementation. It is injected into skills as the `web_cache` runtime parameter. The `web_search_enricher` skill calls `self.cache.get_or_search(query)`.

**Bug note:** As of 2026-05-20 there is a method name mismatch — `WebSearchCache` has `web_search_cached()` but `web_search_enricher` calls `get_or_search()`. Fix: add `get_or_search` alias on `WebSearchCache`. See `docs/code-review-2026-05-20.md` BUG-1.

---

### AD-7: `prompts/research.py` country notes are manually maintained

**Known limitation:** `research.py` country-specific notes (`_CANADA_RESEARCH_NOTES`, etc.) are manual copies of rules from `prompts/domains/<domain>/<cc>.py`. They will drift. When editing postal or municipality logic in a country file, also update `research.py`.

**Long-term plan:** Extract `SHORT_RULES` from each country file and have `research.py` import them, eliminating the duplication. Not yet scheduled.

---

### AD-8: `guardrails.py` country list is domain-coupled

**Bug/debt (2026-05-20):** `guardrails.py:12` hardcodes `VALID_COUNTRIES = {'CA', 'USA', 'NL', 'MX', 'JP'}` which is the real-estate domain's country set. The guardrails file is shared by `multi_turn_conversation.py`. When sports ticketing or other domains are added to the CLI, this list will incorrectly reject valid countries.

**Fix:** Remove the hardcoded set. Pass valid countries from the domain's configuration at runtime.

---

## Known Technical Debt

| ID | File | Issue | Priority |
|----|------|-------|----------|
| TD-1 | `cleaning/cache.py` | `get_or_search()` method missing — production web search silently fails | High |
| TD-2 | `cleaning/llm_client.py` | `build_client_for_tier()` missing — skill planner lazy init fails at runtime | High |
| TD-3 | `scope_interpreter.py:4` | Imports from legacy `schema_discovery` instead of `db.sqlite_schema_discovery` | Medium |
| TD-4 | `guardrails.py:12` | Hardcoded real-estate country list, not domain-agnostic | Medium |
| TD-5 | `multi_turn_conversation.py` | Imports from 6 legacy root-level modules; modernization deferred | Medium |
| TD-6 | `database.py`, `db_helpers.py`, `schema_discovery.py` | Root-level DB layer duplicates `db/`; kept until TD-5 done | Low |
| TD-7 | `prompts/research.py` | Country notes drift from country files; no automated sync | Low |
| TD-8 | One-line re-export files in `skills/real_estate/` | Wrapper files add indirection; may be removable if `skills.yaml` can reference `_common` directly | Low |

---

## Dead Code Removed

| File | Deleted | Reason |
|------|---------|--------|
| `data_cleaning_api_test.py` | 2026-05-20 | Manual API test from dev, replaced by `tests/` |
| `debug_api.py` | 2026-05-20 | Debug script from early dev |
| `test_direct.py` | 2026-05-20 | HTTP endpoint test from early dev |
| `test_sdk.py` | 2026-05-20 | SDK format test from early dev |
| `setup_sample_data.py` | 2026-05-20 | Hardcoded 16-record seeder, replaced by seeder framework |
| `data_cleaning/` (dir) | 2026-05-20 | Demo CLI workflow, never integrated |
| `cleaning/municipality_data.py` | 2026-05-20 | Shapefile stubs with zero callers |
| `cleaning/flags.py` | 2026-05-20 | Abandoned prototype, broken imports, zero callers |
| `data.sqlite` | 2026-05-20 | Empty file in project root |

---

## Test Coverage Notes

- Tests use mocks — no real DB or API keys required.
- System Python (3.12) lacks `psycopg`, `anthropic`, `pydantic` — 4 tests require the project venv (`.venv-win` on Windows). All 248 other tests pass in system Python.
- `multi_turn_conversation.py` — 31 tests added 2026-05-20 covering all tool dispatch and guardrail integration.
- `cleaning/cache.py` — 22 tests added 2026-05-20 covering cache hit/miss, PG layer, error handling, `get_or_search` alias.
- `guardrails.py` — 57 tests added 2026-05-20 covering every exported function, positive and negative cases.
- `test_multi_turn.py` applies patches at module import time (not in fixtures) due to module-level side effects in `multi_turn_conversation.py`.

## Schema Discovery Consolidation (2026-05-20)

Created `db/schema_discovery.py` as a unified interface that dispatches to
`db.sqlite_schema_discovery` or `db.pg_schema_discovery` based on `DB_BACKEND`.
Callers import from `db.schema_discovery` — backend is transparent.

Updated `scope_interpreter.py` and `multi_turn_conversation.py` to use this
unified import. The root-level `schema_discovery.py` is now fully bypassed by
production code (kept until multi_turn modernization is considered complete).
