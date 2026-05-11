# Pipeline Entry Point and Flow
**Date:** 2026-05-11
**Companion to:** `2026-05-11-general-data-cleaning-refactor-design.md`

---

## Entry Point

```python
from skills.registry import SkillRegistry
from cleaning.orchestrator_v2 import OrchestrationTeam, BatchBudget

# 1. Load registry once per batch — expensive, reuse across records
registry = SkillRegistry.load(
    domain="real_estate",
    runtime={
        "pg_conn": pg_conn,          # DB connection for spell_corrections, plan_cache, etc.
        "web_cache": web_cache,      # Tavily cache
        "llm_client": llm_client,   # Only used by LLM planner (last resort)
    }
)

# 2. Optional: cap web search spend per batch
budget = BatchBudget(max_queries=100)

# 3. Run
team = OrchestrationTeam(registry, batch_budget=budget)

# Single record
result = team.process_record(record)

# Batch
report = team.process_batch(records)
```

Registry validates at load time:
- All declared skill classes importable
- Phase 1 field sets disjoint (no overlapping text_fields / address_fields)
- No circular skill dependencies
- All `depends_on` references resolve

Raises `ConfigError` or `ValueError` at load — fail fast, never at record time.

---

## Per-Record Flow

```
INPUT RECORD (raw dict from source)
      │
      ▼
┌─────────────────────────────────────────────────────────┐
│ PHASE 1 — Deterministic, Parallel                       │
│                                                         │
│  spell_checker ──────────────────┐                      │
│  address_standardizer ───────────┼──► merge → record'   │
│  record_linker (link-only) ──────┘    + audit_entries   │
│                                                         │
│  ThreadPoolExecutor — each skill gets record.copy()     │
│  Results merged: disjoint field sets, no conflicts      │
└─────────────────────────────────────────────────────────┘
      │
      ▼
┌─────────────────────────────────────────────────────────┐
│ TRIAGE 1 — Route based on confidence                    │
│                                                         │
│  confidence ≥ 0.85 AND completeness ≥ 0.80 → DONE ──►  │
│  0.60 ≤ confidence < 0.85               → needs_review  │
│  confidence < 0.60 OR completeness < 0.70 → unsalvageable│
└─────────────────────────────────────────────────────────┘
      │ needs_review only
      ▼
┌─────────────────────────────────────────────────────────┐
│ PHASE 2 — Domain Tier 3 Skills (sequential, per domain) │
│                                                         │
│  real_estate:                                           │
│    municipality_authority   FSA/geo → canonical munic.  │
│    geographic_validator     postal / province coherence │
│    nominatim_geocoder       coordinate lookup           │
│                                                         │
│  sports_ticketing:                                      │
│    venue_normalizer         (future)                    │
│    team_normalizer          (future)                    │
│                                                         │
│  Runs in depends_on order (topological sort)            │
└─────────────────────────────────────────────────────────┘
      │
      ▼
┌─────────────────────────────────────────────────────────┐
│ PHASE 3 — Web Search Enrichment                         │
│                                                         │
│  Triggered only when _triage_route == "needs_review"    │
│  Fetches authoritative external facts (postal codes,    │
│  municipality names, venue addresses)                   │
│  Budget-gated (BatchBudget.max_queries)                 │
│  Results feed field corrections directly                │
└─────────────────────────────────────────────────────────┘
      │
      ▼
┌─────────────────────────────────────────────────────────┐
│ TRIAGE 2 — Re-route with enriched evidence              │
│                                                         │
│  Same thresholds as Triage 1                            │
│  Most records resolve here → DONE                       │
└─────────────────────────────────────────────────────────┘
      │ still unresolved only
      ▼
┌─────────────────────────────────────────────────────────┐
│ PHASE 4 — LLM Planner (LAST RESORT)                     │
│                                                         │
│  Invoked only when all deterministic paths exhausted    │
│  Reads skill docs + record state + unresolved gaps      │
│  Outputs narrow recommendation for the specific gap     │
│  NOT "clean this record" — "resolve this specific       │
│  conflict given this evidence"                          │
│  Plan cached 24h (plan_cache table)                     │
└─────────────────────────────────────────────────────────┘
      │
      ▼
┌─────────────────────────────────────────────────────────┐
│ TRIAGE 3 — Final route                                  │
│                                                         │
│  done → write clean record                              │
│  needs_review → queue for human review                  │
│  unsalvageable → reject with audit trail                │
└─────────────────────────────────────────────────────────┘
      │
      ▼
┌─────────────────────────────────────────────────────────┐
│ OUTPUT                                                  │
│                                                         │
│  cleaned_record  → destination (DB / file / stream)     │
│  audit_log       → audit table (separate, never merged) │
│  _group_id       → entity groups from record_linker     │
└─────────────────────────────────────────────────────────┘
```

---

## Skill Result Contract (every skill)

```python
class SkillResult(BaseModel):
    record: dict[str, Any]   # cleaned data only
    audit: List[AuditEntry]  # what changed and why
    confidence: float        # this skill's confidence in its output

class AuditEntry(BaseModel):
    skill: str
    field: str
    original: Any
    corrected: Any
    reason: str
    confidence: float
```

Skills never return raw dicts. Pipeline never reads `_decisions` from a record. Audit is a side stream collected by the orchestrator, not passed between skills.

---

## What Flows Between Skills

```
record (dict)          — data being cleaned, passed forward
audit_entries (list)   — accumulated by orchestrator, never in record
_triage_route (str)    — set on record by triage, read by orchestrator to decide next phase
_group_id (str)        — set by record_linker, carried through untouched
_linked_records (list) — set by record_linker, carried through untouched
```

`_triage_route`, `_group_id`, `_linked_records` are the only underscore-prefixed fields allowed in the record dict. All others (`_decisions` etc.) are removed.

---

## Domain Configuration Example — Real Estate

```yaml
# skills/real_estate/skills.yaml
domain: real_estate

skills:
  spell_checker:
    class: skills._common.spell_checker.spell_checker.SpellChecker
    skill_doc: skills/_common/spell_checker/skill.md
    config:
      text_fields: [city, municipality, description]
      threshold: 0.85
      domain: real_estate
    cost: low
    phase: 1

  address_standardizer:
    class: skills._common.address_standardizer.address_standardizer.AddressStandardizer
    skill_doc: skills/_common/address_standardizer/skill.md
    config:
      address_fields: [address]
    cost: low
    phase: 1

  record_linker:
    class: skills._common.record_linker.record_linker.RecordLinker
    skill_doc: skills/_common/record_linker/skill.md
    config:
      blocking_fields: [postal_code]
      match_rules:
        - name: email_exact
          fields: [email]
          match_type: exact
          weight: 1.0
        - name: address_composite
          fields: [address, city, postal_code]
          match_type: fuzzy
          threshold: 0.90
          weight: 0.80
    cost: low
    phase: 1

  municipality_authority:
    class: skills.real_estate.municipality_authority.municipality_authority.MunicipalityAuthorityAgent
    skill_doc: skills/real_estate/municipality_authority/skill.md
    config:
      trust_postal_over_name: true
      escalate_confidence_threshold: 0.60
      pg_conn: "${runtime.pg_conn}"
    cost: high
    phase: 2
    depends_on: [address_standardizer]

  geographic_validator:
    class: skills.real_estate.geographic_validator.geographic_validator.GeographicValidator
    skill_doc: skills/real_estate/geographic_validator/skill.md
    config:
      strict_mode: false
    cost: medium
    phase: 2
    depends_on: [municipality_authority]

  nominatim_geocoder:
    class: skills.real_estate.nominatim_geocoder.nominatim_geocoder.NominatimGeocoderSkill
    skill_doc: skills/real_estate/nominatim_geocoder/skill.md
    config:
      rate_limit: 1
      cache_ttl_days: 30
      pg_conn: "${runtime.pg_conn}"
    cost: high
    phase: 2
    depends_on: [address_standardizer]

  data_quality_triage:
    class: skills.real_estate.data_quality_triage.data_quality_triage.DataQualityTriageAgent
    skill_doc: skills/real_estate/data_quality_triage/skill.md
    config:
      min_confidence_auto_complete: 0.85
      min_confidence_agent_review: 0.60
    cost: medium
    phase: triage

  web_search_enricher:
    class: skills._common.web_search_enricher.web_search_enricher.WebSearchEnricher
    skill_doc: skills/_common/web_search_enricher/skill.md
    config:
      max_queries: 3
      trigger_below: 0.70
      pg_conn: "${runtime.pg_conn}"
      web_cache: "${runtime.web_cache}"
    cost: high
    phase: 3

  skill_planner:
    class: skills._common.skill_planner.skill_planner.SkillPlanner
    skill_doc: skills/_common/skill_planner/skill.md
    config:
      tier: "fast"
      plan_cache_ttl_hours: 24
      pg_conn: "${runtime.pg_conn}"
      llm_client: "${runtime.llm_client}"
    cost: high
    phase: 4
    depends_on: [data_quality_triage]
```

---

## Domain Configuration Example — Sports Ticketing

```yaml
# skills/sports_ticketing/skills.yaml
domain: sports_ticketing

skills:
  spell_checker:
    class: skills._common.spell_checker.spell_checker.SpellChecker
    skill_doc: skills/_common/spell_checker/skill.md
    config:
      text_fields: [event_description, venue_notes]
      threshold: 0.85
      domain: sports_ticketing
    cost: low
    phase: 1

  record_linker:
    class: skills._common.record_linker.record_linker.RecordLinker
    skill_doc: skills/_common/record_linker/skill.md
    config:
      blocking_fields: [event_date]
      match_rules:
        - name: venue_team_date
          fields: [venue_name, home_team, event_date]
          match_type: fuzzy
          threshold: 0.85
          weight: 0.90
    cost: low
    phase: 1

  event_normalizer:
    class: skills.sports_ticketing.event_normalizer.event_normalizer.EventNormalizer
    config:
      pg_conn: "${runtime.pg_conn}"
    cost: low
    phase: 2
    depends_on: []

  ticket_product_categorizer:
    class: skills.sports_ticketing.ticket_product_categorizer.ticket_product_categorizer.TicketProductCategorizer
    config: {}
    cost: low
    phase: 2
    depends_on: []
```

---

## Adding a New Domain

```bash
# 1. Scaffold
python scripts/scaffold_domain.py --domain healthcare

# 2. Edit skills/healthcare/skills.yaml
#    - Declare Tier 1+2 skills with domain-specific field configs
#    - Add Tier 3 domain skills (e.g. icd_code_validator, provider_lookup)

# 3. Seed domain-specific spell override data
python scripts/init_data.py --domain healthcare

# 4. Add domain parsers to web_search_enricher if web enrichment needed
#    skills/_common/web_search_enricher/parsers/healthcare/
```

Tier 1+2 skills require zero code changes — only YAML config with the right field names for that domain.

---

## Confidence Thresholds (current)

| Label | Numeric | Triage route |
|-------|---------|--------------|
| HIGH | ≥ 0.85 | `done` |
| MEDIUM | 0.60 – 0.84 | `needs_review` |
| LOW | < 0.60 | `unsalvageable` |

Completeness < 0.70 → `unsalvageable` regardless of confidence.
Confidence uses `min()` (weakest-link across skills). Calibrated scoring is a future improvement — see design spec.
