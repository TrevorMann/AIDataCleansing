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

---

## Conversational Interface (`multi_turn_conversation.py`)

Interactive multi-turn conversation for data cleaning and domain research. Two LLM backend paths — pick one.

### Path A — OpenRouter (default)

Supports any model available on OpenRouter (Claude, GPT, Llama, Mistral, etc.). No prompt caching.

```bash
# .env
OPENROUTER_API_KEY=sk-or-...
LLM_BACKEND=openrouter          # optional — auto-detected if key is present
OPENROUTER_MODEL=anthropic/claude-haiku-4-5   # optional, this is the default
```

System prompt sent as a **plain string**. `cache_control` is not used.

### Path B — Anthropic API (direct)

Direct Anthropic API with prompt caching. The base system prompt is cached across turns using `cache_control`, which reduces cost significantly on long conversations.

```bash
# .env
ANTHROPIC_API_KEY=sk-ant-...
LLM_BACKEND=anthropic           # optional — fallback when no OpenRouter key
ANTHROPIC_MODEL=claude-haiku-4-5-20251001   # optional, this is the default
```

System prompt sent as a **list of typed blocks**. The base prompt block gets `cache_control: ephemeral` — Anthropic caches it for 5 minutes, saving input token cost on every turn.

### Running the interface

```bash
python multi_turn_conversation.py
```

On startup it prints the detected backend, model, and active domain:
```
[LLM] backend=openrouter  model=anthropic/claude-haiku-4-5
[domain] active=real_estate  label=Real estate listings — addresses, postal codes, neighbourhoods
[domain] sub_categories=['CA', 'USA', 'NL', 'MX', 'JP']
```

**Commands:**
```
CLEAN Canadian data        # triggers real_estate/CA prompt + cleaning workflow
CLEAN US data              # triggers real_estate/USA prompt
HISTORY                    # show conversation history
QUIT                       # exit
```

**Skill injection:** Certain phrases in your message auto-inject a skill's instructions into the system prompt for that turn:

| Phrase | Skill injected |
|--------|---------------|
| "research healthcare domain" | `domain-architect` |
| "migrate schema to snowflake" | `backend-schema-manager` |
| "build pipeline for ..." | `data-cleaning` |

---

## Prompt Architecture

System prompts are assembled in layers — only load what's needed:

```
prompts/
├── base.py                        ← always loaded: generic data engineer rules
└── domains/
    ├── real_estate/
    │   ├── ca.py                  ← Canada real estate (postal codes, FSA, RE neighbourhoods)
    │   ├── usa.py                 ← USA real estate (ZIP codes, state names, RE neighbourhoods)
    │   ├── nl.py, mx.py, jp.py
    │   └── __init__.py            ← loader: get_prompt(sub) maps CA/USA/NL/... → RULES
    ├── sports_ticketing/          ← stub — populated when domain is initialized
    └── esp/                       ← stub — populated when domain is initialized
```

**build_system_prompt(sub, schema, domain)** assembles: `base + domains/<domain>.get_prompt(sub)`

To add a new country to real estate: create `prompts/domains/real_estate/<cc>.py` with a `RULES` string and add it to `prompts/domains/real_estate/__init__.py`.

---

## Domain Registry (`data/domain_registry.json`)

Tracks which domains are initialized and their metadata. Auto-updated by `scripts/domain.py scaffold`.

```json
{
  "active_domain": "real_estate",
  "domains": {
    "real_estate": {
      "initialized_at": "2026-05-05",
      "label": "Real estate listings — addresses, postal codes, neighbourhoods",
      "sub_category_dimension": "country",
      "sub_categories": ["CA", "USA", "NL", "MX", "JP"],
      "prompt_module": "prompts.domains.real_estate"
    }
  }
}
```

Switch active domain:
```bash
# Edit data/domain_registry.json directly
"active_domain": "sports_ticketing"
```

Or after scaffolding a new domain (it becomes active automatically):
```bash
python scripts/domain.py scaffold --domain sports_ticketing

## Adding a New Domain

### Step 1 — Research & Blueprint (Claude Code)

Open Claude Code and ask it to research the domain:

> "research hospitality domain" or "add architecture for healthcare"

The `domain-architect` skill runs automatically. It:
- Searches for industry-standard identifiers, reference datasets, open data sources
- Defines master data cache strategy (`ref_` tables, escalation tiers)
- Produces `db/blueprints/<domain>_blueprint.md` and `rules/<domain>.md`

Skip this step for simple domains where you already know the data model.

### Step 2 — Scaffold the skeleton

```bash
python scripts/domain.py scaffold --domain hospitality
```

Creates:
```
skills/hospitality/skills.yaml
seeders/hospitality/manifest.yaml
data/seeds/hospitality/
```

### Step 3 — Define skills and seeders

Edit the generated stubs:
- `skills/<domain>/skills.yaml` — declare skills and their cost tier
- `seeders/<domain>/manifest.yaml` — declare idempotent data seeders
- `data/seeds/<domain>/` — drop seed CSVs (spell corrections, query packs)

Register seeders from CLI:
```bash
# Wikipedia FSA seeder
python scripts/domain.py add-seeder --domain hospitality --type wikipedia_fsa \
    --name wikipedia_fsa_ON --country CA --letters K,L,M,N,P

# Stats Can shapefile seeder
python scripts/domain.py add-seeder --domain hospitality --type statscan_shp \
    --name statscan_fsa_BC \
    --fsa-shapefile "F:/data/lfsa000a21a_e.shp" \
    --csd-shapefile "F:/data/lcsd000a25a_e.shp" \
    --country CA --province-pruid 59
```

### Step 4 — Initialize (migrations + seed)

```bash
# Preview what will run
python scripts/domain.py seed --domain hospitality --dry-run

# Apply migrations then seed
python scripts/domain.py init --domain hospitality
```

No pipeline code changes needed. The framework discovers skills and seeders from each domain's manifest.

---

## Using a Different Backend

Postgres is the source of truth. SQLite works out of the box as a local fallback. To add Snowflake, DuckDB, BigQuery, SQL Server, or Redshift:

### Via Claude Code (recommended)

Open Claude Code and say:

> "migrate schema for real_estate to snowflake"

The `backend-schema-manager` skill runs automatically. It:
- Reads `db/migrations/*.sql` + `db/pg_init.py`
- Translates Postgres DDL to the target backend (LLM knowledge + `web_search` for unfamiliar types)
- Generates `db/<backend>_init.py`
- Extends `db/upsert.py` and `db/connection.py` with the new backend branch
- Prints `.env` instructions and any manual steps needed

### Via CLI

```bash
python scripts/domain.py migrate-schema --domain real_estate --target snowflake
```

Prints the current Postgres DDL and instructions for completing the translation in Claude Code. Full automated translation requires the `backend-schema-manager` skill via Claude Code.

### Switch backends

```bash
# .env
DB_BACKEND=snowflake
# or: sqlite, postgres, duckdb, sqlserver, redshift, bigquery
```

All seeder `upsert()` calls go through `db/upsert.py` — no seeder code changes needed when adding a backend. Only `db/upsert.py`, `db/connection.py`, and the new `db/<backend>_init.py` are added.

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
