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

```bash
# 1. Scaffold skeleton
python scripts/scaffold_domain.py --domain sports_ticketing

# 2. Edit generated files:
#    skills/sports_ticketing/skills.yaml    — declare skills
#    seeders/sports_ticketing/manifest.yaml — declare seeders
#    data/seeds/sports_ticketing/           — drop seed CSVs

# 3. Preview seeder plan
python scripts/init_data.py --domain sports_ticketing --dry-run

# 4. Seed data
python scripts/init_data.py --domain sports_ticketing
```

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

```
skills/
├── base.py                    # BaseSkill ABC
├── agent.py                   # BaseAgent (sequential executor)
├── registry.py                # SkillRegistry (O(1) lookup, runtime injection)
├── _common/                   # Domain-agnostic skills
│   ├── skill_planner/         # LLM planner
│   └── web_search_enricher/   # Tavily enricher + per-domain parsers
├── real_estate/               # Real estate domain
│   ├── skills.yaml
│   ├── spell_checker/
│   ├── address_standardizer/
│   ├── fuzzy_matcher/
│   ├── municipality_authority/
│   ├── geographic_validator/
│   ├── nominatim_geocoder/
│   ├── data_quality_triage/
│   ├── web_search_enricher    # wired from _common
│   └── skill_planner          # wired from _common
└── sports_ticketing/          # Sports ticketing domain
    ├── skills.yaml
    ├── event_normalizer/
    └── ticket_product_categorizer/
```

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
