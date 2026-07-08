# Checkpoint — Integration Builder (2026-05-31)

Resume point for the Integration Builder project. Pick up here tomorrow.

## Where we are

Building the **Integration Builder**: the domain-init agent fetches *real* reference data
(schedules, teams, venues) into **auxiliary lookup/cleaning tables** the cleaning pipeline
validates against — instead of only writing static LLM-authored seed files.

**Tiered, decomposed into 4 sub-projects** (each gets spec → plan → build):

1. **Credential & endpoint config store** — ✅ **DONE** (this session)
2. **API-spec ingestion skill** — ⏭️ next to design
3. Schema generation + generic config-driven `RestApiSeeder` (per-domain aux table + puller)
4. Tier-2 web-search fallback

Tier 1 = structured/API (build first). Tier 2 = web search (fallback).
Per-domain **typed** tables chosen over a generic KV table — generating those tables IS the value prop.

> Policy change made this session: the tool now generates **auxiliary** lookup/cleaning/reference
> tables (keyed to join back to the user's data); it still **never** creates/alters the user's
> **source** tables. Recorded in `CLAUDE.md`, `project_domain_init_architecture` memory, and the
> `2026-05-27` domain-init spec.

## Sub-project #1 — DONE this session

- **Spec:** `docs/superpowers/specs/2026-05-31-integration-config-store-design.md`
- **Code:** `integrations/__init__.py`, `integrations/config.py`
  - pydantic models `IntegrationConfig` / `AuthConfig` / `Endpoint`
  - `load_integration`, `list_integrations`, `write_integration_template`
  - Auth types: `bearer | header | query | none`
  - **Open APIs use `auth.type: none`** → `resolve_credential()` returns `None`, no env var.
    `write_integration_template(..., auth_type="none")` omits `env_var` and skips `.env.example`.
  - Credentialed: secret bound by **env-var name only**, resolved via `config.get_config_value`;
    the agent never touches raw secret values.
- **Real config:** `integrations/sports_ticketing/milb_schedule.yaml` — open MLB StatsAPI
  (`statsapi.mlb.com/api/v1/schedule?sportId=11`, verified HTTP 200, no auth).
- **Tests:** `tests/test_integration_config.py` — **17 passing** (TDD throughout).
  Run with: `venv/bin/python -m pytest tests/test_integration_config.py -v`

## Next: design sub-project #2 (API-spec ingestion)

When we resume, **brainstorm #2 first** (don't jump to code). Key design inputs already settled:

- **Two input paths, no-spec is first-class:**
  - (a) OpenAPI/Swagger spec when one exists.
  - (b) **No-spec → infer from a sample response.** MLB StatsAPI has *no* formal OpenAPI spec —
    the agent hits a known endpoint (from the #1 config), samples JSON, and infers the response
    shape + field meanings. The skill detects which path applies and degrades gracefully.
- Use **pydantic-ai** here (the AI piece; #1 had no AI).
- Output feeds #3: chosen endpoint(s) + params + an inferred response schema.

## Pending input from user

User will write a **full business spec document as the source of truth**. That should inform/
re-anchor the sub-project specs (#2–#4) and possibly the overall decomposition. **Read it before
finalizing #2's design.**

## Status / housekeeping

- **Not committed.** Untracked: `integrations/`, `tests/test_integration_config.py`,
  both `docs/superpowers/specs/2026-05-31-*` and `2026-05-27-*`. Modified (tracked): `CLAUDE.md`.
- **Pre-existing breakage (NOT from this work, do not mistake for ours):**
  - `db/sqlite_helpers.py:34` — `SyntaxError` (unfinished param-ordering fix from earlier schema
    work); blocks any test importing `cli.py`.
  - 16 `tests/test_metadata_annotation.py` failures — from the earlier incomplete schema-migration
    work. `integrations/` imports none of these paths.
  - Full `pytest tests/` will not collect cleanly until `sqlite_helpers.py` is fixed; run the
    integration test file directly for now.
- **Env:** use `venv/bin/python` in WSL (has pydantic 2.13.3 / yaml / pytest). On Windows it's
  `.venv-win\Scripts\python.exe`.

## Memory pointers

- `project_integration_builder` — the multi-stage plan (this project)
- `project_domain_init_architecture` — schema-gen policy update
