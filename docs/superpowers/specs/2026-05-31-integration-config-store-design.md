# Integration Config Store

**Date:** 2026-05-31
**Status:** Draft — awaiting review

## Context

This is **sub-project #1** of a larger "Integration Builder" vision. The full vision: the
domain-init agent helps a user stand up external data integrations — ingest an API spec,
store credentials, configure endpoints, generate a per-domain reference/lookup table, and
wire a seeder that pulls the data. That is **Tier 1** (structured/API). **Tier 2** is a
web-search fallback that reuses a Tier-1 table schema or synthesizes its own.

The full Integration Builder decomposes into four independent pieces:

1. **Credential & endpoint config store** ← *this spec*
2. API-spec ingestion skill (LLM picks endpoints/params from an OpenAPI spec)
3. Schema generation + generic config-driven API seeder (per-domain table + idempotent puller)
4. Tier-2 web-search fallback

This spec covers **only #1**. It deliberately stops short of spec ingestion, seeders, and
table generation. It is the foundation those later pieces build on.

> **Architecture note / deliberate override:** The recorded decision in
> `project_domain_init_architecture` was "user brings their DB; the framework does not
> generate schema/migrations." Later sub-projects (#3) intentionally reverse that — the
> agent *will* generate per-domain lookup/cleaning tables, because generating those tables
> and doing the research is the agent's core value. This spec (#1) does not generate any
> tables, but flags the reversal here so it is recorded before later stages land.

## Problem

The domain-init Q&A already collects the inputs an integration needs (e.g. `trusted_sources`,
the leagues/entities the user cares about), but nothing in the framework can *hold* an
external source's connection details. There is no place to declare "source X lives at this
base URL, authenticates this way, with the secret in this env var, and exposes these
endpoints." Without that store, sub-projects #2 and #3 have nothing to read from.

## Constraints (existing conventions to follow)

- **Secrets pattern is already settled.** Secrets live in `.env` (gitignored).
  `config.get_config_value(key)` resolves `.env` → `os.environ` → default.
  `.env.example` documents required vars with placeholder values and is committed.
- **Per-domain layout is already settled.** Domain-scoped artifacts live in parallel
  per-domain dirs: `data/seeds/<domain>/`, `seeders/<domain>/`, `skills/<domain>/`.
- **YAML is the existing config format** (`manifest.yaml`, `query_packs.yaml`,
  `column_metadata.yaml`); `pyyaml` is already a dependency.

## Solution

Add a per-domain, per-source **integration config**: a committed YAML file holding all
**non-secret** connection details, with the credential referenced **by env-var name only**.
The raw secret never enters the config file, the agent's context, or logs — the user places
it in `.env` themselves (the same way `ANTHROPIC_API_KEY` / `TAVILY_API_KEY` work today).

A small loader reads a source's YAML and resolves its credential at runtime, raising a clear
error if the named env var is unset.

### Why this credential approach

| Option | Why not |
|--------|---------|
| Agent prompts for the value and writes it to `.env` | Raw secret passes through the tool and may be captured in context/logs. |
| Store credentials in a DB table | Puts secrets in the database; breaks the existing `.env` convention. |
| **Env-var-name only (chosen)** | Secret never touches the agent; config file is safely committable; matches existing pattern exactly. |

## Architecture

```
integrations/
├── __init__.py
├── config.py                 ← loader: load_integration(domain, source) → IntegrationConfig
└── <domain>/
    └── <source>.yaml         ← committed, NO secrets

.env                          ← holds the actual secret value (gitignored, unchanged pattern)
.env.example                  ← documented placeholder appended for each new credential
```

### Config file shape — `integrations/<domain>/<source>.yaml`

Worked example — MiLB rides the **open** MLB StatsAPI (no credential), so `auth.type: none`:

```yaml
domain: sports_ticketing
source: milb_schedule      # slug; matches the filename
base_url: https://statsapi.mlb.com
auth:
  type: none              # open API — no credential; env_var omitted
endpoints:
  schedule:
    path: /api/v1/schedule
    method: GET
    params:               # static/default params; runtime values (dates) filled by later sub-projects
      sportId: 11         # 11=Triple-A, 12=Double-A, 13=High-A, 14=Single-A
  teams:
    path: /api/v1/teams
    method: GET
    params:
      sportId: 11
```

A credentialed source instead names the secret by env-var (value lives in `.env`):

```yaml
auth:
  type: bearer            # bearer | header | query | none
  env_var: SOME_API_KEY   # name only — value lives in .env
  param_name: X-Api-Key   # header name (type=header) or query key (type=query); omit for bearer/none
```

- `auth.type`:
  - `bearer` → `Authorization: Bearer <secret>`
  - `header` → `<param_name>: <secret>` (e.g. `X-Api-Key`)
  - `query`  → `?<param_name>=<secret>`
  - `none`   → no credential; `env_var` omitted; `resolve_credential()` returns `None`
- `endpoints` is a name → `{path, method, params}` map. This sub-project only **stores** it;
  it does not call anything.

### Loader — `integrations/config.py`

```python
@dataclass
class AuthConfig:
    type: str                  # bearer | header | query | none
    env_var: str | None
    param_name: str | None

@dataclass
class Endpoint:
    name: str
    path: str
    method: str
    params: dict

@dataclass
class IntegrationConfig:
    domain: str
    source: str
    base_url: str
    auth: AuthConfig
    endpoints: dict[str, Endpoint]

    def resolve_credential(self) -> str | None:
        """Resolve the secret from .env/env via config.get_config_value.
        Returns None for auth.type == 'none'. Raises if env_var is set but missing."""

def load_integration(domain: str, source: str) -> IntegrationConfig: ...
def list_integrations(domain: str) -> list[str]: ...   # source slugs for a domain
```

- `load_integration` reads `integrations/<domain>/<source>.yaml`, validates required keys,
  and returns a typed `IntegrationConfig`. Missing file → `FileNotFoundError` with a message
  pointing at the expected path. Malformed/missing keys → `ValueError` naming the field.
- `resolve_credential()` uses `config.get_config_value(auth.env_var)`. If `auth.type != "none"`
  and the var is unset/empty, raise `ValueError` with the exact env-var name and a hint to add
  it to `.env`.

### Creating a config

For this sub-project, config creation is **manual / lightweight** — a small helper plus
documentation, not an interactive wizard (that arrives with sub-project #2):

- A helper `write_integration_template(domain, source, env_var=None, auth_type="bearer")`
  writes a commented `integrations/<domain>/<source>.yaml` skeleton. For credentialed
  `auth_type`s it appends a placeholder line (`<ENV_VAR>=your-value-here`) to `.env.example`
  if not already present. For `auth_type="none"` (open APIs) it omits `env_var` and writes
  nothing to `.env.example`; passing a credentialed `auth_type` without an `env_var` raises.
- The user fills in `base_url`/`auth`/`endpoints` and, if credentialed, puts the real secret in `.env`.

## Data flow

```
caller (future seeder / spec-ingestion)
   │  load_integration("sports_ticketing", "milb_schedule")
   ▼
integrations/config.py  ──reads──▶ integrations/sports_ticketing/milb_schedule.yaml
   │                                         (base_url, auth, endpoints)
   │  cfg.resolve_credential()
   ▼
auth.type == "none" → None        (credentialed → config.get_config_value(env_var) → .env / os.environ)
   │
   ▼
IntegrationConfig (typed)  +  resolved secret (in-memory only; None for open APIs)
```

## Error handling

| Condition | Behavior |
|-----------|----------|
| Config file missing | `FileNotFoundError`, message includes expected path. |
| Required key missing / wrong type | `ValueError` naming the field. |
| `auth.type` not in allowed set | `ValueError` listing allowed values. |
| `env_var` set but not in `.env`/environ | `ValueError` with the env-var name + "add it to .env". |
| `auth.type == "none"` | `resolve_credential()` returns `None`, no error. |

## Testing

Pure file + env logic — no DB, no network, no API keys (consistent with the project's
mock-based test suite).

- `load_integration` parses a valid YAML fixture into the correct `IntegrationConfig`.
- Missing file raises `FileNotFoundError` with the path.
- Missing/invalid `auth.type` raises `ValueError`.
- `resolve_credential()` returns the value when the env var is set (monkeypatched).
- `resolve_credential()` raises naming the env var when it is unset.
- `resolve_credential()` returns `None` for `auth.type == "none"`.
- `bearer` / `header` / `query` each surface the right `param_name` semantics.
- `write_integration_template` creates the skeleton and appends to `.env.example` idempotently
  (re-running does not duplicate the placeholder).
- `list_integrations` returns the source slugs present for a domain.

## Out of scope (later sub-projects)

- Reading an OpenAPI/API spec or LLM endpoint selection (#2).
- Actually calling endpoints, generating tables, or seeding data (#3).
- Web-search fallback (#4).
- Any interactive wizard inside `initialize_domain.py`.

## Open questions

None blocking. Layout (`integrations/<domain>/<source>.yaml`), loader location
(`integrations/config.py`), and env-var-name credential binding are settled per review above.
what 