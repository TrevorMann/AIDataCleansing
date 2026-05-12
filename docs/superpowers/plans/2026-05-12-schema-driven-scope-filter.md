# Schema-Driven Scope Filter Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Replace hardcoded country routing and `country_map` with a `ScopeInterpreter` that calls the LLM with domain schema metadata to produce a filter dict from any natural language request.

**Architecture:** New `scope_interpreter.py` makes a single haiku LLM call (schema + user query → JSON filter dict). `_run_cleaning_workflow` calls it in Step 1a, derives `country_scope` from the result via `sub_category_dimension` in domain config. Dead country routing code is deleted.

**Tech Stack:** Python 3.11+, Anthropic SDK, existing `llm_client_factory`, `schema_discovery.format_schema_for_prompt`, `prompts.domain_registry`

**Spec:** `docs/superpowers/specs/2026-05-12-schema-driven-scope-filter-design.md`

---

## File Map

| File | Action | Responsibility |
|------|--------|----------------|
| `scope_interpreter.py` | **CREATE** | LLM-driven query → filter dict |
| `tests/test_scope_interpreter.py` | **CREATE** | Unit tests for ScopeInterpreter |
| `data_cleaning_agent.py` | **MODIFY** lines 47–69 | Remove `country_map` from `interpret_user_query` |
| `multi_turn_conversation.py` | **MODIFY** multiple | Wire ScopeInterpreter; sub_dim derivation; remove dead code |
| `prompts/research.py` | **MODIFY** line 46 | Fix `country_scope: str` → `str \| None` type hint |

---

## Task 1: Create `scope_interpreter.py` (TDD)

**Files:**
- Create: `scope_interpreter.py`
- Create: `tests/test_scope_interpreter.py`

- [ ] **Step 1: Write the failing tests**

Create `tests/test_scope_interpreter.py`:

```python
import os
import tempfile
from unittest.mock import MagicMock
import pytest
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
```

- [ ] **Step 2: Run tests to verify they fail**

```bash
cd /mnt/f/AI_learning_project && python -m pytest tests/test_scope_interpreter.py -v
```

Expected: `ModuleNotFoundError: No module named 'scope_interpreter'`

- [ ] **Step 3: Create `scope_interpreter.py`**

```python
import json
import logging
from schema_discovery import format_schema_for_prompt
from llm_client_factory import build_message_kwargs, log_usage

logger = logging.getLogger(__name__)

_SYSTEM = (
    "You extract database filter criteria from natural language requests. "
    "Return ONLY valid JSON — a single object with field/value pairs. "
    "Return {} if the user wants all records or specifies no filter. "
    "Return null if the user specifies a filter that cannot be mapped to any schema field. "
    "Use only field names that appear in the provided schema."
)


class ScopeInterpreter:
    def __init__(self, client, backend: str, model: str):
        self._client = client
        self._backend = backend
        self._model = model

    def interpret(self, user_query: str, domain: str, db_path: str) -> dict | None:
        """
        Returns:
          {"field": "value"}  — filter found, apply to fetch
          {}                  — user wants all records
          None                — user specified something unresolvable in schema
        """
        schema = format_schema_for_prompt(db_path, domain)
        user_msg = f"Schema:\n{schema}\n\nUser request: {user_query}"

        kwargs = build_message_kwargs(self._backend)
        response = self._client.messages.create(
            model=self._model,
            max_tokens=200,
            system=_SYSTEM,
            messages=[{"role": "user", "content": user_msg}],
            **kwargs,
        )
        log_usage(self._backend, response.usage)

        raw = next(
            (b.text for b in response.content if hasattr(b, "text")),
            None,
        )
        if not raw:
            return None

        try:
            result = json.loads(raw.strip())
            if result is None:
                return None
            return result if isinstance(result, dict) else None
        except json.JSONDecodeError:
            logger.warning("ScopeInterpreter: invalid JSON from LLM: %r", raw)
            return None
```

- [ ] **Step 4: Run tests to verify they pass**

```bash
cd /mnt/f/AI_learning_project && python -m pytest tests/test_scope_interpreter.py -v
```

Expected: all 6 tests PASS

- [ ] **Step 5: Commit**

```bash
git add scope_interpreter.py tests/test_scope_interpreter.py
git commit -m "feat(scope): add ScopeInterpreter — LLM-driven query-to-filter"
```

---

## Task 2: Strip `country_map` from `interpret_user_query`

**Files:**
- Modify: `data_cleaning_agent.py:47–69`
- Test: `tests/test_scope_interpreter.py` (extend with agent tests)

- [ ] **Step 1: Write the failing tests**

Add to `tests/test_scope_interpreter.py`:

```python
from data_cleaning_agent import DataCleaningAgent


def test_interpret_user_query_no_country_filter():
    """country_map removed — country must NOT appear in filters."""
    db = _make_db()
    agent = DataCleaningAgent(db)
    result = agent.interpret_user_query("clean US data")
    assert "country" not in result


def test_interpret_user_query_keeps_scope_all():
    db = _make_db()
    agent = DataCleaningAgent(db)
    result = agent.interpret_user_query("clean all uncleaned data")
    assert result.get("scope") == "all_uncleaned"


def test_interpret_user_query_keeps_issue_type_phone():
    db = _make_db()
    agent = DataCleaningAgent(db)
    result = agent.interpret_user_query("fix phone numbers")
    assert result.get("issue_type") == "phone"


def test_interpret_user_query_keeps_limit():
    db = _make_db()
    agent = DataCleaningAgent(db)
    result = agent.interpret_user_query("clean first batch")
    assert result.get("scope") == "first_batch"
    assert result.get("limit") == 5
```

- [ ] **Step 2: Run to verify they fail**

```bash
cd /mnt/f/AI_learning_project && python -m pytest tests/test_scope_interpreter.py::test_interpret_user_query_no_country_filter -v
```

Expected: FAIL — `"country" in result` (the country_map still populates it)

- [ ] **Step 3: Remove `country_map` from `data_cleaning_agent.py:47–69`**

Replace lines 36–86 of `data_cleaning_agent.py` `interpret_user_query` (the entire method) with:

```python
    def interpret_user_query(self, user_query: str) -> dict:
        """Parse user query for scope/limit/issue-type modifiers.

        Country filtering is handled by ScopeInterpreter (LLM-driven).
        This method only extracts structural query modifiers.
        """
        query_lower = user_query.lower()
        filters = {}

        # Issue type detection
        if 'phone' in query_lower:
            filters['issue_type'] = 'phone'
        if 'postal' in query_lower or 'zip' in query_lower:
            filters['issue_type'] = 'postal'
        if 'name' in query_lower or 'case' in query_lower:
            filters['issue_type'] = 'case'

        # Scope detection
        if 'all' in query_lower or 'uncleaned' in query_lower or 'dirty' in query_lower:
            filters['scope'] = 'all_uncleaned'
        elif 'first' in query_lower:
            filters['scope'] = 'first_batch'
            filters['limit'] = 5

        return filters
```

- [ ] **Step 4: Run all agent tests to verify they pass**

```bash
cd /mnt/f/AI_learning_project && python -m pytest tests/test_scope_interpreter.py -v
```

Expected: all 10 tests PASS

- [ ] **Step 5: Run full test suite to check for regressions**

```bash
cd /mnt/f/AI_learning_project && python -m pytest tests/ -v --tb=short 2>&1 | tail -30
```

Expected: no new failures

- [ ] **Step 6: Commit**

```bash
git add data_cleaning_agent.py tests/test_scope_interpreter.py
git commit -m "refactor(agent): remove hardcoded country_map from interpret_user_query"
```

---

## Task 3: Wire ScopeInterpreter into `_run_cleaning_workflow`

**Files:**
- Modify: `multi_turn_conversation.py:800–843` (Step 1 + auto-detect block)
- Modify: `multi_turn_conversation.py:873` (research_prompt)
- Modify: `multi_turn_conversation.py:892` (research_system)

- [ ] **Step 1: Replace Step 1 + auto-detect block in `_run_cleaning_workflow`**

In `multi_turn_conversation.py`, add import at top of file (after existing imports):

```python
from scope_interpreter import ScopeInterpreter
```

Then replace lines 798–843 (Step 1 print block through the auto-detect block) with:

```python
        # Step 1: Interpret scope via LLM + extract structural modifiers
        print(f"\n{'='*80}")
        print("STEP 1: INTERPRETING SCOPE")
        print(f"{'='*80}")
        t = time.time()
        scope_filter = ScopeInterpreter(_CLIENT, _BACKEND, _MODEL).interpret(
            user_query, _ACTIVE_DOMAIN, DB_PATH
        )
        if scope_filter is None:
            return "❌ No data found to clean based on your ask."
        filters = agent.interpret_user_query(user_query)
        filters.update(scope_filter)
        step1_elapsed = time.time() - t
        print(f"  scope_filter={scope_filter}  filters={filters}")
        print(f"  ⏱️  {step1_elapsed:.2f}s\n")

        # Step 2: Fetch records
        print(f"{'='*80}")
        print("STEP 2: FETCHING RECORDS")
        print(f"{'='*80}")
        t = time.time()
        records = agent.fetch_data_for_query(filters)
        step2_elapsed = time.time() - t
        if not records:
            return "❌ No records found to clean based on your ask."
        print(f"  ✅ {len(records)} records  ⏱️  {step2_elapsed:.2f}s\n")

        # Derive sub-category scope from filter using domain config
        if not country_scope:
            sub_dim = _DOMAIN_CONFIG.get('sub_category_dimension')
            if sub_dim and sub_dim in scope_filter:
                country_scope = scope_filter[sub_dim]
                print(f"  Sub-category: {country_scope}\n")
```

- [ ] **Step 2: Remove `or 'CA'` from research_prompt line (~line 873)**

Find and change:
```python
            research_prompt = build_research_prompt(country_scope or 'CA', research_table)
```
to:
```python
            research_prompt = build_research_prompt(country_scope, research_table)
```

- [ ] **Step 3: Remove `or 'CA'` from research_system line (~line 892)**

Find and change:
```python
            research_system = build_system_prompt(sub=country_scope or 'CA', schema='')
```
to:
```python
            research_system = build_system_prompt(sub=country_scope, schema='')
```

- [ ] **Step 4: Run full test suite**

```bash
cd /mnt/f/AI_learning_project && python -m pytest tests/ -v --tb=short 2>&1 | tail -30
```

Expected: no failures

- [ ] **Step 5: Commit**

```bash
git add multi_turn_conversation.py
git commit -m "feat(workflow): wire ScopeInterpreter into _run_cleaning_workflow"
```

---

## Task 4: Remove dead code from `multi_turn_conversation.py`

**Files:**
- Modify: `multi_turn_conversation.py` — delete `detect_country_scope` and 5 country handlers

- [ ] **Step 1: Delete `detect_country_scope` function (lines ~191–217)**

Remove the entire function:
```python
def detect_country_scope(user_query: str) -> str | None:
    """
    Return the canonical country code from a user query, or None if ambiguous/multi-country.
    Returns: 'CA', 'USA', 'NL', 'MX', 'JP', or None
    """
    query_lower = user_query.lower()

    if 'north american' in query_lower:
        return None

    country_keywords = {
        'CA': ['canadian', 'canada'],
        'USA': ['american', 'usa', 'united states', 'u.s.'],
        'NL': ['dutch', 'netherlands', 'holland', 'european', 'europe'],
        'MX': ['mexican', 'mexico'],
        'JP': ['japanese', 'japan'],
    }

    matched = [code for code, keywords in country_keywords.items()
               if any(kw in query_lower for kw in keywords)]

    # 'us' too short for substring match — word boundary only
    if not matched and re.search(r'\bus\b', query_lower):
        matched = ['USA']

    return matched[0] if len(matched) == 1 else None
```

- [ ] **Step 2: Delete 5 country handler methods (lines ~536–574)**

Remove all five methods in full:
- `handle_canada_cleaning`
- `handle_usa_cleaning`
- `handle_europe_cleaning`
- `handle_mexico_cleaning`
- `handle_japan_cleaning`

Each follows the same pattern — remove all five:
```python
    def handle_canada_cleaning(self, user_query: str) -> str:
        original = self.system_prompt
        self.system_prompt = build_system_prompt('CA', schema=DB_SCHEMA)
        try:
            return self._run_cleaning_workflow(user_query, country_scope='CA')
        finally:
            self.system_prompt = original
    # ... and the other four
```

- [ ] **Step 3: Verify file still imports cleanly**

```bash
cd /mnt/f/AI_learning_project && python -c "import multi_turn_conversation; print('OK')"
```

Expected: `OK`

- [ ] **Step 4: Run full test suite**

```bash
cd /mnt/f/AI_learning_project && python -m pytest tests/ -v --tb=short 2>&1 | tail -30
```

Expected: no failures

- [ ] **Step 5: Commit**

```bash
git add multi_turn_conversation.py
git commit -m "refactor: remove dead country routing (detect_country_scope + 5 handlers)"
```

---

## Task 5: Fix `prompts/research.py` type hint + final check

**Files:**
- Modify: `prompts/research.py:46`

- [ ] **Step 1: Fix type hint on `build_research_prompt`**

In `prompts/research.py`, change line 46:
```python
def build_research_prompt(country_scope: str, data: str) -> str:
```
to:
```python
def build_research_prompt(country_scope: str | None, data: str) -> str:
```

- [ ] **Step 2: Run full test suite one final time**

```bash
cd /mnt/f/AI_learning_project && python -m pytest tests/ -v --tb=short 2>&1 | tail -30
```

Expected: all tests PASS, no failures

- [ ] **Step 3: Commit**

```bash
git add prompts/research.py
git commit -m "fix(research): allow None country_scope in build_research_prompt"
```

---

## Self-Review

**Spec coverage:**
- ✅ `ScopeInterpreter` new module — Task 1
- ✅ `interpret` returns `dict | None` contract — Task 1
- ✅ LLM prompt: system + user with schema — Task 1
- ✅ `log_usage` per `feedback_llm_api_patterns` — Task 1 (`scope_interpreter.py`)
- ✅ Strip `country_map` from `interpret_user_query` — Task 2
- ✅ Wire ScopeInterpreter in Step 1a — Task 3
- ✅ Sub_dim derivation replaces `or 'CA'` and auto-detect block — Task 3
- ✅ `None` → early return "no data found" — Task 3
- ✅ Remove `detect_country_scope` — Task 4
- ✅ Remove 5 country handlers — Task 4
- ✅ `prompts/research.py` type hint — Task 5
- ✅ Tests for all new behaviour — Tasks 1 + 2

**No placeholders, no TODOs, no TBDs.**

**Type consistency:** `ScopeInterpreter.interpret` returns `dict | None` — used as `scope_filter` throughout, checked with `if scope_filter is None` and `if sub_dim in scope_filter`. Consistent.
