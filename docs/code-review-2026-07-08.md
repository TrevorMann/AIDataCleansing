# Project Audit — 2026-07-08

Scope: review all three pillars (seed research, schema metadata, escalation cleaning)
against expectations. Method: static review + test suite + read-only DB/dry-run paths.

Tags: **[fix-now]** small, safe to do immediately · **[design]** needs a spec before
building · **[debt]** note it, schedule later.

---

## Pillar 1 — Domain seed research (API-driven, model-swappable, web-grounded)

**What exists:** `initialize_domain.py` Phase 3 → `DomainResearcher.research_with_schema()`
builds a schema+samples+annotations-grounded prompt and asks the LLM for spell
corrections, query packs, and column descriptions. Solid grounding design.

| # | Finding | Tag |
|---|---------|-----|
| 1.1 | **Seed research has no web search.** `research_with_schema()` is a single pure-LLM call — "use websearch and knowledge of the domain" is only half met. Tavily infra (`WebSearchCache`) exists but is never used at init time. Domain facts (team aliases, venue names, postal formats) come from model memory and can be stale/hallucinated. | design |
| 1.2 | **Two parallel LLM client abstractions.** `llm_client_factory.py` (backend factory: openrouter/anthropic) and `cleaning/llm_client.py` (fast/standard/deep tiers). `initialize_domain.py` uses *both*: Phase 2 annotation uses the tiered client, Phase 3 research uses the factory. Different env vars (`LLM_BACKEND` vs `LLM_BACKEND_FAST/...`), different retry/caching behavior, guaranteed drift. Consolidate on the tiered client (it's the richer one) and make the factory a thin shim or retire it. | design |
| 1.3 | **`DomainResearcher` calls `llm_client.messages.create()` raw** — no `log_usage()`, no retry, no `build_message_kwargs()`. Violates the project's own LLM-call rules (CLAUDE.md / llm best practices). Same in `MetadataAnnotationService._annotate_column` (uses `LLMClient.messages_create`, which retries but never logs usage). | fix-now |
| 1.4 | **Single-shot 4096-token JSON response** for the entire bundle (corrections + query packs + descriptions). For a domain with many tables this will truncate mid-JSON and the whole phase fails at `json.loads`. Split into one call per artifact, or use the SDK structured-output path. | design |
| 1.5 | sports_ticketing has seed YAMLs but `seeders/sports_ticketing/manifest.yaml` is minimal and no domain skills are exercised end-to-end (no eval dataset like `evals/datasets/real_estate_ca.json`). | debt |

## Pillar 2 — Schema metadata for client databases

**What exists:** Phase 0–2 registration → discovery → `MetadataAnnotationService`
writing `data_details.column_metadata` with confidence + `is_llm_generated`. Multi-schema
is supported (schema param threaded through); multi-table works via `domain_registry.json`.

| # | Finding | Tag |
|---|---------|-----|
| 2.1 | **Column descriptions are capped at 120 chars** (`result["description"][:120]`, `max_tokens=256`) and generated one column at a time with only 5 sample values and **no sibling-column context**. For "semantic metadata to help AI/LLMs understand a client's database" this is thin: no table-level description, no relationships/FK semantics, no enum value inventories, no units. Upgrade to table-at-a-time annotation with a richer schema (table purpose, per-column semantics, value domain, PII flag, join keys). | design |
| 2.2 | **Silent failure masks bad annotations:** any exception in `_annotate_column` returns `{description: column_name, confidence: 0.3}` — an API outage annotates every column with junk that then blocks re-annotation (upsert `DO NOTHING` without `--force`). Distinguish "LLM said low confidence" from "call failed"; failed calls should not be persisted. | fix-now |
| 2.3 | `column_metadata` PK is `(domain, table_name, column_name)` — **no schema column**, so two tables with the same name in different schemas collide. Fine today, breaks the "arbitrary client DB, many schemas" goal. | debt |
| 2.4 | `initialize_domain._get_table_schema` falls back to SQLite via `PRAGMA` inside `except`, but Phase 2/3 services are psycopg-only — the backend-agnostic dispatch rule (db/helpers dispatchers) isn't applied in the init path. | debt |

## Pillar 3 — Escalation cleaning (deterministic → cheap LLM + web → Sonnet)

**What exists:** `OrchestrationTeam` 5-phase pipeline. Phase 1 deterministic skills run in
parallel; triage short-circuits `done`/`unsalvageable` before any LLM cost; web search is
budget-gated and only fires on `needs_review`; LLM planner is genuinely last-resort. The
cost-minimizing shape you asked for **is** there for the first two tiers.

| # | Finding | Tag |
|---|---------|-----|
| 3.1 | **The third tier (Sonnet-class deep model for stragglers) does not exist in v2.** `EscalationAgent` (deep-tier, prior-search-log-aware, exactly your spec) is only wired into the retired v1 `orchestrator.py`. In v2, records still `needs_review` after the planner just... stay `needs_review`. Port `EscalationAgent` into the v2 pipeline as a Phase 6 skill using `build_client_for_tier("deep")`, budget-capped. | design |
| 3.2 | **Deterministic tier is not self-learning.** `SpellChecker` *reads* `spell_corrections` but nothing ever *writes* new corrections back. When web search or the escalation LLM resolves a value (e.g. fixes a misspelled venue), that resolution should be proposed as a new `spell_corrections` row (with source + confidence, human-reviewable), so the next batch handles it deterministically. Query-pattern memory already does this for search templates (`record_query_outcome`) — extend the same idea to corrections. This is the single highest-leverage feature for "majority handled deterministically". | design |
| 3.3 | **Audit trail is in-memory only.** `audit_log` is returned in `CleaningRunReport` but never persisted — no per-record lineage in the DB, no record→decisions query after the run. For "auditability required", add an `cleaning_audit` table (run_id, record_id, skill, decision, confidence, ts) written per batch. | design |
| 3.4 | `run_cleaning_workflow_v2` swallows all exceptions into an empty report (`except Exception → _empty_report`) — a mid-batch crash loses all processed records and the audit log. Return partial results + the error. | fix-now |
| 3.5 | `flagged_count=0, flags_by_type={}` are hardcoded in the v2 report — routes exist on records but are never aggregated. Cheap fix, gives the batch-level triage summary (X done / Y review / Z unsalvageable) speed reporting needs. | fix-now |
| 3.6 | `skill_planner` defaults to tier `"fast"` — reasonable, but the tier isn't set in `skills.yaml` for either domain, so the "standard" middle tier is effectively unused. | debt |

## Cross-cutting

| # | Finding | Tag |
|---|---------|-----|
| 4.1 | Model table in `cleaning/llm_client.py` is valid (`claude-sonnet-4-6`, `claude-opus-4-7` live). Optional: add `claude-opus-4-8` / `claude-sonnet-5` entries as newer deep-tier options. | debt |
| 4.2 | Line endings: fixed 2026-07-08 (`.gitattributes` + renormalize, commit 80d4ffa). | done |
| 4.3 | Approved field-cleaner spec (2026-06-02) still unimplemented; it overlaps with 3.2 (field-level cleaning + learning) — sequence them together. | design |
| 4.4 | Test suite: **446 passed, 12 skipped** (2m33s) — green baseline. | done |

---

## Recommended build order

1. **Fix-now batch** (1.3, 2.2, 3.4, 3.5) — small PRs, no design needed.
2. **Deep-tier escalation in v2** (3.1) — ports existing code, completes pillar 3.
3. **Self-learning corrections writeback** (3.2, with 4.3 field-cleaner) — biggest cost lever.
4. **Web-grounded seed research** (1.1 + 1.2 + 1.4) — one design: unify LLM client, add Tavily grounding + per-artifact calls to Phase 3.
5. **Rich semantic metadata** (2.1, then 2.3) — table-level annotation upgrade.
