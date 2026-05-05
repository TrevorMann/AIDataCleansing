# AI Data Cleaning Pipeline

Domain-agnostic data cleaning framework with deterministic skills, AI-driven orchestration, and smart web search enrichment. Built on Anthropic SDK + PostgreSQL.

## What It Does

Takes messy records (real estate listings, sports tickets, etc.) and cleans them through a 5-phase pipeline:

1. **Deterministic** — spell correction, address standardization, fuzzy matching (no LLM, fast)
2. **Triage** — route records: done / needs_review / unsalvageable
3. **AI Planner** — LLM reads skill documentation, picks the right skills for ambiguous records
4. **Enrichment** — municipality resolution (DB), geocoding (Nominatim), web search (Tavily) — only when needed
5. **Re-triage** — final routing with enriched evidence

## Quick Start

```bash
pip install -r requirements.txt
cp .env.example .env  # fill in API keys

# Bootstrap DB
python db/pg_init.py
python scripts/init_data.py --domain real_estate

# Run tests (no DB/API keys needed)
python -m pytest tests/
```

See [CLAUDE.md](CLAUDE.md) for full setup, env vars, and architecture details.

## Adding a New Industry

Three steps:

```bash
# 1. Scaffold
python scripts/scaffold_domain.py --domain sports_ticketing

# 2. Edit the generated stubs:
#    skills/sports_ticketing/skills.yaml        — add your skills
#    seeders/sports_ticketing/manifest.yaml     — add your seeders
#    data/seeds/sports_ticketing/               — drop seed CSVs

# 3. Seed + run
python scripts/init_data.py --domain sports_ticketing
```

No changes to pipeline code. The framework discovers skills and seeders from the manifest.

## Architecture

```
┌─────────────────────────────────────────────────────┐
│                  OrchestrationTeam                   │
│                                                      │
│  Phase 1: Deterministic (cost=low)                   │
│    spell_checker → address_standardizer → fuzzy      │
│                                                      │
│  Phase 2: Triage                                     │
│    done ──────────────────────────────────► exit     │
│    unsalvageable ──────────────────────────► exit    │
│    needs_review ──────────────────────────► Phase 3  │
│                                                      │
│  Phase 3: AI Planner (LLM, plan cached 24h)          │
│    reads skill.md files → outputs ordered plan JSON  │
│                                                      │
│  Phase 4: Planned skills (budget enforced)           │
│    municipality_authority                            │
│    nominatim_geocoder                                │
│    web_search_enricher (Tavily, only if gap found)   │
│                                                      │
│  Phase 5: Re-triage                                  │
└─────────────────────────────────────────────────────┘
```

## Skills

Each skill has two parts:
- `skill.md` — LLM-readable documentation (Purpose, When to Use, Examples, Constraints)
- `skill.py` — deterministic implementation

### Real Estate Skills

| Skill | Cost | What |
|-------|------|------|
| `spell_checker` | low | Fix misspellings (DB-backed, no hardcoded dict) |
| `address_standardizer` | low | Expand abbreviations, normalize quadrants (NW/SE/etc.) |
| `fuzzy_matcher` | low | Canonicalize and compare address variants |
| `municipality_authority` | high | FSA → municipality via PostgreSQL cache |
| `geographic_validator` | medium | Province/city/postal coherence check |
| `nominatim_geocoder` | high | OSM geocoding with PG cache |
| `data_quality_triage` | medium | Route: done / needs_review / unsalvageable |
| `web_search_enricher` | high | Tavily search for low-confidence gaps |
| `skill_planner` | high | LLM picks skill execution order per record |

### Sports Ticketing Skills (proof of generality)

| Skill | Cost | What |
|-------|------|------|
| `event_normalizer` | low | "Leafs vs Habs" → "toronto maple leafs vs montreal canadiens" |
| `ticket_product_categorizer` | low | full_season / half_season / individual / voucher |

### Cross-domain Skills (`skills/_common/`)

- `web_search_enricher` — domain-agnostic core; per-domain parsers in `parsers/<domain>/<gap>.py`
- `skill_planner` — LLM reads skill menu, outputs JSON plan; hallucinations rejected; dep order enforced

## Design Principles

**No hardcoded data in skills.** All domain dictionaries live in DB:
- Spell corrections → `spell_corrections` table
- FSA → municipality → `municipality_lookup_cache`
- Search query templates → `query_pattern_memory`

**Web search is last resort.** Triggered only when `_triage_route == "needs_review"` and a gap is identified. Per-batch `BatchBudget` caps Tavily spend.

**Confidence is weakest-link.** `min(signals)` not average — one bad signal tanks the record.

**Deterministic skills always run.** LLM planner only picks medium/high-cost skills for ambiguous records.

## DB Migrations

```
db/migrations/
├── 003_spell_corrections.sql       # spell_corrections table
├── 004_query_pattern_memory.sql    # query_pattern_memory, source_registry
└── 005_plan_cache.sql              # plan_cache (AI planner, 24h TTL)
```

## Project Layout

```
skills/               # Domain skill implementations
  _common/            # Cross-domain (skill_planner, web_search_enricher)
  real_estate/        # Real estate domain skills + skills.yaml
  sports_ticketing/   # Sports ticketing domain skills + skills.yaml

seeders/              # Idempotent public-data seeders
  real_estate/        # Wikipedia FSA, StatsCan shapefile, spell corrections
  sports_ticketing/   # (add your seeders here)

cleaning/             # Core pipeline modules
  orchestrator_v2.py  # OrchestrationTeam, BatchBudget, run_cleaning_workflow_v2
  municipality_resolver.py
  nominatim_client.py
  llm_client.py       # Anthropic SDK wrapper (tiered: fast/standard/deep)
  cache.py            # WebSearchCache (Tavily + PG-backed)

db/                   # DB layer
  connection.py       # postgres / sqlite switch
  migrations/         # SQL migration files
  pg_query_memory.py  # Query pattern memory helpers

data/seeds/           # Version-controlled seed data
  real_estate/        # spell_corrections.csv, query_packs.yaml
  sports_ticketing/   # query_packs.yaml
  _common/            # Cross-domain query packs

scripts/
  init_data.py        # Seed CLI: python scripts/init_data.py --domain X
  scaffold_domain.py  # New domain CLI: python scripts/scaffold_domain.py --domain X

tests/                # 123 tests, all passing, no real DB/API required
```

## Environment Variables

| Variable | Required | Description |
|----------|----------|-------------|
| `ANTHROPIC_API_KEY` | Yes (or OpenRouter) | Anthropic direct API |
| `OPENROUTER_API_KEY` | Yes (or Anthropic) | OpenRouter API |
| `POSTGRES_DSN` | Yes (if postgres) | `postgresql://user:pass@host/db` |
| `DB_BACKEND` | No | `postgres` or `sqlite` (default: sqlite) |
| `TAVILY_API_KEY` | Yes (web search) | Tavily search API |
| `DB_PATH` | No | SQLite path (default: `data/cleaning.db`) |
