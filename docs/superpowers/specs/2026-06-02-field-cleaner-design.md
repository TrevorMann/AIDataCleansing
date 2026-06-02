# Field Cleaner Skill — Design Spec

**Date:** 2026-06-02
**Status:** Approved for implementation

---

## Problem

The existing skills are domain-specific and field-specific (real estate, sports ticketing). Adding cleaning support for a new industry or a new field type requires writing new Python classes. Industry-specific knowledge is embedded in skill source files (hardcoded alias dictionaries, Toronto-only postal logic) rather than in data. There is no self-learning mechanism — every correction requires a code or config change.

---

## Goals

- One skill handles all field-type cleaning for any domain
- Industry-specific knowledge lives in rules files and DB, not in code
- Deterministic path handles known values without LLM calls
- LLM fires only for ambiguous or unresolvable values (one batched call per record)
- System learns from high-confidence LLM corrections and reuses them deterministically
- PII / sensitive fields are never touched or logged
- Users can inspect and toggle learned corrections

---

## Non-Goals

- This skill does not replace `address_standardizer` (abbreviation expansion) or `spell_checker` (general typo correction) — it is a complement that handles field-type-level validation, normalization, and enrichment
- Does not auto-promote learned corrections to base rules files — that is a manual human step
- Does not geocode or do external lookups (that remains `nominatim_geocoder`)

---

## Architecture

### File Layout

```
skills/
└── _common/
    └── field_cleaner/
        ├── field_cleaner.py       # FieldCleanerSkill — the only class
        ├── resolver.py            # FieldTypeResolver — internal field→type mapping
        ├── skill.md               # LLM planner documentation
        └── rules/
            ├── gender.yaml
            ├── address.yaml
            ├── city.yaml
            ├── postal_code.yaml
            └── country.yaml
```

New field types are added by dropping a new `rules/<type>.yaml` file. No code changes required.

---

### Field Type Resolution

`FieldTypeResolver` runs once at registry load time and produces a `{field_name → field_type}` map used by the skill throughout the batch. It does not run per-record.

**Priority order (highest to lowest):**

1. **Config override** — explicit mapping in `skills.yaml` (e.g. `zip: postal_code`, `sex: gender`). Always wins.
2. **Sensitive flag** — fields listed in `sensitive_fields` config or annotated `sensitive: true` in `column_metadata`. Marked as `sensitive` regardless of type — skip entirely.
3. **Column metadata annotation** — `column_metadata.field_type` if present and `enabled=true` in annotation. Used when available; may not cover all domains yet.
4. **Convention** — field name pattern matching against known patterns per type (e.g. `postal*`, `zip*`, `postcode` → `postal_code`; `gender`, `sex` → `gender`). Last resort heuristic.

Fields that don't resolve to any type are silently skipped (no audit entry).

---

### Rules File Format

Each file is static, version-controlled, and human-authored. Never written to at runtime.

```yaml
# rules/gender.yaml
field_type: gender
description: "Normalize gender values to a canonical form"

canonical_values:
  - Male
  - Female
  - Non-binary
  - Unknown

normalization_map:          # deterministic: lowercase(raw) → canonical
  m: Male
  f: Female
  male: Male
  female: Female
  man: Male
  woman: Female
  nb: Non-binary
  non binary: Non-binary

guardrails:                 # injected into LLM system prompt as hard constraints
  - "Single letters (T, Q, etc.) are not valid — do not use them as output"
  - "If the value cannot be resolved with high confidence, output Unknown"
  - "Never infer gender from a name"

reject_patterns:            # regex — value fails deterministic, escalates to LLM
  - "^[a-zA-Z]$"           # single letter
```

```yaml
# rules/postal_code.yaml
field_type: postal_code
description: "Validate and normalize postal/zip codes"

guardrails:
  - "Format depends on the country field in the same record — use it if present"
  - "Canadian format: A1A 1A1 (e.g. M5V 2T6)"
  - "US format: 12345 or 12345-6789"
  - "Do not invent or guess a postal code — output null if unresolvable"

validation_patterns:
  CA: "^[A-Z]\\d[A-Z]\\s?\\d[A-Z]\\d$"
  US: "^\\d{5}(-\\d{4})?$"

reject_if_empty: true
```

```yaml
# rules/country.yaml
field_type: country
description: "Normalize country to ISO 3166-1 alpha-2 code"

normalization_map:
  canada: CA
  united states: US
  usa: US
  america: US
  united kingdom: GB
  uk: GB
  great britain: GB
  australia: AU
  france: FR
  germany: DE
  mexico: MX
  netherlands: NL
  japan: JP

guardrails:
  - "Output ISO 3166-1 alpha-2 two-letter code (e.g. CA, US, GB)"
  - "Do not output country names — always use the code"
  - "If uncertain, output null — do not guess"
```

---

### Learned Corrections (Two-Tier Rules)

Learned corrections live in DB alongside the base rules files. The skill reads both at startup; base rules files are static.

**DB table (new migration):**

```sql
CREATE TABLE learned_field_corrections (
    id              SERIAL PRIMARY KEY,
    field_type      TEXT NOT NULL,
    domain          TEXT NOT NULL,
    raw_value       TEXT NOT NULL,
    corrected_value TEXT NOT NULL,
    confidence      FLOAT NOT NULL,
    times_seen      INT DEFAULT 1,
    enabled         BOOLEAN DEFAULT TRUE,
    promoted        BOOLEAN DEFAULT FALSE,   -- flagged for manual merge to base rules
    created_at      TIMESTAMPTZ DEFAULT NOW(),
    last_seen_at    TIMESTAMPTZ DEFAULT NOW(),
    UNIQUE (field_type, domain, raw_value)
);
```

**`enabled` flag behaviour:**
- `TRUE` (default) — fires on deterministic path, no LLM needed
- `FALSE` — kept for audit history, skipped in processing; field goes to LLM again
- `promoted` — human-reviewed and ready to merge into `rules/<type>.yaml` (manual step only)

**Deterministic lookup order per field:**
1. Base `normalization_map` from rules file (static YAML)
2. `learned_field_corrections` WHERE `enabled = TRUE` for this `field_type` + `domain`
3. → LLM if still unresolved

---

### Processing Flow (per record)

```
record arrives
     │
     ▼
FieldTypeResolver lookup (pre-computed at startup)
  each field → field_type | sensitive | (skip)
     │
     ├── sensitive → audit: "skipped: sensitive field", value never logged or touched
     │
     └── resolved field_type →
           deterministic pass:
             lowercase(value) in normalization_map?  → apply, confidence=1.0, log
             value matches validation_pattern?        → log "valid", no change
             value matches reject_pattern?            → add to needs_llm batch
             value in learned_field_corrections?      → apply, log, increment times_seen
             value already a canonical_value?         → log "valid", no change
             else ambiguous                           → add to needs_llm batch
     │
     ▼ (only if needs_llm batch non-empty)
Single LLM call covering all dirty fields in the record:
  system prompt  = general data cleaning knowledge
  per field type = guardrails from rules/<type>.yaml injected
  context        = "previously learned: T→Male (gender)" if relevant
  structured output:
    [{field, original, corrected, confidence, reason}]
     │
     ▼
For each LLM correction:
  confidence ≥ 0.90 → apply correction
                    → upsert learned_field_corrections (increment times_seen if exists)
  confidence 0.70–0.89 → apply correction
                       → do NOT write to learned corrections (not confident enough)
                       → audit: confidence + "needs_review"
  confidence < 0.70  → do NOT apply
                     → audit: "unresolvable", reason logged, value unchanged
     │
     ▼
return record + audit entries
(audit entries never contain values from sensitive fields)
```

---

### Skills.yaml Wiring

```yaml
field_cleaner:
  class: skills._common.field_cleaner.field_cleaner.FieldCleanerSkill
  skill_doc: skills/_common/field_cleaner/skill.md
  cost: low        # deterministic path; escalates to high if LLM fires
  phase: 1
  latency_estimate_ms: 50   # deterministic; LLM path ~1500ms
  depends_on: [spell_checker, address_standardizer]
  config:
    field_overrides:                              # config override layer (highest priority)
      zip: postal_code
      postcode: postal_code
      sex: gender
      nation: country
    sensitive_fields: [ssn, sin, dob, credit_card, tax_id, passport]
    llm_client: "${runtime.llm_client}"
    pg_conn: "${runtime.pg_conn}"
    learning_confidence_threshold: 0.90          # min confidence to write learned correction
```

---

### Audit Trail Contract

Every processed field produces one audit entry via `skill.get_audit()`. Sensitive fields produce a "skipped" entry with no value. The record dict itself never contains `_decisions` — the orchestrator collects audit via `skill.get_audit()` directly (existing pattern in `orchestrator_v2._run_skill`).

```python
# Example audit entries
{"skill": "FieldCleanerSkill", "decision": "gender: 'M' → 'Male'",       "reason": "normalization_map", "confidence": 1.0}
{"skill": "FieldCleanerSkill", "decision": "gender: skipped",             "reason": "sensitive field",   "confidence": 1.0}
{"skill": "FieldCleanerSkill", "decision": "gender: 'T' → 'Male'",       "reason": "LLM (learned)",     "confidence": 0.95}
{"skill": "FieldCleanerSkill", "decision": "postal_code: unresolvable",   "reason": "LLM confidence 0.4","confidence": 0.40}
```

---

### Initial Field Types (Phase 1)

| Type | Convention patterns | Key rules |
|---|---|---|
| `gender` | `gender`, `sex` | Normalize to Male/Female/Non-binary/Unknown; reject single letters |
| `address` | `address`, `addr`, `street*` | Expand abbreviations; flag P.O. boxes |
| `city` | `city`, `town`, `suburb` | Title case; flag numeric values |
| `postal_code` | `postal*`, `zip*`, `postcode` | Country-aware format validation |
| `country` | `country`, `nation`, `country_code` | Normalize to ISO 3166-1 alpha-2 |

---

### DB Migration

New file: `db/migrations/007_learned_field_corrections.sql`

Follows existing migration naming convention. Idempotent (`CREATE TABLE IF NOT EXISTS`).

---

## What Gets Scrapped

- `skills/real_estate/data_quality_triage/` — replaced by `_common/data_quality_triage` (already exists)
- `skills/real_estate/geographic_validator/` — geography validation moves into `postal_code` and `country` rules + LLM guardrails
- `skills/sports_ticketing/event_normalizer/` — team alias normalization moves to `learned_field_corrections` table seeded per domain
- `skills/sports_ticketing/ticket_product_categorizer/` — out of scope for this skill; domain-specific categorization stays in domain skills

The `_common` skills (spell_checker, address_standardizer, record_linker, data_quality_triage, skill_planner, web_search_enricher) are unchanged.

---

## Open Questions (deferred)

- Expose `learned_field_corrections` via a CLI command (`python scripts/manage_learned_rules.py --domain X`) for user to review/toggle — follow-up task
- Convention pattern registry (list of patterns per type) — hard-coded in `resolver.py` initially; can move to config later if patterns need domain overrides
