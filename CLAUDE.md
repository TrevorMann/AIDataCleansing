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
