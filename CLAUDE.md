# General Instructions

## 1. Think Before Coding
Don't assume. Don't hide confusion. Surface tradeoffs.

Before implementing:
- State your assumptions explicitly. If uncertain, ask.
- If multiple interpretations exist, present them.
- If a simpler approach exists, say so.
- If something is unclear, stop. Name what's confusing.

## 2. Simplicity First
Minimum code that solves the problem. Nothing speculative.

- No features beyond what was asked.
- No abstractions for single-use code.
- No “flexibility” that wasn't requested.
- No error handling for impossible scenarios.
- If 200 lines could be 50, rewrite it.

## 3. Surgical Changes
Touch only what you must. Clean up only your own mess.

- Don't “improve” adjacent code or formatting.
- Don't refactor things that aren't broken.
- Match existing style, even if you'd do it differently.
- If you notice dead code, mention it — don't delete it.

## 4. Goal-Driven Execution
Define success criteria. Loop until verified.

Transform tasks into verifiable goals:
- “Add validation” → “Write tests, then make them pass”
- “Fix the bug” → “Reproduce it in a test, then fix”
- “Refactor X” → “Ensure tests pass before and after”

# AI Data Cleaning Project — Claude Code Instructions

## Environment Variables

Create a `.env` file (or export these):

```bash
# Required: LLM backend (one of these)
ANTHROPIC_API_KEY=sk-ant-...        # Direct Anthropic API
OPENROUTER_API_KEY=sk-or-...        # OpenRouter (alternative models)

# Required: PostgreSQL (when DB_BACKEND=postgres)
POSTGRES_DSN=postgresql://user:pass@localhost:5432/cleaning_db
DB_BACKEND=postgres                  # "postgres" | "sqlite" (default: sqlite)

# Optional: SQLite fallback path
DB_PATH=data/cleaning.db            # only used when DB_BACKEND=sqlite

# Required: Web search enrichment
TAVILY_API_KEY=tvly-...
```

## Model Selection

Default model per backend (set in `llm_client_factory.py`):

| Backend | Default | Override env var |
|---------|---------|-----------------|
| OpenRouter | `openai/gpt-oss-20b:free` | `OPENROUTER_MODEL` |
| Anthropic | `claude-haiku-4-5-20251001` | `ANTHROPIC_MODEL` |

To switch models without touching code, set in `.env`:

```bash
# OpenRouter — switch to Claude Haiku
OPENROUTER_MODEL=anthropic/claude-haiku-4-5

# OpenRouter — switch to Claude Sonnet
OPENROUTER_MODEL=anthropic/claude-sonnet-4-5

# Anthropic direct — switch to Sonnet
ANTHROPIC_MODEL=claude-sonnet-4-5-20251016
```

All pipeline components (orchestrator, evals judge, multi-turn conversation) share the same factory — changing the env var switches every caller at once.

## Setup

```bash
pip install -r requirements.txt
```

## Bootstrap DB (first time)

Run DB migrations, then seed public data:

```bash
# Apply schema migrations (PostgreSQL)
python db/pg_init.py

# Seed domain data (idempotent — safe to rerun)
python scripts/init_data.py --domain real_estate

# Preview without executing
python scripts/init_data.py --domain real_estate --dry-run

# Seed only specific seeders
python scripts/init_data.py --domain real_estate --only spell_corrections
```

## Annotate Domain Columns

Generate LLM descriptions for each column in `column_metadata` (run after seeding):

```bash
# Preview unannotated columns (no writes)
python scripts/annotate_domain.py --domain real_estate --dry-run

# Annotate all gaps
python scripts/annotate_domain.py --domain real_estate

# Overwrite existing LLM-generated annotations
python scripts/annotate_domain.py --domain real_estate --force
```

Annotations stored in `column_metadata` with `is_llm_generated=TRUE` and a `confidence` score. Columns with confidence < 0.70 are flagged — review directly in DB or re-run after improving `prompts/annotation.py`.

## Test Writing

Write a test that reproduces it, then make it pass
Write tests for invalid inputs, then make them pass

## Run Tests

```bash
python -m pytest tests/ -v
```

Tests use mocks — no real DB or API keys required.


## Run Pipeline

```python
from skills.registry import SkillRegistry
from cleaning.orchestrator_v2 import OrchestrationTeam, run_cleaning_workflow_v2

# Simple batch
report = run_cleaning_workflow_v2(records, domain="real_estate")

# With DB + web search
registry = SkillRegistry.load("real_estate", runtime={
    "pg_conn": pg_conn,
    "web_cache": web_search_cache,
    "llm_client": llm_client,
})
team = OrchestrationTeam(registry)
result = team.process_record(record)
```

## Add a New Industry / Domain

**Primary path — `initialize_domain.py`.** A single orchestrator that walks the
schema-first flow against your existing database (see
`docs/runbooks/initialize-domain.md` for a full step-by-step runbook):

```bash
# Full first-time initialization (interactive, 4 phases)
python scripts/initialize_domain.py --domain sports_ticketing
#   Phase 0  Table Registration   — pick which DB tables belong to the domain → domain_registry.json
#   Phase 1  Schema Discovery      — read columns/types/PKs for the registered tables
#   Phase 2  Annotation            — LLM describes each column (writes column_metadata)
#   Phase 3  Seed Research         — samples real data + Q&A → spell_corrections / query_packs / column_metadata seeds

# Register tables added to the DB after the initial run
python scripts/initialize_domain.py --domain sports_ticketing add_table

# Re-run Phase 3 only (e.g. after data is ingested into previously-empty tables)
python scripts/initialize_domain.py --domain sports_ticketing --refresh-seeds

# Reset init state so the domain can be re-initialized (iterative testing).
# Removes column_metadata/spell_corrections/query_pattern_memory/source_registry rows,
# the 'tables' entry in domain_registry.json, and (optionally) generated seed files.
# Does NOT drop your actual data tables.
python scripts/initialize_domain.py --domain sports_ticketing teardown
```

You still wire skills manually after initialization:
- Wire skills in `skills/sports_ticketing/skills.yaml` (copy `_common` entries from the
  real_estate reference, adjust config; add domain-specific skills only for truly
  domain-specific logic).
- Declare seeders in `seeders/sports_ticketing/manifest.yaml`.

**Lower-level scripts** (still independently runnable; `initialize_domain.py` orchestrates them):
`scripts/scaffold_domain.py`, `scripts/research_domain.py`, `scripts/init_data.py`,
`scripts/annotate_domain.py`. `annotate_domain.py` now requires the domain's tables to be
registered first (it reads them from `domain_registry.json`).

## Architecture

```
Pipeline phases (per record):
  1. Deterministic (cost=low)    — spell check, address standardize, fuzzy match
  2. Triage                      — route: done / needs_review / unsalvageable
  3. AI Planner (cost=high)      — LLM picks medium/high skills; plan cached 24h
  4. Planned skills              — municipality, geocode, web search; budget enforced
  5. Re-triage                   — final route with enriched evidence
```

Key classes:
- `SkillRegistry` — loads `skills/<domain>/skills.yaml`, injects runtime resources
- `BaseSkill` / `BaseAgent` — skill ABC + sequential executor
- `OrchestrationTeam` — 5-phase pipeline; accepts `BatchBudget`
- `SkillPlanner` — LLM reads `skill.md` files, outputs ordered JSON plan
- `WebSearchEnricher` — Tavily-backed, gap-triggered, per-domain parsers
- `SeederRegistry` — loads `seeders/<domain>/manifest.yaml`, runs idempotent seeders

## DB Migrations

Migrations live in `db/migrations/`. Run in order:

| File | What |
|------|------|
| `003_spell_corrections.sql` | `spell_corrections` table |
| `004_query_pattern_memory.sql` | `query_pattern_memory`, `source_registry` |
| `005_plan_cache.sql` | `plan_cache` (AI planner 24h TTL) |

Municipality tables (001, 002) from postgres branch — apply manually if needed.

## Skills Layout

Skills are split into two tiers:
- `_common/` — domain-agnostic; any domain wires these via `skills.yaml` config
- `<domain>/` — domain-specific logic only; keep to the minimum

```
skills/
├── base.py                    # BaseSkill ABC
├── agent.py                   # BaseAgent (sequential executor)
├── registry.py                # SkillRegistry (O(1) lookup, runtime injection)
├── _common/                   # Domain-agnostic skills — wire via skills.yaml config
│   ├── spell_checker/         # DB-backed spell correction (domain-overridable)
│   ├── address_standardizer/  # Abbreviation expansion, quadrant normalization
│   ├── record_linker/         # Config-driven fuzzy record matching
│   ├── data_quality_triage/   # Routes record: done/needs_review/unsalvageable (config-driven fields)
│   ├── skill_planner/         # LLM reads skill docs + column annotations → ordered plan
│   └── web_search_enricher/   # Tavily enricher + per-domain parsers in parsers/<domain>/
├── real_estate/               # Real estate domain — domain-specific only
│   ├── skills.yaml            # Wires _common skills with RE config + declares RE-specific skills
│   ├── municipality_authority/  # FSA → municipality via PG cache
│   ├── geographic_validator/    # Province/city/postal coherence
│   └── nominatim_geocoder/      # OSM geocoding with PG cache
└── sports_ticketing/          # Sports ticketing domain
    ├── skills.yaml            # Wires _common skills with ST config + declares ST-specific skills
    ├── event_normalizer/        # Team name canonicalization, date/time normalization
    └── ticket_product_categorizer/  # Product type classification
```

**Rule:** If a skill doesn't contain domain-specific logic, it belongs in `_common/` and is wired via config in the domain's `skills.yaml`. Never copy a skill class into a domain directory just to re-export it.

## Prompt Architecture

### Layers

```
<schema>                    ← input data (injected by assembler, NOT a rule)
<general_rules>             ← prompts/base.py — behavior rules, always loaded
<domain_rules domain="X" sub="Y">  ← prompts/domains/<domain>/<sub>.py — sub-category rules
```

`build_system_prompt(sub, schema, domain)` in `prompts/__init__.py` assembles all layers.
`research.py` is a focused variant for the postal+municipality phase only (see below).

### Sub-category file template

Every sub-category file (e.g. `ca.py`, `usa.py`) must follow this XML structure:

```
<postal_code>     — format, NEVER-modify rule, mismatch flag, missing search strategy
<state_province>  — full name required, valid values list
<municipality>    — real-estate neighbourhood definition + examples + search strategy
<phone>           — format, country code, leading-zero rule
<formatting>      — Country: <full name>
```

CA additionally uses `<Postal Code Logic>`, `<Municipality Logic>`, `<Process>` for
more complex multi-step logic. Other countries can adopt sub-sections when logic grows.

### Separation of concerns

| What | Where |
|------|-------|
| Behavior rules (how to process, when to flag, confidence scale) | `prompts/base.py` |
| Format rules, valid values, country-specific constraints | `prompts/domains/<d>/<sub>.py` |
| Static valid-value lists (state names, province names) | Sub-category file `<state_province>` |
| Dynamic lookup data (FSA→municipality, spell corrections) | DB (seeded via `init_data.py`) |
| Runtime infrastructure references (cache, DB tables) | Skills only — not in prompts |

**Do not reference runtime infrastructure** (e.g. "check the FSA cache table") in prompts.
Prompts describe what to verify; skills implement how to look it up.

### research.py — focused research path

`research.py` provides a stripped-down prompt for the postal+municipality phase only.
Its country notes (`_CANADA_RESEARCH_NOTES` etc.) mirror rules in sub-category files.

**Known limitation:** these notes are a manual copy — they will drift from the country files.
When editing postal format or municipality logic in a country file, also update `research.py`.
Long-term: consolidate by extracting `SHORT_RULES` from each country file.

### Adding a new sub-category (e.g. new country)

1. Create `prompts/domains/<domain>/<cc>.py` following the XML template above
2. Import and register in `prompts/domains/<domain>/__init__.py`
3. Add matching `_<CC>_RESEARCH_NOTES` block to `research.py`

### Confidence scale (prompts ↔ triage routing)

| Label  | Numeric | Triage route |
|--------|---------|--------------|
| HIGH   | ≥ 0.85  | `done` |
| MEDIUM | 0.60–0.84 | `needs_review` |
| LOW    | < 0.60  | `unsalvageable` |

Prompts emit the label string. Triage skill reads numeric thresholds. The mapping is
documented in `prompts/base.py` CONFIDENCE SCALE block — keep both in sync.

## Hardcoded Data Policy

**No hardcoded data in skill source files.** All domain dictionaries live in DB:
- Spell corrections → `spell_corrections` table (seeded from `data/seeds/<domain>/spell_corrections.csv`)
- FSA → municipality → `municipality_lookup_cache` table (seeded from Wikipedia)
- Query templates → `query_pattern_memory` table (seeded from `data/seeds/<domain>/query_packs.yaml`)

Lock tests assert these are not reintroduced:
- `tests/cleaning/test_spell_corrections.py::test_no_hardcoded_corrections_in_spell_checker_source`
- `tests/test_full_agent_pipeline.py::test_municipality_authority_no_hardcoded_fsa_dict`

## Confidence + Routing

| Route | Condition |
|-------|-----------|
| `done` | confidence ≥ 0.85 AND completeness ≥ 0.80 |
| `needs_review` | 0.60 ≤ confidence < 0.85 |
| `unsalvageable` | completeness < 0.70 OR confidence < 0.60 |

Confidence uses **min()** (weakest-link), not average.

## Web Search Budget

Default 100 queries/batch. Pass `BatchBudget` to `OrchestrationTeam`:

```python
from cleaning.orchestrator_v2 import BatchBudget, OrchestrationTeam
budget = BatchBudget(max_queries=50)
team = OrchestrationTeam(registry, batch_budget=budget)
```

Web search only triggers when `_triage_route == "needs_review"`.
