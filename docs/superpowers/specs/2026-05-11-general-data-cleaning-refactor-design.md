# General Data Cleaning Refactor — Design Spec
**Date:** 2026-05-11
**Branch:** feature/agent-team-skill-registry
**Status:** Approved — pending implementation plan

---

## Problem Statement

The current data cleaning pipeline has general skills (spell checking, address standardization, fuzzy matching) implemented with real-estate-specific hardcoding — field names, match logic, and documentation all assume real estate data. The skills live in `skills/real_estate/` despite being domain-agnostic in nature. This prevents reuse across domains (sports ticketing, HR, etc.) and makes the "common" code misleading.

Additionally: audit trail is mixed into the data record (`_decisions`), skill boundaries have no type contracts, Phase 1 skills run sequentially despite being independent, the LLM planner is positioned as the primary orchestration path (it should be last resort), and fuzzy matching is the wrong abstraction for record linkage.

---

## Design Principles

1. **Deterministic tools do the cleaning** — LLM coordinates only when rules can't resolve
2. **Config drives field selection** — no field names hardcoded in skill implementations
3. **Explicit opt-in per domain** — each `skills.yaml` declares which skills it uses and with what config
4. **Domain thin wrappers as extension points** — `skills/<domain>/spell_checker/` re-exports `_common/` today, subclasses when domain needs divergent behavior
5. **Audit trail separate from data** — never in-band in the cleaned record
6. **Type contracts at skill boundaries** — Pydantic models, not raw dicts
7. **Phase 1 parallelism** — independent skills run concurrently, field set disjointness enforced by registry

---

## Skill Tier Architecture

```
Tier 1 — Universal  (skills/_common/)
  spell_checker         any text field, any domain, symspellpy + DB override
  record_linker         replaces fuzzy_matcher — graph-based, transitive, link-only

Tier 2 — Field-type  (skills/_common/)
  address_standardizer  any domain with address-like fields, abbreviation expansion

Tier 3 — Domain  (skills/<domain>/)
  real_estate:
    municipality_authority    FSA → municipality, geo → municipality
    geographic_validator      postal/province/country coherence
    nominatim_geocoder        coordinate lookup via Nominatim API
  sports_ticketing:
    venue_normalizer          venue name → canonical form (future)
    team_normalizer           team name → canonical form (future)
```

Domain thin wrapper pattern:
```python
# skills/real_estate/spell_checker/spell_checker.py
from skills._common.spell_checker.spell_checker import SpellChecker  # noqa: F401
```
Subclass only when domain behavior genuinely diverges from the common implementation.

---

## Pydantic at Skill Boundaries

**Why:** Raw `Dict[str, Any]` input/output means type errors surface 3 skills downstream. Pydantic catches bad data at the exact boundary it crosses, makes skill contracts self-documenting, and enables IDE autocomplete and trivial test fixture generation.

```python
from pydantic import BaseModel, ConfigDict
from typing import Any, List, Optional

class AuditEntry(BaseModel):
    skill: str
    field: str
    original: Any
    corrected: Any
    reason: str
    confidence: float

class SkillResult(BaseModel):
    model_config = ConfigDict(extra='allow')  # domain fields pass through
    record: dict[str, Any]    # cleaned data only — no audit pollution
    audit: List[AuditEntry]   # separate stream
    confidence: float

class SkillInput(BaseModel):
    model_config = ConfigDict(extra='allow')
    record: dict[str, Any]
    config: dict[str, Any]
```

`extra='allow'` on `record` lets unknown domain fields pass through without validation failure on infrastructure-controlled fields.

---

## Skill Implementations

### Spell Checker (Tier 1)

**Base:** `symspellpy` English dictionary (250k words) — catches common typos without any domain seeding.
**Override layer:** domain DB table (`spell_corrections`) for proper nouns `symspellpy` gets wrong (municipality names, venue names, brand names).
**Field selection:** inclusion-only via config. Anything not in `text_fields` is never touched.

```yaml
# real_estate/skills.yaml
spell_checker:
  class: skills._common.spell_checker.spell_checker.SpellChecker
  skill_doc: skills/_common/spell_checker/skill.md
  config:
    text_fields: [city, description, notes]
    threshold: 0.85
    domain: real_estate
  cost: low
  latency_estimate_ms: 100

# sports_ticketing/skills.yaml
spell_checker:
  class: skills._common.spell_checker.spell_checker.SpellChecker
  skill_doc: skills/_common/spell_checker/skill.md
  config:
    text_fields: [event_description, venue_notes]
    threshold: 0.85
    domain: sports_ticketing
  cost: low
  latency_estimate_ms: 100
```

Corrects only HIGH confidence matches (above threshold). Flags ambiguous as `needs_review`. PII fields are protected by omission — if not in `text_fields`, not processed.

### Address Standardizer (Tier 2)

No logic change — abbreviation expansion is already domain-agnostic. One fix: remove hardcoded `"address"` field, add `address_fields` config.

```yaml
address_standardizer:
  config:
    address_fields: [address, mailing_address, delivery_address]
```

### Record Linker (Tier 1, replaces FuzzyMatcher)

**Purpose:** reveal which records refer to the same real-world entity. Never mutates data. Output is linkage metadata only.

**Transitive matching:** if record A links to B (email match) and B links to C (name+company match), then A, B, C are all the same group. Implemented via Union-Find (connected components on the match graph).

```yaml
record_linker:
  config:
    blocking_fields: [postal_code]       # narrow candidate pool before comparison
    match_rules:
      - name: email_exact
        fields: [email]
        match_type: exact                # 100% match required
        weight: 1.0
      - name: name_company
        fields: [first_name, last_name, company]
        match_type: fuzzy
        threshold: 0.85
        weight: 0.90
      - name: address_composite
        fields: [address, city, postal_code]
        match_type: fuzzy
        threshold: 0.90
        weight: 0.80
```

Algorithm:
1. Apply `blocking_fields` — compare only within block (same postal code, same event date, etc.)
2. For each pair within block, evaluate match rules
3. Any rule fires → `union(record_a_id, record_b_id)` in Union-Find
4. After all pairs: assign `_group_id = find(record_id)` to each record
5. Records sharing a `_group_id` are the same entity

Output per record (metadata, not field mutation):
```python
{
  "_group_id": "rec_001",            # shared across all group members
  "_linked_records": [               # direct matches found
    {"id": "rec_002", "matched_rule": "email_exact", "confidence": 1.0},
    {"id": "rec_003", "matched_rule": "name_company", "confidence": 0.88},
  ]
}
```

Sports ticketing example:
```yaml
record_linker:
  config:
    blocking_fields: [event_date]
    match_rules:
      - name: venue_team_date
        fields: [venue_name, home_team, event_date]
        match_type: fuzzy
        threshold: 0.85
        weight: 0.90
```

---

## Audit Log

`_decisions` removed from data records. Skill base class collects `AuditEntry` objects in `SkillResult.audit`. Pipeline accumulates them per record into an `AuditLog` written to a separate table or log stream.

```python
# audit_log table schema
record_id     TEXT
run_id        TEXT
skill         TEXT
field         TEXT
original      TEXT
corrected     TEXT
reason        TEXT
confidence    FLOAT
created_at    TIMESTAMP
```

Cleaned record written to destination clean. Audit written to audit table. Never merged.

---

## Phase 1 Parallel Execution

Phase 1 skills (spell_checker, address_standardizer, record_linker) have no inter-dependencies. Run concurrently via `ThreadPoolExecutor`.

```python
from concurrent.futures import ThreadPoolExecutor, as_completed

def run_phase1(record: dict, skills: dict, tools: dict) -> tuple[dict, list]:
    audit_entries = []

    with ThreadPoolExecutor() as executor:
        futures = {
            executor.submit(skill.run, record.copy(), tools): name
            for name, skill in skills.items()
        }
        results = {}
        for future in as_completed(futures):
            name = futures[future]
            result: SkillResult = future.result()
            results[name] = result
            audit_entries.extend(result.audit)

    # Merge: each skill owns disjoint field sets — no conflicts possible
    merged = dict(record)
    for result in results.values():
        merged.update(result.record)

    return merged, audit_entries
```

**Safety contract:** Registry validates at load time that no two Phase 1 skills declare overlapping `text_fields` / `address_fields`. Raises `ConfigError` on overlap. `record_linker` is always safe — outputs only `_group_id` / `_linked_records`, never touches source fields.

---

## LLM Role (Revised)

LLM planner is **last resort** — invoked only when:
- All deterministic Phase 1 + Tier 3 domain skills have run
- Web search enrichment ran and still did not resolve
- Triage confidence remains below threshold

Not the primary orchestration path. Handles the long tail of genuinely ambiguous records that rules cannot resolve. Narrowly prompted with the specific unresolved gap — not "clean this record" but "given these specific conflicts and evidence, what do you recommend?"

LLM planner position in pipeline:
```
Phase 1 (parallel deterministic)
  → Triage
  → Tier 3 domain skills
  → Web search enrichment
  → Re-triage
  → [LAST RESORT] LLM planner if still unresolved
  → Final triage
```

Web search (Tavily) remains valid — fetches authoritative external facts (venue addresses, postal codes, municipality names). Deterministic facts, not LLM generation. Results feed field corrections directly.

---

## What Does NOT Change

- `SkillRegistry` — load, O(1) lookup, runtime injection
- `BaseSkill` / `BaseAgent` ABC
- `OrchestrationTeam` 5-phase structure (internal parallelism added to Phase 1)
- `WebSearchEnricher` and its domain parsers
- `municipality_authority`, `geographic_validator`, `nominatim_geocoder`
- `DataQualityTriage` routing thresholds
- Prompt architecture (3-layer assembly)
- DB schema for spell_corrections, plan_cache, query_pattern_memory

---

## File Changes Summary

### New / moved
```
skills/_common/spell_checker/skill.md          (was real_estate/, now generic)
skills/_common/address_standardizer/skill.md   (was real_estate/, now generic)
skills/_common/record_linker/                  (new — replaces fuzzy_matcher)
skills/sports_ticketing/spell_checker/         (new thin wrapper)
skills/sports_ticketing/address_standardizer/  (new thin wrapper)
skills/sports_ticketing/record_linker/         (new thin wrapper)
```

### Modified
```
skills/_common/spell_checker/spell_checker.py        add symspellpy, remove hardcoded fields
skills/_common/address_standardizer/address_standardizer.py  remove hardcoded "address" field
skills/base.py                                        SkillResult Pydantic model, audit split
skills/real_estate/skills.yaml                        add text_fields/address_fields config, skill_doc paths to _common/
skills/sports_ticketing/skills.yaml                   add spell_checker, address_standardizer, record_linker
cleaning/orchestrator_v2.py                           Phase 1 parallel execution
```

### Deleted
```
skills/real_estate/fuzzy_matcher/                     replaced by record_linker
skills/_common/fuzzy_matcher/                         replaced by record_linker
skills/real_estate/spell_checker/skill.md             moved to _common/
skills/real_estate/address_standardizer/skill.md      moved to _common/
skills/real_estate/fuzzy_matcher/skill.md             deleted (skill deleted)
```

### Tests updated
```
tests/test_skill_registry.py                  imports → _common.*
tests/test_full_agent_pipeline.py             imports → _common.*
tests/cleaning/test_spell_corrections.py      imports → _common.*
```

---

## Documented for Future (Out of Scope Now)

- Calibrated confidence scoring (replace `min()` heuristic with proper probability model)
- Circuit breaker / retry per skill with configurable failure policy
- Idempotent checkpointing for large batch recovery
- Async web search enrichment (non-blocking)
- `phone_normalizer`, `date_normalizer` as additional Tier 2 skills
- Formal blocking strategy library for record_linker at scale (currently: exact blocking on field value)
