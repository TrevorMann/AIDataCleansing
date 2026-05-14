# AI Data Cleaning Pipeline

Domain-agnostic LLM-driven data cleaning framework. Real estate is the reference implementation — the framework initializes any industry domain via scaffolded skills, seeders, and schemas.

Built on: Anthropic SDK / OpenRouter + PostgreSQL.

---

## Pipeline Flow (per record)

```
raw record
  │
  ├─ Phase 1: Deterministic (cost=low, parallel)
  │     spell_checker → address_standardizer → fuzzy_matcher
  │
  ├─ Phase 2: Triage
  │     confidence ≥ 0.85  →  done
  │     0.60–0.84          →  needs_review
  │     < 0.60             →  unsalvageable
  │
  ├─ Phase 3: AI Planner  (only if needs_review)
  │     LLM reads column_metadata + skill docs → ordered plan JSON
  │     plan cached 24h by record signature
  │
  ├─ Phase 4: Planned skills  (budget-gated)
  │     municipality_authority, nominatim_geocoder, web_search_enricher
  │
  └─ Phase 5: Re-triage → final route
```

---

## Setup (first time)

```bash
# 1. Install deps
pip install -r requirements.txt

# 2. Configure .env
ANTHROPIC_API_KEY=sk-ant-...       # or OPENROUTER_API_KEY
POSTGRES_DSN=postgresql://user:pass@host/db
DB_BACKEND=postgres
TAVILY_API_KEY=tvly-...

# 3. Init DB schema
python db/pg_init.py

# 4. Seed domain data
python scripts/init_data.py --domain real_estate

# 5. Annotate columns (enriches AI planner)
python scripts/annotate_domain.py --domain real_estate

# 6. Run tests (no DB/API keys needed)
python -m pytest tests/ -v
```

---

## Scripts

| Command | Purpose |
|---------|---------|
| `python scripts/init_data.py --domain X` | Seed domain data (idempotent) |
| `python scripts/init_data.py --domain X --dry-run` | Preview seeder plan |
| `python scripts/init_data.py --domain X --only spell_corrections` | Run single seeder |
| `python scripts/annotate_domain.py --domain X` | LLM-annotate all columns |
| `python scripts/annotate_domain.py --domain X --dry-run` | Show unannotated gaps |
| `python scripts/annotate_domain.py --domain X --force` | Overwrite existing annotations |
| `python scripts/scaffold_domain.py --domain X` | Scaffold new domain skeleton |
| `python -m pytest tests/ -v` | Full test suite |

---

## Run Pipeline

```python
from skills.registry import SkillRegistry
from cleaning.orchestrator_v2 import OrchestrationTeam

registry = SkillRegistry.load("real_estate", runtime={
    "pg_conn": pg_conn,
    "llm_client": llm_client,
})
team = OrchestrationTeam(registry)
result = team.process_record(record)
```

---

## LLM Backends

| Env var | Default model |
|---------|--------------|
| `ANTHROPIC_API_KEY` | `claude-haiku-4-5-20251001` — override with `ANTHROPIC_MODEL` |
| `OPENROUTER_API_KEY` | `openai/gpt-oss-20b:free` — override with `OPENROUTER_MODEL` |

All pipeline components (planner, triage, enricher) share the same factory — one env var change switches everything.

---

## Add a New Domain

```bash
# 1. Scaffold skeleton
python scripts/scaffold_domain.py --domain sports_ticketing

# 2. Edit generated stubs:
#    skills/sports_ticketing/skills.yaml     — declare skills + cost tier
#    seeders/sports_ticketing/manifest.yaml  — declare idempotent seeders
#    data/seeds/sports_ticketing/            — drop seed CSVs

# 3. Preview + seed
python scripts/init_data.py --domain sports_ticketing --dry-run
python scripts/init_data.py --domain sports_ticketing

# 4. Annotate
python scripts/annotate_domain.py --domain sports_ticketing
```

No pipeline code changes needed. Framework discovers skills and seeders from each domain's manifest.

---

## Skills

### Real Estate (reference implementation)

| Skill | Cost | What |
|-------|------|------|
| `spell_checker` | low | Fix misspellings (DB-backed, no hardcoded dict) |
| `address_standardizer` | low | Expand abbreviations, normalize quadrants |
| `fuzzy_matcher` | low | Canonicalize and compare address variants |
| `municipality_authority` | high | FSA → municipality via PostgreSQL cache |
| `geographic_validator` | medium | Province/city/postal coherence check |
| `nominatim_geocoder` | high | OSM geocoding with PG cache |
| `data_quality_triage` | medium | Route: done / needs_review / unsalvageable |
| `web_search_enricher` | high | Tavily search for low-confidence gaps |
| `skill_planner` | high | LLM picks skill execution order per record |

### Cross-domain (`skills/_common/`)

- `skill_planner` — reads skill menu + column annotations, outputs JSON plan; hallucinations rejected; dep order enforced
- `web_search_enricher` — domain-agnostic core; per-domain parsers in `parsers/<domain>/<gap>.py`

---

## Design Principles

**No hardcoded data in skills.** All domain dictionaries live in DB:
- Spell corrections → `spell_corrections` table
- FSA → municipality → `municipality_lookup_cache`
- Search query templates → `query_pattern_memory`

**Web search is last resort.** Triggered only when `_triage_route == "needs_review"` and a gap exists. Per-batch `BatchBudget` caps Tavily spend.

**Confidence is weakest-link.** `min(signals)` not average — one bad signal tanks the record.

**Deterministic skills always run.** LLM planner only picks medium/high-cost skills for ambiguous records.

---

## DB Migrations

```
db/migrations/
├── 003_spell_corrections.sql       # spell_corrections table
├── 004_query_pattern_memory.sql    # query_pattern_memory, source_registry
└── 005_plan_cache.sql              # plan_cache (AI planner, 24h TTL)
```

Run in order via `python db/pg_init.py` (applies all migrations idempotently).

---

## Project Layout

```
skills/               # Domain skill implementations
  _common/            # Cross-domain (skill_planner, web_search_enricher)
  real_estate/        # Real estate domain skills + skills.yaml
  sports_ticketing/   # Sports ticketing domain skills + skills.yaml

seeders/              # Idempotent public-data seeders
  real_estate/        # Wikipedia FSA, spell corrections, query packs
  sports_ticketing/

services/             # Application services
  metadata_annotation.py  # LLM-driven column annotation (MetadataAnnotationService)

cleaning/             # Core pipeline modules
  orchestrator_v2.py  # OrchestrationTeam, BatchBudget, run_cleaning_workflow_v2
  llm_client.py       # Anthropic SDK wrapper (tiered: fast/standard/deep)
  cache.py            # WebSearchCache (Tavily + PG-backed)

prompts/              # LLM prompt assembly
  base.py             # Always-loaded behavior rules
  annotation.py       # Column annotation prompt
  domains/            # Per-domain, per-subcategory prompt rules

db/                   # DB layer
  connection.py       # postgres / sqlite switch
  pg_init.py          # Schema init + migrations
  migrations/         # SQL migration files

data/seeds/           # Version-controlled seed data
  real_estate/        # spell_corrections.csv, query_packs.yaml
  _common/            # Cross-domain query packs

scripts/
  init_data.py        # Seed CLI
  annotate_domain.py  # Column annotation CLI
  scaffold_domain.py  # New domain scaffolding

tests/                # 163 tests, all passing, no real DB/API required
```

---

## Environment Variables

| Variable | Required | Description |
|----------|----------|-------------|
| `ANTHROPIC_API_KEY` | Yes (or OpenRouter) | Anthropic direct API |
| `OPENROUTER_API_KEY` | Yes (or Anthropic) | OpenRouter API |
| `POSTGRES_DSN` | Yes (if postgres) | `postgresql://user:pass@host/db` |
| `DB_BACKEND` | No | `postgres` or `sqlite` (default: sqlite) |
| `TAVILY_API_KEY` | Yes (web search) | Tavily search API |
| `DB_PATH` | No | SQLite path (default: `data/cleaning.db`) |
| `ANTHROPIC_MODEL` | No | Override Anthropic model |
| `OPENROUTER_MODEL` | No | Override OpenRouter model |

---

See [CLAUDE.md](CLAUDE.md) for detailed architecture, prompt layer docs, confidence scale, and hardcoded data policy.
