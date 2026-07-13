# AI Data Cleaning Pipeline

Domain-agnostic LLM-driven data cleaning framework. Real estate and sports ticketing are the reference implementations — the framework initializes any industry domain via scaffolded skills, seeders, and schemas.

Built on: Anthropic SDK / OpenRouter + PostgreSQL (or SQLite for local dev).

---

## Pipeline Flow (per record)

```
raw record
  │
  ├─ Phase 1: Deterministic  (cost=low, parallel)
  │     spell_checker → address_standardizer → record_linker
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

Confidence uses **weakest-link** (`min()`, not average). Web search is last resort — only triggers when `_triage_route == "needs_review"` and a gap exists.

---

## Two Entry Points

| | `multi_turn_conversation.py` | `cleaning/orchestrator_v2.OrchestrationTeam` |
|---|---|---|
| **Who** | Human / CLI testing | Applications, batch jobs, tests |
| **What** | Interactive conversation loop with tool dispatch | Clean function call, no UI |
| **Conversation history** | Yes — multi-turn with tool use | No — stateless per record |
| **Use when** | Manual testing, debugging, exploring data | Embedding in apps or automation |

---

## Setup (first time)

### Option A — PostgreSQL (production)

```bash
# 1. Install deps
pip install -r requirements.txt

# 2. Configure .env
ANTHROPIC_API_KEY=sk-ant-...       # or OPENROUTER_API_KEY
POSTGRES_DSN=postgresql://user:pass@host/db
DB_BACKEND=postgres
TAVILY_API_KEY=tvly-...

# 3. Init DB schema (runs all migrations idempotently)
python db/pg_init.py

# 4. Seed domain data
python scripts/init_data.py --domain real_estate

# 5. Annotate columns (enriches AI planner — run once, re-run if schema changes)
python scripts/annotate_domain.py --domain real_estate

# 6. Run tests (no DB/API keys needed)
python -m pytest tests/ -v
```

### Option B — SQLite (local development)

```bash
# 1. Install deps
pip install -r requirements.txt

# 2. Configure .env
ANTHROPIC_API_KEY=sk-ant-...       # or OPENROUTER_API_KEY
DB_BACKEND=sqlite
DB_PATH=data/cleaning.db           # default if omitted
TAVILY_API_KEY=tvly-...

# 3. Init DB schema (SQLite — creates tables and seeds municipality schema)
python db/sqlite_init.py

# 4. Seed domain data
python scripts/init_data.py --domain real_estate

# 5. Annotate columns
python scripts/annotate_domain.py --domain real_estate

# 6. Run tests
python -m pytest tests/ -v
```

---

## Initialize a New Domain

No pipeline code changes needed. The framework discovers skills and seeders from each domain's YAML files.

### Recommended: `initialize_domain.py` orchestrator

A single schema-first orchestrator that reads your existing database and walks four
interactive phases. Full step-by-step instructions (Postgres + SQLite) live in
[`docs/runbooks/initialize-domain.md`](docs/runbooks/initialize-domain.md).

```bash
python scripts/initialize_domain.py --domain sports_ticketing
#   Phase 0  Table Registration  — choose which DB tables belong to the domain → domain_registry.json
#   Phase 1  Schema Discovery     — read columns / types / PKs for those tables
#   Phase 2  Annotation           — LLM describes each column → column_metadata
#   Phase 3  Seed Research         — samples real data + Q&A → seed files, then loads them

# Maintenance subcommands:
python scripts/initialize_domain.py --domain sports_ticketing add_table       # register newly-added tables
python scripts/initialize_domain.py --domain sports_ticketing --refresh-seeds # re-run Phase 3 only
python scripts/initialize_domain.py --domain sports_ticketing teardown        # reset init state to re-run
```

Then wire skills in `skills/sports_ticketing/skills.yaml` (copy `_common` entries from the
real_estate reference, adjust config) and declare seeders in
`seeders/sports_ticketing/manifest.yaml`.

### Manual path (lower-level scripts)

The orchestrator drives these; run them directly for full control. Note
`annotate_domain.py` now reads the domain's tables from `domain_registry.json`, so the
domain must be registered (Phase 0, or `initialize_domain.py`) first.

```bash
python scripts/scaffold_domain.py --domain sports_ticketing     # skeleton dirs/files
python scripts/initialize_domain.py --domain sports_ticketing --refresh-seeds  # Q&A + LLM → seed files
python scripts/init_data.py --domain sports_ticketing --dry-run # preview seed load
python scripts/init_data.py --domain sports_ticketing           # load seeds
python scripts/annotate_domain.py --domain sports_ticketing     # annotate columns
```

---

### Field guide (manual path reference)

Use this when hand-editing stubs or reviewing LLM-generated output.

#### `skills/<domain>/skills.yaml`

Declares every skill available for the domain and how the registry should wire it up.

```yaml
domain: sports_ticketing          # must match --domain flag and directory name
version: 1.0

config:                           # domain-wide defaults (available to all skills)
  fuzzy_match_threshold: 0.85     # example: used by record_linker
  web_search_timeout: 5

skills:
  event_normalizer:               # skill key — referenced in plans and logs
    class: skills.sports_ticketing.event_normalizer.EventNormalizer
                                  # Python import path to the skill class (must subclass BaseSkill)
    skill_doc: skills/sports_ticketing/event_normalizer/skill.md
                                  # markdown doc the AI planner reads to decide if it should invoke this skill
    tools: []                     # list of tool names this skill exposes (empty = no sub-tools)
    config:                       # skill-specific config; ${runtime.X} injects a runtime resource
      team_name_field: team_name
      pg_conn: "${runtime.pg_conn}"
    cost: low                     # low | medium | high — planner uses this for budget decisions
    phase: 1                      # 1 = deterministic (always runs), 2 = planned, triage = routing, 3+ = enrichment
    latency_estimate_ms: 200      # used by planner to estimate total plan cost
    depends_on: []                # skill keys that must complete before this one runs

  record_linker:                  # wire a _common skill — no domain-specific code needed
    class: skills._common.record_linker.record_linker.RecordLinker
    skill_doc: skills/_common/record_linker/skill.md
    tools: []
    config:
      blocking_fields: [event_id]
      match_rules:
        - name: event_exact
          fields: [event_id]
          match_type: exact
          weight: 1.0
    cost: low
    phase: 1
    latency_estimate_ms: 150
    depends_on: []
```

**Required fields per skill:** `class`, `cost`, `phase`. Everything else has sensible defaults.

**`cost` values and what they mean:**

| Value | When planner picks it | Typical latency |
|-------|-----------------------|-----------------|
| `low` | Always runs (phase 1) | < 200 ms |
| `medium` | When confidence is borderline | 300–600 ms |
| `high` | Only for `needs_review` records | > 1 s (LLM or external API) |

**`phase` values:**

| Value | Meaning |
|-------|---------|
| `1` | Deterministic — runs on every record unconditionally |
| `triage` | Triage routing — reads outputs of phase 1 skills |
| `2` | Planned — runs only when AI planner selects this skill |
| `3` | Enrichment — web search / external APIs, budget-gated |

**Runtime injection** — any config value prefixed `${runtime.X}` is substituted with the value passed in `SkillRegistry.load(..., runtime={"X": ...})`. Standard runtime keys:

| Key | Type | What |
|-----|------|------|
| `pg_conn` | psycopg connection | Postgres connection |
| `web_cache` | `WebSearchCache` | Tavily search cache |
| `llm_client` | `LLMClients` | Tiered LLM clients |

---

#### `seeders/<domain>/manifest.yaml`

Declares idempotent data loaders. Each seeder runs only when its target rows are absent (or on `--force`).

```yaml
domain: sports_ticketing
description: "Sports ticketing seeders — event catalogue and venue data"

schema_migrations:                # SQL files to apply (in order) before seeders run
  - db/migrations/003_spell_corrections.sql
  # Add custom migration files here for any domain-specific tables

seeders:
  - name: spell_corrections       # seeder key — shown in --dry-run output and logs
    class: seeders.sports_ticketing.spell_corrections.SpellCorrectionsSeeder
                                  # Python import path (must subclass BaseSeeder)
    enabled: true                 # false = skip unless --only <name> is passed
    refresh_cadence: as_needed    # as_needed | daily | monthly | yearly — informational only
    license: internal             # data license; shown in --dry-run output
    config:
      seed_csv: data/seeds/sports_ticketing/spell_corrections.csv
                                  # path passed to the seeder's run() method

  - name: query_packs
    class: seeders.sports_ticketing.query_packs.QueryPackSeeder
    enabled: true
    refresh_cadence: as_needed
    license: internal
    config:
      packs_yaml: data/seeds/sports_ticketing/query_packs.yaml

  - name: venue_lookup            # example of a domain-specific seeder
    class: seeders.sports_ticketing.venue_lookup.VenueLookupSeeder
    enabled: true
    refresh_cadence: monthly
    license: CC BY-SA (Wikipedia)
    config:
      country: CA                 # config is passed directly to seeder.__init__()
```

**Required fields per seeder:** `name`, `class`, `enabled`. Omitting `config` is valid if the seeder takes no arguments.

If you don't have domain-specific lookup data yet, a minimal manifest needs only `spell_corrections` and `query_packs` — both seeders reuse the shared seeder classes.

---

#### `data/seeds/<domain>/` seed files

#### `spell_corrections.csv`

One correction per line. Loaded by `SpellCorrectionsSeeder` into the `spell_corrections` DB table.

```csv
wrong,right,source,confidence
Torontoo,Toronto,manual,1.0
Calgarry,Calgary,manual,1.0
Edmunton,Edmonton,manual,0.95
```

| Column | Required | Description |
|--------|----------|-------------|
| `wrong` | Yes | Misspelled form (case-insensitive match at query time) |
| `right` | Yes | Canonical correct form |
| `source` | Yes | `manual` / `llm_generated` / `stats_model` — for audit trail |
| `confidence` | Yes | 0.0–1.0; corrections below domain threshold are skipped |

Start with the most common misspellings for your domain's key text fields (venue names, team names, city names). You don't need exhaustive coverage — the symspell layer handles phonetic variants automatically.

#### `query_packs.yaml`

Tavily search query templates. Loaded by `QueryPackSeeder` into `query_pattern_memory`. Used by `web_search_enricher` when a gap exists after deterministic cleaning.

```yaml
domain: sports_ticketing

gap_types:                        # each key is a gap type string your triage skill emits
  venue_unresolved:
    seed_queries:                 # {field_name} placeholders are filled from the record at search time
      - "site:seatgeek.com {venue_name} {city}"
      - "{venue_name} {city} arena seating capacity"
      - "site:wikipedia.org {venue_name}"

  team_name_ambiguous:
    seed_queries:
      - "{team_name} official name {sport}"
      - "site:wikipedia.org {team_name} {sport} franchise"

trusted_sources:                  # results from these domains are weighted higher by the parser
  - seatgeek.com
  - ticketmaster.com
  - wikipedia.org
  - espn.com
```

| Field | Required | Description |
|-------|----------|-------------|
| `domain` | Yes | Must match the domain directory name |
| `gap_types.<key>` | Yes | Gap type string your triage/planner skill emits (e.g. `venue_unresolved`) |
| `gap_types.<key>.seed_queries` | Yes | 2–4 query templates; `{field}` expands from the record |
| `trusted_sources` | No | Domains the web parser treats as authoritative |

One gap type per distinct data problem. Three query templates per gap type is usually enough — more queries consume Tavily budget without adding coverage.

### PostgreSQL-specific: run migrations first

For domains that need custom tables (e.g., municipality cache, specialized lookup tables):

```bash
# Add migration file following existing naming convention
# db/migrations/NNN_your_feature.sql

# Apply via pg_init.py (idempotent — skips already-applied migrations)
python db/pg_init.py
```

### SQLite-specific: add tables via sqlite_init.py

For SQLite backends, add any new table DDL to `db/sqlite_init.py` (the `init_db()` function runs `CREATE TABLE IF NOT EXISTS` for all required tables).

---

## Using the Interactive CLI

```bash
python multi_turn_conversation.py
```

The CLI supports multi-turn conversation with these built-in tools:
- `validate_phone` — format and validate phone numbers
- `web_search` — Tavily search for enrichment
- `insert_record` / `update_record` / `delete_record` / `query_records` — CRUD with guardrail validation

Example session:
```
> Show me records that need cleaning for domain real_estate
> Clean the postal codes for the Canadian records
> Update the municipality for record 42 to "Oakville"
```

---

## Programmatic API

```python
from skills.registry import SkillRegistry
from cleaning.orchestrator_v2 import OrchestrationTeam, BatchBudget
from cleaning.llm_client import build_clients
from cleaning.cache import WebSearchCache

# Build clients
clients = build_clients()
pg_conn = ...   # psycopg connection

# Wire up runtime resources
registry = SkillRegistry.load("real_estate", runtime={
    "pg_conn": pg_conn,
    "web_cache": WebSearchCache(pg_conn=pg_conn),
    "llm_client": clients,
})

# Run with budget cap (optional)
budget = BatchBudget(max_queries=50)
team = OrchestrationTeam(registry, batch_budget=budget)

# Process a single record
result = team.process_record({
    "name": "john smith",
    "city": "toronto",
    "postal_code": "M5V2T6",
    "country": "CA",
})

# Batch
from cleaning.orchestrator_v2 import run_cleaning_workflow_v2
report = run_cleaning_workflow_v2(records, domain="real_estate")
print(report.summary_text)
```

---

## Scripts

| Command | Purpose |
|---------|---------|
| `python scripts/init_data.py --domain X` | Seed domain data (idempotent) |
| `python scripts/init_data.py --domain X --dry-run` | Preview seeder plan |
| `python scripts/init_data.py --domain X --only spell_corrections` | Run single seeder |
| `python scripts/annotate_domain.py --domain X` | LLM-annotate all unannotated columns |
| `python scripts/annotate_domain.py --domain X --dry-run` | Show unannotated gaps |
| `python scripts/annotate_domain.py --domain X --force` | Overwrite existing annotations |
| `python scripts/scaffold_domain.py --domain X` | Scaffold new domain skeleton |
| `python scripts/initialize_domain.py --domain X --refresh-seeds` | LLM-assisted, schema-aware seed generation via Q&A |
| `python -m pytest tests/ -v` | Full test suite (no DB/API keys needed) |

---

## Key Files

### Pipeline core

| File | What |
|------|------|
| `cleaning/orchestrator_v2.py` | `OrchestrationTeam` — 5-phase pipeline; `BatchBudget`; `run_cleaning_workflow_v2` |
| `cleaning/llm_client.py` | Anthropic SDK wrapper; tiered (fast/standard/deep); cache control; retry |
| `cleaning/cache.py` | `WebSearchCache` — Tavily + PG-backed search cache; inject as `web_cache` runtime |
| `skills/registry.py` | `SkillRegistry` — loads `skills.yaml`, injects runtime resources, O(1) lookup |
| `skills/base.py` | `BaseSkill` — ABC with `run()`, `clear_audit()`, `log_decision()` |
| `skills/agent.py` | `BaseAgent` — sequential skill executor |

### Domain skills

| Path | What |
|------|------|
| `skills/_common/spell_checker/` | DB-backed spell correction (symspellpy + domain overrides) |
| `skills/_common/address_standardizer/` | Abbreviation expansion, quadrant normalization |
| `skills/_common/record_linker/` | Config-driven fuzzy record matching |
| `skills/_common/skill_planner/` | LLM reads skill docs + column annotations → ordered JSON plan |
| `skills/_common/web_search_enricher/` | Tavily enrichment; per-domain parsers in `parsers/<domain>/` |
| `skills/real_estate/municipality_authority/` | FSA → municipality via PG cache |
| `skills/real_estate/geographic_validator/` | Province / city / postal coherence |
| `skills/real_estate/nominatim_geocoder/` | OSM geocoding with PG cache |
| `skills/real_estate/data_quality_triage/` | Routes record: done / needs_review / unsalvageable |

### Prompt assembly

| File | What |
|------|------|
| `prompts/base.py` | Behavior rules, always loaded |
| `prompts/annotation.py` | Column annotation prompt |
| `prompts/research.py` | Stripped-down postal+municipality research prompt |
| `prompts/domains/real_estate/ca.py` | Canada-specific rules (postal format, municipality, phone) |
| `prompts/domains/real_estate/usa.py` | USA-specific rules |

### DB layer

| File | What |
|------|------|
| `db/connection.py` | Backend switch: returns postgres or sqlite connection |
| `db/pg_init.py` | Postgres schema init + applies all migrations idempotently |
| `db/sqlite_init.py` | SQLite schema init (CREATE TABLE IF NOT EXISTS) |
| `db/pg_helpers.py` | Postgres CRUD helpers |
| `db/sqlite_helpers.py` | SQLite CRUD helpers |
| `db/pg_schema_discovery.py` | Postgres schema introspection |
| `db/sqlite_schema_discovery.py` | SQLite schema introspection |
| `db/pg_query_memory.py` | Query pattern memory (top_queries_for, record_query_outcome) |
| `db/migrations/` | SQL migration files — applied in order by `pg_init.py` |

### Seeders

| File | What |
|------|------|
| `seeders/registry.py` | Discovers and runs seeders from domain manifest |
| `seeders/real_estate/manifest.yaml` | Declares real estate seeders |
| `seeders/real_estate/spell_corrections.py` | Loads spell_corrections.csv → DB |
| `seeders/real_estate/wikipedia_fsa.py` | Loads FSA → municipality from Wikipedia |
| `seeders/real_estate/query_packs.py` | Loads Tavily query templates → DB |

### Services and eval

| File | What |
|------|------|
| `services/metadata_annotation.py` | `MetadataAnnotationService` — LLM-driven column annotation |
| `evals/run.py` | Evaluation harness entry point |
| `evals/llm_judge.py` | LLM-as-judge scoring |
| `evals/datasets/` | Ground-truth eval datasets (real_estate_ca, general_cleaning) |

---

## Skills Reference

### Real Estate

| Skill | Cost tier | What |
|-------|-----------|------|
| `spell_checker` | low | Fix misspellings — DB-backed, no hardcoded dict |
| `address_standardizer` | low | Expand abbreviations, normalize quadrants |
| `record_linker` | low | Canonicalize and fuzzy-match address variants |
| `data_quality_triage` | medium | Route: done / needs_review / unsalvageable |
| `geographic_validator` | medium | Province / city / postal coherence check |
| `municipality_authority` | high | FSA → municipality via PG cache |
| `nominatim_geocoder` | high | OSM geocoding with PG cache |
| `web_search_enricher` | high | Tavily search for low-confidence gaps |
| `skill_planner` | high | LLM picks skill execution order per record |

### Sports Ticketing

| Skill | What |
|-------|------|
| `event_normalizer` | Team name canonicalization, date/time normalization |
| `ticket_product_categorizer` | Product type classification |
| `record_linker` | Fuzzy record matching (wired from `_common`) |

---

## LLM Backends

| Env var | Default model | Override |
|---------|--------------|---------|
| `ANTHROPIC_API_KEY` | `claude-haiku-4-5-20251001` | `ANTHROPIC_MODEL` |
| `OPENROUTER_API_KEY` | `openai/gpt-oss-20b:free` | `OPENROUTER_MODEL` |

Three tiers: `LLM_BACKEND_FAST`, `LLM_BACKEND_STANDARD`, `LLM_BACKEND_DEEP`. All default to `LLM_BACKEND_DEFAULT`. Triage + planner use different tiers; changing one env var switches all pipeline components at once.

---

## DB Migrations

```
db/migrations/
├── 003_spell_corrections.sql         # spell_corrections table
├── 004_query_pattern_memory.sql      # query_pattern_memory, source_registry
├── 005_plan_cache.sql                # plan_cache (AI planner, 24h TTL)
└── 006_column_metadata_annotation_fields.sql  # annotation confidence + source fields
```

Applied in order by `python db/pg_init.py` (idempotent — skips already-applied).

---

## Design Principles

**No hardcoded data in skills.** All domain dictionaries live in DB:
- Spell corrections → `spell_corrections` table
- FSA → municipality → `municipality_lookup_cache`
- Search query templates → `query_pattern_memory`

**Web search is last resort.** Only triggers when `_triage_route == "needs_review"` and a gap exists. Per-batch `BatchBudget` caps Tavily spend.

**Confidence is weakest-link.** `min(signals)` not average — one bad signal tanks the record.

**Deterministic skills always run.** LLM planner only picks medium/high-cost skills for ambiguous records.

**Both DB backends are supported.** SQLite for local dev; PostgreSQL for production. `db/connection.py` handles the switch — skills and seeders are backend-agnostic.

---

## Environment Variables

| Variable | Required | Description |
|----------|----------|-------------|
| `ANTHROPIC_API_KEY` | Yes (or OpenRouter) | Anthropic direct API |
| `OPENROUTER_API_KEY` | Yes (or Anthropic) | OpenRouter API |
| `POSTGRES_DSN` | If `DB_BACKEND=postgres` | `postgresql://user:pass@host/db` |
| `DB_BACKEND` | No | `postgres` or `sqlite` (default: `sqlite`) |
| `DB_PATH` | No | SQLite path (default: `data/cleaning.db`) |
| `TAVILY_API_KEY` | Yes (web search) | Tavily search API |
| `ANTHROPIC_MODEL` | No | Override Anthropic model |
| `OPENROUTER_MODEL` | No | Override OpenRouter model |
| `LLM_BACKEND_DEFAULT` | No | Default backend token (e.g. `gpt-oss`, `anthropic-haiku`) |
| `LLM_BACKEND_FAST` | No | Override fast-tier backend |
| `LLM_BACKEND_STANDARD` | No | Override standard-tier backend |
| `LLM_BACKEND_DEEP` | No | Override deep-tier backend |

---

See [CLAUDE.md](CLAUDE.md) for detailed architecture, prompt layer docs, and confidence scale.
See [MEMORY.md](MEMORY.md) for architecture decisions and known technical debt.
See [docs/code-review-2026-05-20.md](docs/code-review-2026-05-20.md) for the latest code review findings.
