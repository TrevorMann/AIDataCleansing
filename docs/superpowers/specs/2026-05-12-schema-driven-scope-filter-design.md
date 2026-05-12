# Schema-Driven Scope Filter

**Date:** 2026-05-12
**Status:** Approved

## Problem

`multi_turn_conversation.py` hardcodes country routing (`detect_country_scope`, `handle_canada_cleaning`, etc.) and `data_cleaning_agent.py` hardcodes a `country_map` for query interpretation. Both couple the CLI and agent to the real_estate domain. When `country_scope` is None, `_run_cleaning_workflow` defaults to `'CA'` rules, causing model refusals when non-CA data is processed.

The framework is multi-industry. Sports ticketing filters by event type, ticket product, or venue — not country. Any hardcoded field name in the CLI is wrong.

## Solution

Replace hardcoded filter logic with a new `ScopeInterpreter` that calls the LLM with the domain's schema metadata and the user's query to produce a filter dict. The LLM handles all natural language variation ("US", "American", "NBA games", "premium tickets") without alias tables or hardcoded field names.

## Architecture

```
User: "CLEAN NBA games"
         │
         ▼
  ScopeInterpreter                ← new module, one LLM call (haiku)
  schema metadata + user query
         │
         ▼
  filter dict                     e.g. {"event_type": "NBA"}
  {} = all records
  None = query specified something unresolvable
         │
     None? → return "no data found to clean based on your ask"
         │
         ▼
  fetch_data_for_query(filter)    ← unchanged
         │
     0 records? → "no data found to clean based on your ask"
         │
         ▼
  derive country_scope from filter
  using sub_category_dimension in domain registry
         │
         ▼
  pre-clean → research (with correct prompt sub-category rules)
```

## Components

### `scope_interpreter.py` (new)

```python
class ScopeInterpreter:
    def __init__(self, client, backend, model): ...

    def interpret(self, user_query: str, domain: str, db_path: str) -> dict | None:
        """
        Returns:
          {"field": "value"}  — filter found
          {}                  — all records / no filter specified
          None                — user specified something unresolvable in schema
        """
```

**Prompt:**
- System: extract DB filter criteria from natural language; return valid JSON only; return `{}` for all-records requests; only use fields that exist in the schema
- User: `Schema:\n{column_metadata}\n\nUser request: {user_query}`

**Model config:** uses `_CLIENT`/`_BACKEND`/`_MODEL` from caller (same factory as everything else). One round, no tools, `max_tokens=200`. Calls `log_usage` per `feedback_llm_api_patterns`.

**Schema context:** `format_schema_for_prompt(db_path, domain)` — already filters to domain-relevant columns via `column_metadata`. No new DB queries.

### `multi_turn_conversation.py` (modified)

**Step 1 in `_run_cleaning_workflow`:**
```python
# Step 1a: LLM scope interpretation
scope_filter = ScopeInterpreter(_CLIENT, _BACKEND, _MODEL).interpret(
    user_query, _ACTIVE_DOMAIN, DB_PATH
)
if scope_filter is None:
    return "no data found to clean based on your ask"

# Step 1b: scope/limit modifiers (keep existing — all_uncleaned, first_batch, limit)
filters = agent.interpret_user_query(user_query)
filters.update(scope_filter)
```

**Sub-category derivation** (replaces `or 'CA'` defaults and auto-detect block):
```python
if not country_scope:
    sub_dim = _DOMAIN_CONFIG.get('sub_category_dimension')  # e.g. "country"
    if sub_dim and sub_dim in scope_filter:
        country_scope = scope_filter[sub_dim]  # "USA" from {"country": "USA"}
    # else: None — generic research prompt, no country-specific rules
```

**Dead code removed:**
- `detect_country_scope` function
- `handle_canada_cleaning`, `handle_usa_cleaning`, `handle_europe_cleaning`, `handle_mexico_cleaning`, `handle_japan_cleaning`
- `or 'CA'` defaults on lines building `research_prompt` and `research_system`
- Auto-detect country from records block (added in prior session, replaced by sub_dim derivation)

### `data_cleaning_agent.py` (modified)

`interpret_user_query` — remove:
- `country_map` dict and its loop
- `re.search(r'\bus\b')` check (added in prior session, now handled by ScopeInterpreter)

Keep:
- Scope detection: `all_uncleaned`, `first_batch`, `limit`
- Issue-type detection: `phone`, `postal`, `name`

### `prompts/research.py` (minor)

Change `build_research_prompt(country_scope: str, ...)` → `build_research_prompt(country_scope: str | None, ...)`. Already handles `None` correctly via `_COUNTRY_NOTES.get(country_scope, "")`.

## Data Flow Example

**"CLEAN US data"** (real_estate domain):
1. ScopeInterpreter sees schema with `country TEXT — country of the listing`
2. LLM returns `{"country": "USA"}`
3. `fetch_data_for_query({"country": "USA"})` → fetches US records
4. `sub_category_dimension = "country"` → `country_scope = "USA"`
5. Research uses USA prompt rules

**"CLEAN NBA games"** (sports_ticketing domain, future):
1. ScopeInterpreter sees schema with `sport_type TEXT — sport category`
2. LLM returns `{"sport_type": "NBA"}`
3. Fetches NBA records
4. `sub_category_dimension = "sport_type"` → `country_scope = "NBA"` (maps to sport-specific prompt rules)

**"CLEAN all data"**:
1. LLM returns `{}`
2. No filter applied — all records fetched
3. `scope_filter = {}` so sub_dim check misses → `country_scope = None` → generic research rules (no country-specific prompt)

**"CLEAN purple data"** (nonsense):
1. LLM returns `None`
2. Returns `"no data found to clean based on your ask"` immediately

## What Does Not Change

- All country/domain prompt files (`ca.py`, `usa.py`, `nl.py`, etc.)
- `build_research_prompt`, `build_system_prompt`
- `fetch_data_for_query` filter logic
- `sub_category_dimension` in `domain_registry.json`
- `SkillRegistry`, `OrchestrationTeam`, all skills

## Out of Scope

- Multi-field combined filters (e.g. `country=USA AND status=pending`) — future iteration
- `scope_filter_config` alias table — dropped; LLM handles aliasing naturally
- Caching scope interpretations
