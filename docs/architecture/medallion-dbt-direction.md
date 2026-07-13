# Future direction: dbt / medallion architecture layering

Status: exploratory, not scheduled. Captures a scoping decision from a design
discussion so it isn't re-litigated from scratch later.

## The idea

Introduce a dbt-based medallion architecture (raw → silver → gold) alongside the
existing Python skill pipeline, using `column_metadata` / LLM annotations as the
shared metadata layer feeding both.

## Scope decision

**In scope for this direction:** dbt owns **silver → gold**. Silver is already
the cleaned/structurally-conformed layer (this is where the existing Python
skill pipeline's output — triage, spell correction, enrichment — lands). Gold
is where dbt's declarative SQL transforms, tests, and lineage add value: joins,
aggregations, dimensional modeling, business-logic marts built on top of
already-cleaned records.

**Out of scope for now:**
- **Raw → silver via dbt.** The initial framing was "cleaning = raw → silver,"
  but that's not how this project's cleaning actually works: cleaning here is
  inherently per-record and stateful (LLM triage routing, web-search
  enrichment, confidence scoring, spell-correction voting) — not a good fit
  for declarative SQL transforms. Raw → silver stays owned by the existing
  skills/orchestrator pipeline.
- **API-spec-driven raw ingestion.** `initialize_domain.py` currently only
  reads tables that already exist in the user's DB (source tables are never
  created or altered by the tool — see the Schema generation policy in
  CLAUDE.md). Ingesting from an API spec into a raw landing zone is new
  surface area (auth, pagination, incremental pulls, landing schema) and is a
  separate decision from adding dbt.

## Why this split

dbt's strengths (testable, versioned, declarative, has real lineage) apply
well once data is already clean and typed — that's silver → gold territory.
The value of this project's pipeline is the LLM-driven, per-record cleaning
that gets data *to* silver in the first place; that doesn't map cleanly onto
SQL transforms, so it stays where it is.

## Metadata reuse

If/when this is picked up: `column_metadata` (populated by
`scripts/annotate_domain.py`, Phase 2 of `initialize_domain.py`) is a natural
source for dbt model/column doc blocks (`schema.yml` descriptions), rather
than maintaining annotations twice.
