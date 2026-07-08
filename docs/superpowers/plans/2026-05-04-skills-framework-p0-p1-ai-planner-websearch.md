# Skills Framework: P0 Bugs + P1 Restoration + AI Planner + WebSearch

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make data-cleaning skills framework correct (P0), restore lost capabilities (P1), then add AI-driven skill orchestration with smart web-search enrichment.

**Architecture:** Three-tier execution. (1) Deterministic skills always run cheap. (2) Fuzzy/decision-tree skills run on partial confidence. (3) LLM Planner + WebSearchEnricher run only when confidence below threshold. Planner reads `skill.md` files + outputs ordered skill list. Web search uses existing Tavily API (`cleaning/cache.py`), persists queries-that-worked to PG cache for reuse.

**Tech Stack:** Python 3.12, Anthropic SDK (via `cleaning/llm_client.py`), Tavily web search, Postgres (`db/pg_*.py`), pytest.

---

## Stage A — P0 Bug Fixes

Fix correctness bugs blocking everything else. No new capabilities. Pure correctness.

### Task A1: Reset `BaseAgent.decisions_log` per record

**Files:**
- Modify: `skills/agent.py:23, 25-45`
- Test: `tests/test_full_agent_pipeline.py` (add new test)

**Why:** `decisions_log` set in `__init__`, persists across records → cross-record contamination + memory leak.

- [ ] **Step 1: Write failing test for log isolation**

```python
def test_decisions_log_isolated_per_record(registry):
    agent = BaseAgent("X", ["spell_checker"], registry)
    rec1 = {"municipality": "scarbbrough"}
    rec2 = {"municipality": "Toronto"}
    agent.execute(rec1)
    agent.execute(rec2)
    # rec2 must not contain rec1's decisions
    assert all("scarbbrough" not in d.get("decision","") for d in rec2.get("_decisions", []))
```

- [ ] **Step 2: Run test, expect fail**

`pytest tests/test_full_agent_pipeline.py::test_decisions_log_isolated_per_record -v`

- [ ] **Step 3: Fix `agent.py` — scope log to call**

```python
def execute(self, record):
    record_decisions = []
    for skill_name in self.skill_names:
        skill = self.registry.get(skill_name)
        if not skill:
            continue
        record = skill.run(record, self.tools)
        if "_decisions" in record:
            record_decisions.extend(record["_decisions"])
    record["_decisions"] = record_decisions
    return record
```

Drop `self.decisions_log` field. Drop `get_decisions_log()`.

- [ ] **Step 4: Run all tests, expect pass**

`pytest tests/ -v`

- [ ] **Step 5: Commit**

```bash
git add skills/agent.py tests/test_full_agent_pipeline.py
git commit -m "fix: scope agent decisions log per record (no leak across records)"
```

---

### Task A2: Cross-record fuzzy address matching

**Files:**
- Rewrite: `skills/real_estate/fuzzy_matcher/fuzzy_matcher.py:16-41` (replace useless `run()`)
- Modify: `skills/real_estate/fuzzy_matcher/skill.md` (clarify cross-record semantics)
- Test: `tests/test_full_agent_pipeline.py` (add address variant tests)

**Why:** Current `run()` self-compares (always returns 1.0). User wants: "123 Main st, st Catherine" matches "123 main street, saint catherine" — same address, different forms.

**Approach:** Skill takes record + optional `candidates` list (other records). Normalizes both with shared `_canonicalize()` then runs token+char similarity. Output: `_address_match_candidates` (list of {id, similarity}) + decision log.

- [ ] **Step 1: Write failing tests for variant pairs**

```python
def test_fuzzy_matches_st_to_street():
    fm = FuzzyMatcher({"threshold": 0.85})
    sim = fm.compare("123 Main st", "123 main street")
    assert sim >= 0.85

def test_fuzzy_matches_saint_catherine():
    fm = FuzzyMatcher({"threshold": 0.85})
    sim = fm.compare("st Catherine", "saint catherine")
    assert sim >= 0.85

def test_fuzzy_matches_full_variant():
    fm = FuzzyMatcher({"threshold": 0.85})
    sim = fm.compare("123 Main st, st Catherine", "123 main street, saint catherine")
    assert sim >= 0.90
```

- [ ] **Step 2: Run tests, expect fail**

`pytest tests/test_full_agent_pipeline.py -k fuzzy -v`

- [ ] **Step 3: Implement `_canonicalize` + `compare`**

```python
_CANON_MAP = {
    "st": "street", "saint": "street",  # ambiguous handled separately
    "ave": "avenue", "blvd": "boulevard", "rd": "road",
    "ln": "lane", "dr": "drive", "ct": "court", "pkwy": "parkway",
    "ter": "terrace", "pl": "place", "sq": "square",
}

def _canonicalize(self, text: str) -> str:
    text = text.lower().strip()
    text = re.sub(r"[,.]", " ", text)
    text = " ".join(text.split())
    tokens = text.split()
    out = []
    for i, tok in enumerate(tokens):
        # "st Catherine" → "saint catherine" (st before proper noun = saint)
        # "Main st" → "main street" (st after proper noun = street)
        if tok == "st":
            next_tok = tokens[i+1] if i+1 < len(tokens) else ""
            if next_tok and next_tok[0].isalpha() and next_tok not in self._known_streets:
                out.append("saint")
            else:
                out.append("street")
        elif tok in _CANON_MAP:
            out.append(_CANON_MAP[tok])
        else:
            out.append(tok)
    return " ".join(out)

def compare(self, text1: str, text2: str) -> float:
    c1 = self._canonicalize(text1)
    c2 = self._canonicalize(text2)
    return self._compute_similarity(c1, c2)
```

Update `run()`: takes `candidates` from `tools` dict; iterates; emits `_address_match_candidates`.

- [ ] **Step 4: Run tests, expect pass**

`pytest tests/test_full_agent_pipeline.py -k fuzzy -v`

- [ ] **Step 5: Commit**

```bash
git add skills/real_estate/fuzzy_matcher/ tests/
git commit -m "fix(fuzzy_matcher): canonicalize address variants before compare; cross-record matching"
```

---

### Task A3: Drop single-letter directionals, keep quadrants only

**Files:**
- Modify: `skills/real_estate/address_standardizer/address_standardizer.py:37-46, 93-96`
- Modify: `skills/real_estate/address_standardizer/skill.md`
- Test: `tests/test_full_agent_pipeline.py`

**Why:** `\bn\b` matches single "n" anywhere → false expansions. Quadrants (NE/NW/SE/SW) unambiguous. Missing quadrant → flag for review (don't guess).

- [ ] **Step 1: Write failing tests**

```python
def test_quadrant_expanded():
    s = AddressStandardizer()
    assert "Northeast" in s._standardize("123 Main St NE")
    assert "Northwest" in s._standardize("456 Pine Rd NW")

def test_single_letter_directional_not_expanded():
    s = AddressStandardizer()
    # "Doe N Main" must NOT become "Doe North Main"
    out = s._standardize("123 Doe N Main St")
    assert "North" not in out

def test_missing_quadrant_flagged_when_postal_implies_one():
    # implementation to be added in geographic_validator pass — placeholder
    pass
```

- [ ] **Step 2: Run, expect fail**

`pytest tests/test_full_agent_pipeline.py -k directional -v`

- [ ] **Step 3: Replace directionals dict with quadrants only**

```python
# REPLACE self.directionals
self.quadrants = {
    r"\bne\b": "Northeast",
    r"\bnw\b": "Northwest",
    r"\bse\b": "Southeast",
    r"\bsw\b": "Southwest",
}
# Remove single-letter N/E/S/W entries entirely
```

In `_standardize`, replace `for abbr, full in self.directionals.items()` loop with `self.quadrants`. Run quadrant expansion BEFORE street-type expansion (so `NE` doesn't get touched mid-token).

- [ ] **Step 4: Run, expect pass**

`pytest tests/test_full_agent_pipeline.py -v`

- [ ] **Step 5: Commit**

```bash
git add skills/real_estate/address_standardizer/ tests/
git commit -m "fix(address_standardizer): drop single-letter directionals; expand quadrants only"
```

---

### Task A4: SpellChecker uses fuzzy_matcher (DRY + correctness)

**Files:**
- Modify: `skills/real_estate/spell_checker/spell_checker.py:75-128` (delete `_similarity`, use registry)
- Modify: `skills/real_estate/spell_checker/skill.md`
- Test: `tests/test_full_agent_pipeline.py`

**Why:** SpellChecker rolls own naive `_similarity` (broken on length diff). FuzzyMatcher already has Levenshtein. Compose, don't duplicate.

- [ ] **Step 1: Write failing test for short-vs-long**

```python
def test_spell_checker_uses_fuzzy_for_short_typo():
    # "scarb" → "scarborough" should still be a fuzzy hit
    sc = SpellChecker({"threshold": 0.60})
    fm = FuzzyMatcher({"threshold": 0.60})
    out, _ = sc._correct_text("scarb", "city", {"fuzzy_matcher": fm})
    assert out.lower() == "scarborough"
```

- [ ] **Step 2: Run, expect fail**

`pytest tests/test_full_agent_pipeline.py -k spell_checker_uses_fuzzy -v`

- [ ] **Step 3: Refactor**

Delete `SpellChecker._similarity`. In `_correct_text`, when `tools.get("fuzzy_matcher")` present, call `fuzzy.compare(text_lower, wrong)`. Pick best match above threshold.

Wire `tools={"fuzzy_matcher": registry.get("fuzzy_matcher")}` in `BaseAgent.__init__` (or pass in `OrchestrationTeam`).

- [ ] **Step 4: Run all, expect pass**

`pytest tests/ -v`

- [ ] **Step 5: Commit**

```bash
git add skills/real_estate/spell_checker/ skills/agent.py tests/
git commit -m "refactor(spell_checker): use fuzzy_matcher for similarity; wire tools dict"
```

---

### Task A5: DataQualityTriage — min/product confidence

**Files:**
- Modify: `skills/real_estate/data_quality_triage/data_quality_triage.py:65-95`
- Test: `tests/test_full_agent_pipeline.py`

**Why:** Average hides weak signals. Min = conservative, product = calibrated. Pick min (matches "weakest link" semantics).

- [ ] **Step 1: Write failing test**

```python
def test_triage_uses_min_confidence():
    triage = DataQualityTriageAgent()
    rec = {"_municipality_confidence": 0.5, "_geographic_validated": True,
           "_agent_decisions": [], "address": "x", "city": "y",
           "postal_code": "M1A1B1", "municipality": "z", "country": "CA"}
    out = triage.run(rec)
    # min(0.5, 0.85, 0.9) = 0.5, NOT avg(0.75)
    assert out["_triage_data_confidence"] <= 0.6
```

- [ ] **Step 2: Run, expect fail**
- [ ] **Step 3: Switch `_evaluate_confidence` from `sum/len` to `min(scores)`**
- [ ] **Step 4: Run, expect pass**
- [ ] **Step 5: Commit:** `fix(triage): use min confidence (weakest-link), not avg`

---

## Stage B — P1 Restoration (re-integrate lost capabilities)

Bring back PG cache, LLM client, WebSearchCache, Nominatim, **municipality DB resolver**, and **idempotent public-data seeders** from `codex/postgres-backend-migration`. Kill all hardcoded data dicts in skills. Wire into `orchestrator_v2`.

### Task B1: Cherry-pick infrastructure modules

**Files:**
- Copy from `codex/postgres-backend-migration`:
  - `cleaning/llm_client.py`, `cleaning/cache.py`, `cleaning/flags.py`
  - `cleaning/municipality_data.py` (Wikipedia + shapefile loaders)
  - `cleaning/municipality_resolver.py` (DB-backed resolver — replaces hardcoded FSA dict)
  - `db/` directory entire (`pg_init.py`, `sqlite_init.py`, `sqlite_municipality_schema.py`, `connection.py`, `pg_vector.py`)
- Modify: `requirements.txt` (add `anthropic`, `psycopg[binary]`, `requests`, `beautifulsoup4`, `pyshp`)

- [ ] **Step 1: Inspect target files on origin branch**

```bash
git show codex/postgres-backend-migration -- cleaning/ db/ --stat
```

- [ ] **Step 2: Cherry-pick the modules**

```bash
git checkout codex/postgres-backend-migration -- \
    cleaning/llm_client.py \
    cleaning/cache.py \
    cleaning/flags.py \
    cleaning/municipality_data.py \
    cleaning/municipality_resolver.py \
    db/
```

- [ ] **Step 3: Add deps to `requirements.txt`**

```
anthropic>=0.40
psycopg[binary]>=3.2
requests>=2.32
beautifulsoup4>=4.12
pyshp>=2.3
```

- [ ] **Step 4: Verify imports**

```bash
python -c "from cleaning.llm_client import LLMClient; from cleaning.cache import WebSearchCache; from cleaning.municipality_resolver import resolve_municipality; from cleaning.municipality_data import load_wikipedia_fsas"
```

- [ ] **Step 5: Run cherry-picked tests against new branch**

```bash
pytest tests/cleaning/test_municipality_resolver.py tests/test_municipality_e2e.py -v
```

Fix any breakage from missing deps before continuing.

- [ ] **Step 6: Commit:** `chore: restore llm_client, web cache, flags, municipality resolver + loaders, db modules from postgres branch`

---

### Task B2: Kill hardcoded FSA dict — `MunicipalityAuthority` reads PG cache

**Files:**
- Rewrite: `skills/real_estate/municipality_authority/municipality_authority.py:17-135` (DELETE 100+ line hardcoded dict)
- Modify: `skills/real_estate/skills.yaml` (pass DB connection through config)
- Modify: `skills/registry.py` (allow runtime injection of shared resources like `pg_conn`)
- Test: `tests/cleaning/test_postgres_backend.py` (existing, extend)

**Why:** User rule: "no manual overrides, all from cache." DB allows updates without code change. Hardcoded dict bypasses everything.

**Approach:**
1. Skill takes `pg_conn` (or sqlite conn) at construct time
2. Inside `run()`, calls `cleaning.municipality_resolver.resolve_municipality(conn, fsa, ...)`
3. Resolver hits `municipality_lookup_cache` table; if miss, returns `None` + flags `unknown_fsa` for downstream web_search
4. Cache `source` column tracked through to decision log: "Resolved via wikipedia source" / "via stats_canada"

- [ ] **Step 1: Write failing test — DB hit replaces hardcoded dict**

```python
def test_municipality_authority_reads_db_not_hardcoded(pg_conn):
    pg_conn.execute(
        "INSERT INTO municipality_lookup_cache (fsa, municipality, source, confidence) "
        "VALUES ('M1A','Scarborough','wikipedia',0.95)"
    )
    pg_conn.commit()

    skill = MunicipalityAuthorityAgent(config={"pg_conn": pg_conn})
    record = {"postal_code": "M1A 1B1", "municipality": ""}
    out = skill.run(record)

    assert out["municipality"] == "Scarborough"
    assert out["_municipality_confidence"] == 0.95
    assert any("wikipedia" in d.get("reason","") for d in out["_decisions"])

def test_municipality_authority_returns_unknown_on_db_miss(pg_conn):
    skill = MunicipalityAuthorityAgent(config={"pg_conn": pg_conn})
    record = {"postal_code": "Z9Z 9Z9", "municipality": ""}
    out = skill.run(record)
    assert out.get("_municipality_confidence", 0) == 0.0
    # Flags for downstream web_search to attempt
    assert any(d.get("decision","").startswith("Unknown FSA") for d in out["_decisions"])

def test_no_hardcoded_dict_in_source():
    # Lock the regression — fail if anyone adds the dict back
    src = Path("skills/real_estate/municipality_authority/municipality_authority.py").read_text()
    assert "M1A" not in src, "hardcoded FSA dict reintroduced — load from DB instead"
    assert "fsa_to_municipality" not in src
```

- [ ] **Step 2: Run, expect fail**

`pytest tests/cleaning/test_postgres_backend.py -k municipality_authority -v`

- [ ] **Step 3: Rewrite `municipality_authority.py`**

```python
from cleaning.municipality_resolver import resolve_municipality

class MunicipalityAuthorityAgent(BaseSkill):
    def __init__(self, config=None):
        super().__init__(config)
        self.domain = "real_estate"
        self.trust_postal = self.config.get("trust_postal_over_name", True)
        self.escalate_threshold = self.config.get("escalate_confidence_threshold", 0.60)
        self.conn = self.config.get("pg_conn")  # injected at registry load
        if not self.conn:
            raise ValueError("MunicipalityAuthorityAgent requires pg_conn in config")

    def run(self, input_data, tools=None):
        decisions = []
        postal_code = input_data.get("postal_code", "")
        upstream = input_data.get("municipality", "")
        fsa = postal_code[:3].upper().replace(" ", "") if postal_code else ""

        if not fsa:
            return input_data

        result = resolve_municipality(self.conn, fsa)  # returns {municipality, source, confidence} or None

        if not result:
            decisions.append(self.log_decision(
                f"Unknown FSA: {fsa}",
                "FSA not in DB — needs web_search enrichment",
                confidence=0.0,
            ))
            input_data["_decisions"] = decisions
            input_data["_unknown_fsa"] = fsa  # signal to enricher
            return input_data

        # ... rest of conflict resolution unchanged, but uses result["municipality"]
        # Decision logs reference result["source"] (wikipedia / stats_canada / web)
```

- [ ] **Step 4: Update skills.yaml — config now references shared resources**

```yaml
municipality_authority:
  class: skills.real_estate.municipality_authority.municipality_authority.MunicipalityAuthorityAgent
  skill_doc: skills/real_estate/municipality_authority/skill.md
  config:
    trust_postal_over_name: true
    escalate_confidence_threshold: 0.60
    pg_conn: "${runtime.pg_conn}"  # placeholder, registry resolves at load
```

- [ ] **Step 5: Update `registry.py` to accept + inject runtime resources**

```python
@classmethod
def load(cls, domain, config_path=None, runtime=None):
    """runtime: dict of shared resources (pg_conn, web_cache, llm_client) injected into skills."""
    registry = cls()
    registry.runtime = runtime or {}
    registry.load_domain(domain, config_path)
    return registry

def _register_skill(self, skill_name, skill_def, domain):
    ...
    merged_config = {**self.config, **skill_def.get("config", {})}
    # Resolve ${runtime.X} placeholders
    for k, v in list(merged_config.items()):
        if isinstance(v, str) and v.startswith("${runtime."):
            key = v[len("${runtime."):-1]
            merged_config[k] = self.runtime.get(key)
    skill_instance = skill_class(merged_config)
    ...
```

- [ ] **Step 6: Run all tests, expect pass**

`pytest tests/ -v`

- [ ] **Step 7: Commit:** `fix(municipality_authority): kill hardcoded FSA dict; use DB resolver via injected pg_conn`

---

### Task B3: Same pattern for `SpellChecker.corrections` (kill hardcoded)

**Files:**
- Modify: `skills/real_estate/spell_checker/spell_checker.py:16-28` (DELETE hardcoded misspellings dict)
- New: `cleaning/spell_corrections_data.py` (loader from public dataset OR seed file)
- New: `db/migrations/spell_corrections_schema.sql`

**Why:** Same rule applies. Misspelling dict belongs in DB, not code.

- [ ] **Step 1: Schema**

```sql
CREATE TABLE IF NOT EXISTS spell_corrections (
    wrong TEXT PRIMARY KEY,
    right TEXT NOT NULL,
    domain TEXT NOT NULL,
    source TEXT NOT NULL,         -- 'manual_seed', 'wikipedia_redirects', 'crowdsourced'
    confidence REAL DEFAULT 1.0,
    added_at TIMESTAMPTZ DEFAULT NOW()
);
CREATE INDEX idx_spell_corr_domain ON spell_corrections(domain);
```

- [ ] **Step 2: Loader**

`cleaning/spell_corrections_data.py`:
- `load_seed_corrections(conn, seed_file)` — idempotent, reads CSV/YAML, INSERT ON CONFLICT DO NOTHING
- `load_wikipedia_redirects(conn, terms_list)` — for each term, query Wikipedia API for redirects, store as corrections
- `get_corrections_dict(conn, domain)` — returns dict for skill to use at construct time

- [ ] **Step 3: Tests**
- [ ] **Step 4: Modify SpellChecker to load from DB at construct time** (not per-record — cached in instance)
- [ ] **Step 5: Add lock test:** `assert "scarbbrough" not in spell_checker_source`
- [ ] **Step 6: Commit:** `fix(spell_checker): kill hardcoded misspellings; load from DB seeded by public sources`

---

### Task B4: Domain-agnostic seeder framework + manifests

**Files:**
- New: `seeders/__init__.py` (top-level, NOT under `cleaning/` — domain-neutral)
- New: `seeders/base.py` (abstract `Seeder` class)
- New: `seeders/registry.py` (load seeders per domain manifest)
- New: `seeders/real_estate/__init__.py`
- New: `seeders/real_estate/wikipedia_fsa.py`
- New: `seeders/real_estate/statscan_shapefile.py`
- New: `seeders/real_estate/spell_corrections.py`
- New: `seeders/real_estate/manifest.yaml` (domain-level seed plan)
- New: `data/seeds/real_estate/spell_corrections.csv` (version-controlled seed CSV)
- New: `data/seeds/real_estate/README.md` (sources + licensing)
- New: `scripts/init_data.py` (CLI: domain-aware)
- New: `scripts/scaffold_domain.py` (CLI: generate skeleton for new industry)

**Why:** User wants new industry next (sports/ticketing). Framework must be domain-neutral. Adding a domain = drop in: `skills/<domain>/`, `seeders/<domain>/`, `data/seeds/<domain>/`. No code changes elsewhere.

**Layout:**

```
seeders/
├── base.py                       # Seeder ABC (domain-neutral)
├── registry.py                   # Discovers seeders per domain
├── real_estate/
│   ├── manifest.yaml
│   ├── wikipedia_fsa.py
│   ├── statscan_shapefile.py
│   └── spell_corrections.py
└── sports_ticketing/             # NEXT — empty until next industry
    └── manifest.yaml

data/seeds/
├── real_estate/
│   ├── spell_corrections.csv
│   └── README.md                 # Sources, licensing, refresh cadence
└── sports_ticketing/             # NEXT
```

**`seeders/base.py`:**

```python
from abc import ABC, abstractmethod
from typing import Any

class Seeder(ABC):
    """Base class for idempotent public-data seeders. Domain-neutral."""

    name: str          # 'wikipedia_fsa'
    domain: str        # 'real_estate'
    target_table: str  # 'municipality_lookup_cache'
    source_tag: str    # 'wikipedia' (provenance, written to DB source col)
    schema_required: list[str] = []  # tables that must exist (validated)

    @abstractmethod
    def fetch(self) -> Any:
        """Pull data from public source. Return raw payload."""

    @abstractmethod
    def parse(self, payload: Any) -> list[dict]:
        """Parse payload into rows ready for insert."""

    @abstractmethod
    def upsert(self, conn, rows: list[dict]) -> int:
        """Idempotent upsert — INSERT ... ON CONFLICT DO NOTHING/UPDATE."""

    def validate_schema(self, conn):
        """Pre-flight: assert required tables exist."""
        for tbl in self.schema_required:
            assert _table_exists(conn, tbl), f"{tbl} missing — run migrations first"

    def run(self, conn) -> int:
        self.validate_schema(conn)
        payload = self.fetch()
        rows = self.parse(payload)
        return self.upsert(conn, rows)
```

**`seeders/real_estate/manifest.yaml`:**

```yaml
domain: real_estate
description: "Real estate data seeders — Toronto/Canada focus"
schema_migrations:
  - db/migrations/001_municipality_tables.sql
  - db/migrations/003_spell_corrections.sql
seeders:
  - name: wikipedia_fsa
    class: seeders.real_estate.wikipedia_fsa.WikipediaFSASeeder
    enabled: true
    refresh_cadence: monthly
    license: "CC BY-SA (Wikipedia)"
  - name: statscan_shapefile
    class: seeders.real_estate.statscan_shapefile.StatsCanShapefileSeeder
    enabled: false   # disabled by default — needs file download
    refresh_cadence: yearly
    license: "Statistics Canada Open License"
    config:
      shapefile_path: "data/raw/lcsd000a25a_e.shp"
  - name: spell_corrections
    class: seeders.real_estate.spell_corrections.SpellCorrectionsSeeder
    enabled: true
    refresh_cadence: as_needed
    license: "internal"
    config:
      seed_csv: "data/seeds/real_estate/spell_corrections.csv"
```

**`seeders/registry.py`:**

```python
import yaml
from importlib import import_module
from pathlib import Path

class SeederRegistry:
    """Load + run domain seeders from manifest."""

    def __init__(self, domain: str):
        self.domain = domain
        manifest_path = Path(__file__).parent / domain / "manifest.yaml"
        if not manifest_path.exists():
            raise FileNotFoundError(f"No manifest for domain: {domain}")
        self.manifest = yaml.safe_load(open(manifest_path))
        self.seeders = self._load_seeders()

    def _load_seeders(self):
        seeders = []
        for entry in self.manifest.get("seeders", []):
            if not entry.get("enabled", True):
                continue
            class_path = entry["class"]
            mod_name, cls_name = class_path.rsplit(".", 1)
            cls = getattr(import_module(mod_name), cls_name)
            instance = cls(config=entry.get("config", {}))
            seeders.append(instance)
        return seeders

    def run_all(self, conn, only: list[str] = None, dry_run: bool = False) -> dict:
        """Run all seeders. Returns {seeder_name: rows_added}."""
        results = {}
        for s in self.seeders:
            if only and s.name not in only:
                continue
            if dry_run:
                print(f"[{self.domain}/{s.name}] DRY — target={s.target_table}, source={s.source_tag}")
                results[s.name] = -1
                continue
            try:
                count = s.run(conn)
                results[s.name] = count
                print(f"[{self.domain}/{s.name}] → {count} rows ({s.source_tag})")
            except Exception as e:
                print(f"[{self.domain}/{s.name}] FAILED: {e}")
                results[s.name] = None
        return results
```

**`scripts/init_data.py`:**

```python
"""CLI: seed public datasets per domain. Idempotent."""
import argparse
from db.connection import get_connection, get_pg_dsn
from seeders.registry import SeederRegistry

def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--domain", required=True, help="Domain manifest to load (real_estate, sports_ticketing, ...)")
    ap.add_argument("--only", nargs="*", help="Specific seeder names")
    ap.add_argument("--dry-run", action="store_true")
    args = ap.parse_args()

    conn = get_connection(get_pg_dsn())
    registry = SeederRegistry(args.domain)
    results = registry.run_all(conn, only=args.only, dry_run=args.dry_run)
    print(f"\nSummary: {sum(1 for v in results.values() if v and v > 0)} succeeded, "
          f"{sum(1 for v in results.values() if v is None)} failed")

if __name__ == "__main__":
    main()
```

Usage: `python scripts/init_data.py --domain real_estate` or `--domain sports_ticketing --only event_catalog`.

**`scripts/scaffold_domain.py`** — generates skeleton for new industry:

```python
"""CLI: generate skeleton for new domain.

Creates: skills/<domain>/, seeders/<domain>/, data/seeds/<domain>/, manifest stubs.
"""
import argparse
import shutil
from pathlib import Path

TEMPLATES = Path(__file__).parent.parent / "templates" / "domain"

def scaffold(domain: str):
    targets = {
        f"skills/{domain}": ["__init__.py", "skills.yaml.tmpl"],
        f"seeders/{domain}": ["__init__.py", "manifest.yaml.tmpl"],
        f"data/seeds/{domain}": ["README.md.tmpl"],
    }
    for path, files in targets.items():
        Path(path).mkdir(parents=True, exist_ok=True)
        for f in files:
            src = TEMPLATES / f
            dst_name = f.replace(".tmpl", "")
            dst = Path(path) / dst_name
            if dst.exists():
                print(f"  SKIP exists: {dst}")
                continue
            content = src.read_text().replace("{{DOMAIN}}", domain)
            dst.write_text(content)
            print(f"  CREATE: {dst}")

if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--domain", required=True)
    args = ap.parse_args()
    scaffold(args.domain)
    print(f"\nScaffolded {args.domain}. Next:")
    print(f"  1. Edit skills/{args.domain}/skills.yaml — declare your skills")
    print(f"  2. Edit seeders/{args.domain}/manifest.yaml — declare your seeders")
    print(f"  3. Drop seed CSVs in data/seeds/{args.domain}/")
    print(f"  4. python scripts/init_data.py --domain {args.domain} --dry-run")
```

**`templates/domain/skills.yaml.tmpl`** (snippet):

```yaml
domain: {{DOMAIN}}
version: 0.1
config:
  fuzzy_match_threshold: 0.85
skills:
  # Add domain skills here. See skills/real_estate/skills.yaml for example.
  # spell_checker_{{DOMAIN}}:
  #   class: skills.{{DOMAIN}}.spell_checker.SpellChecker
  #   skill_doc: skills/{{DOMAIN}}/spell_checker/skill.md
  #   tools: [fuzzy_matcher]
  #   cost: low
```

**`templates/domain/manifest.yaml.tmpl`** (snippet):

```yaml
domain: {{DOMAIN}}
description: "Seeders for {{DOMAIN}} domain"
schema_migrations: []
seeders: []
  # - name: wikipedia_X
  #   class: seeders.{{DOMAIN}}.wikipedia_X.WikipediaXSeeder
  #   enabled: true
  #   license: "..."
```

- [ ] **Step 1: Schema migrations applied** (`scripts/init_db.py --domain real_estate` first)
- [ ] **Step 2: Tests for `Seeder` ABC + `SeederRegistry` (mocked seeders)**
- [ ] **Step 3: Tests for each real_estate seeder (mocked HTTP, real CSV fixture)**
- [ ] **Step 4: Implement base + registry + 3 real_estate seeders + manifest**
- [ ] **Step 5: Implement `scripts/init_data.py` + `scripts/scaffold_domain.py`**
- [ ] **Step 6: Smoke test:** `python scripts/scaffold_domain.py --domain test_industry` → verify dirs created → delete
- [ ] **Step 7: Update `CLAUDE.md`:**
  - Bootstrap new DB: `python scripts/init_db.py && python scripts/init_data.py --domain real_estate`
  - Add new industry: `python scripts/scaffold_domain.py --domain X` then edit manifests
- [ ] **Step 8: Commit:** `feat(seeders): domain-agnostic seeder framework + manifest + scaffold CLI`

---

### Task B4b: AI-assisted seed generation (optional but valuable)

**Files:**
- New: `scripts/generate_seed.py` (LLM-driven seed-file generator)

**Why:** User wants "ways to generate seeding files / processes based on industry or dataset." When adding new industry, LLM proposes seed CSV templates from industry description + sample records.

**Approach:**
1. CLI takes: `--domain X --description "..." --sample data/raw/sample_X.csv`
2. LLM (Sonnet via existing `llm_client.py`) reads sample + description, outputs:
   - Suggested seed-CSV columns + 50 example rows (e.g., common misspellings for that industry)
   - Suggested public data sources (URLs, APIs, datasets) with rationale
   - Skeleton seeder class for each source
3. Output written to `data/seeds/<domain>/proposed_*.csv` + `seeders/<domain>/proposed_*.py`
4. **NEVER auto-applies** — outputs proposals for human review

**Implementation sketch:**

```python
"""CLI: AI-assisted seed file generator for new domain.

Uses llm_client to propose seed CSVs + seeder code stubs from industry description.
Output is for human review — does NOT auto-commit or auto-run.
"""
import argparse, csv, json
from pathlib import Path
from cleaning.llm_client import build_client_for_tier

PROMPT = """You generate seed data for a domain-agnostic data-cleaning pipeline.

Domain: {domain}
Description: {description}
Sample rows: {sample}

Output JSON only:
{{
  "spell_corrections": [{{"wrong": "...", "right": "...", "confidence": 0.95}}, ...],
  "public_sources": [{{"name": "...", "url": "...", "rationale": "...", "license": "..."}}, ...],
  "suggested_skills": [{{"name": "...", "purpose": "..."}}, ...]
}}

Rules:
- Spell corrections: 30-80 entries, real-world common misspellings for this industry
- Public sources: prefer government, Wikipedia, OpenStreetMap, well-known datasets
- License: only suggest sources with permissive license
"""

def generate(domain: str, description: str, sample_path: str):
    sample = list(csv.DictReader(open(sample_path)))[:20]
    llm = build_client_for_tier("standard")
    resp = llm.messages_create(
        system="You produce JSON only.",
        messages=[{"role":"user","content":PROMPT.format(domain=domain, description=description, sample=json.dumps(sample))}],
        tools=[],
        max_tokens=4096,
    )
    text = next((b.text for b in resp.content if hasattr(b, "text")), "")
    data = json.loads(text)

    out_dir = Path(f"data/seeds/{domain}")
    out_dir.mkdir(parents=True, exist_ok=True)

    with open(out_dir / "proposed_spell_corrections.csv", "w") as f:
        w = csv.DictWriter(f, fieldnames=["wrong", "right", "confidence"])
        w.writeheader()
        w.writerows(data["spell_corrections"])

    (out_dir / "proposed_sources.md").write_text(
        "# Proposed Public Sources (REVIEW BEFORE USE)\n\n" +
        "\n".join(f"- **{s['name']}**: {s['url']} — {s['rationale']} (License: {s['license']})"
                  for s in data["public_sources"])
    )
    print(f"Generated proposals in {out_dir}/. Review before running seeders.")

if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--domain", required=True)
    ap.add_argument("--description", required=True)
    ap.add_argument("--sample", required=True, help="CSV with 10-20 sample rows from this industry")
    args = ap.parse_args()
    generate(args.domain, args.description, args.sample)
```

- [ ] **Step 1: Failing test with mocked LLM client returning canned JSON**
- [ ] **Step 2: Implement generator + JSON validation**
- [ ] **Step 3: Output written to `proposed_*` files (never `*.csv` directly — forces review)**
- [ ] **Step 4: README in `data/seeds/<domain>/` warns proposed-files need human review**
- [ ] **Step 5: Commit:** `feat(seeders): LLM-assisted seed-file generator for new domains`

---

### Task B5: Restore Nominatim geocoding skill

**Files:**
- New: `skills/real_estate/nominatim_geocoder/skill.md`
- New: `skills/real_estate/nominatim_geocoder/nominatim_geocoder.py`
- Modify: `skills/real_estate/skills.yaml`

**Why:** Address → coords + reverse-geocode round-trip catches address invented by user. Was working pre-v2.

- [ ] **Step 1: Write skill.md** (Purpose, When to Use, Input/Output schema, rate limit 1 req/sec, cache via PG)
- [ ] **Step 2: Failing test using mocked HTTP**
- [ ] **Step 3: Implement skill — `urllib` + PG cache table `nominatim_cache(query, response_json, fetched_at)` for repeat queries**
- [ ] **Step 4: Add to `skills.yaml` with `depends_on: [address_standardizer]` and `cost: high`**
- [ ] **Step 5: Test + commit**

---

## Stage C — WebSearch Enrichment Skill

Smart, sparing web search via Tavily (already configured). Triggers only when confidence low. Persists queries that worked + sources that worked, for reuse.

### Architecture

```
WebSearchEnricher
├── trigger: confidence < threshold AND specific gap (e.g., postal_unresolved)
├── query builder: gap_type → query template (parameterized by record fields)
├── source registry: prefer .gov.ca, openstreetmap.org, statcan.gc.ca, mpac.ca
├── cache: WebSearchCache (existing) + new query_pattern_memory table
└── output: enriched fields + decision log + confidence boost
```

### Task C1: Schema for query memory + per-domain query packs

**Files:**
- New: `db/migrations/004_query_pattern_memory.sql`
- New: `db/pg_query_memory.py`
- New: `data/seeds/real_estate/query_packs.yaml` (seed query templates per gap_type)
- New: `data/seeds/_common/query_packs.yaml` (cross-domain query templates)

**Why query packs:** Each domain has different search patterns. Real estate: `site:canadapost.ca {postal}`. Sports: `site:ticketmaster.com {event_name}`. Domain-specific seed YAML, then runtime memory layers domain-tagged learning on top.

```sql
CREATE TABLE query_pattern_memory (
    id SERIAL PRIMARY KEY,
    domain TEXT NOT NULL,             -- 'real_estate', 'sports_ticketing', '_common'
    gap_type TEXT NOT NULL,           -- 'postal_unresolved', 'event_unresolved'
    query_template TEXT NOT NULL,     -- 'site:canadapost.ca postal {postal_code}'
    success_count INT DEFAULT 0,
    failure_count INT DEFAULT 0,
    last_used_at TIMESTAMPTZ,
    sample_resolution JSONB,
    UNIQUE (domain, gap_type, query_template)
);

CREATE TABLE source_registry (
    domain_key TEXT NOT NULL,         -- domain context (NOT URL host) — e.g. 'real_estate'
    url_host TEXT NOT NULL,           -- 'canadapost.ca'
    trust_score REAL DEFAULT 0.5,
    success_count INT DEFAULT 0,
    failure_count INT DEFAULT 0,
    license_notes TEXT,
    PRIMARY KEY (domain_key, url_host)
);
```

**`data/seeds/real_estate/query_packs.yaml`:**

```yaml
domain: real_estate
gap_types:
  postal_unresolved:
    seed_queries:
      - "site:canadapost.ca postal code {postal_code}"
      - "{postal_code} canada postal code municipality"
      - "site:wikipedia.org {postal_code} FSA"
    parser: real_estate.postal_parser   # python module path
  municipality_ambiguous:
    seed_queries:
      - "{postal_code} municipality {province}"
      - "site:openstreetmap.org {address} {city}"
    parser: real_estate.municipality_parser
  unknown_country:
    seed_queries:
      - "{address} country code"
      - "{city} {state_province} country"
    parser: _common.country_parser
trusted_sources:
  - canadapost.ca
  - wikipedia.org
  - openstreetmap.org
  - statcan.gc.ca
  - mpac.ca
```

**`data/seeds/_common/query_packs.yaml`:**

```yaml
domain: _common
gap_types:
  unknown_phone_format:
    seed_queries:
      - "{phone} country code area"
    parser: _common.phone_parser
  unknown_email_domain:
    seed_queries:
      - "{email_domain} company organization"
    parser: _common.email_parser
trusted_sources:
  - wikipedia.org
  - opencorporates.com
```

- [ ] **Step 1: Migration + tests**
- [ ] **Step 2: `pg_query_memory.py` helpers — `top_queries_for(domain, gap_type, k=3)`, `record_query_outcome(domain, gap, query, success)`, `update_source_score(domain, host, success)`**
- [ ] **Step 3: Loader: `load_query_packs(conn, domain)` reads YAML, upserts into `query_pattern_memory` (idempotent, source='seed')**
- [ ] **Step 4: Add to seeder framework: `QueryPackSeeder` (domain-agnostic)**
- [ ] **Step 5: Commit**

---

### Task C2: WebSearchEnricher skill (domain-agnostic, in `skills/_common/`)

**Files:**
- New: `skills/_common/__init__.py`
- New: `skills/_common/web_search_enricher/skill.md`
- New: `skills/_common/web_search_enricher/web_search_enricher.py`
- New: `skills/_common/web_search_enricher/parsers/` (per-domain parser plugins)
- Modify: `skills/real_estate/skills.yaml` (reference shared skill via class path)
- Modify: `skills/registry.py` (load skills from `_common` AND domain folder)

**Why domain-agnostic:** Same Tavily call mechanics, different parsers. Skill core = router. Parsers per domain extract structured fields from search results.

**skill.md outline:**
- Purpose: "Resolve missing/ambiguous fields via public web search. Domain-agnostic core; per-domain parsers extract fields from results."
- When to Use: low conf + identifiable gap_type, gap has registered seed/learned queries, max query budget not exceeded
- Input: `record`, `domain` (auto-set by registry), `gap_type` (from triage hints)
- Output: `record` with filled fields + `_web_search_evidence` (list of {query, url, snippet, parsed}) + `_decisions`
- Constraints: max N queries per record, prefer cached, prefer trusted sources, never run if `_triage_route == "unsalvageable"` or `"done"`

**Implementation sketch:**

```python
from importlib import import_module
from cleaning.cache import WebSearchCache
from db.pg_query_memory import top_queries_for, record_query_outcome, update_source_score

class WebSearchEnricher(BaseSkill):
    def __init__(self, config=None):
        super().__init__(config)
        self.max_queries = self.config.get("max_queries", 3)
        self.confidence_trigger = self.config.get("trigger_below", 0.70)
        self.cache: WebSearchCache = self.config.get("web_cache")
        self.conn = self.config.get("pg_conn")
        # domain set by registry on load

    def run(self, record, tools=None):
        # Hard gate
        if record.get("_triage_route") in ("done", "unsalvageable"):
            return record
        if record.get("_triage_data_confidence", 1.0) >= self.confidence_trigger:
            return record

        gaps = self._identify_gaps(record)
        if not gaps:
            return record

        evidence = []
        decisions = []
        budget = self.max_queries

        for gap in gaps:
            if budget <= 0: break
            queries = top_queries_for(self.conn, self.domain, gap, k=2)
            if not queries:
                queries = top_queries_for(self.conn, "_common", gap, k=2)  # fallback
            parser = self._load_parser(self.domain, gap)

            for q_template in queries:
                if budget <= 0: break
                budget -= 1
                try:
                    query = q_template.format(**record)
                except KeyError:
                    continue  # missing field — try next template
                result = self.cache.get_or_search(query)  # hits Tavily if not cached
                parsed = parser.parse(result, record) if parser else None
                if parsed:
                    record.update(parsed["fields"])
                    evidence.append({
                        "query": query, "gap": gap,
                        "url": parsed.get("source_url"),
                        "snippet": parsed.get("snippet"),
                    })
                    record_query_outcome(self.conn, self.domain, gap, q_template, success=True)
                    if parsed.get("source_url"):
                        update_source_score(self.conn, self.domain, _host(parsed["source_url"]), success=True)
                    decisions.append(self.log_decision(
                        f"Resolved {gap} via web search",
                        f"Query: '{query}' → {parsed['fields']}",
                        confidence=parsed.get("confidence", 0.75),
                    ))
                    break
            else:
                record_query_outcome(self.conn, self.domain, gap, queries[0] if queries else "", success=False)
                decisions.append(self.log_decision(
                    f"Web search failed for {gap}",
                    f"Tried {len(queries)} queries, no parsable result",
                    confidence=0.0,
                ))

        record["_web_search_evidence"] = evidence
        if decisions:
            record.setdefault("_decisions", []).extend(decisions)
        return record

    def _identify_gaps(self, record) -> list[str]:
        """Inspect record for known gap_type signals. Domain-neutral signals here;
        domain-specific gaps come from triage-emitted `_gap_hints` field."""
        gaps = []
        if record.get("_unknown_fsa"): gaps.append("postal_unresolved")
        if record.get("_municipality_confidence", 1.0) < 0.70: gaps.append("municipality_ambiguous")
        if not record.get("country"): gaps.append("unknown_country")
        # Triage can emit explicit hints (domain-specific gap_types)
        gaps.extend(record.get("_gap_hints", []))
        return list(dict.fromkeys(gaps))  # dedupe, preserve order

    def _load_parser(self, domain, gap_type):
        """Load parser module dynamically. Falls back to _common parser."""
        for d in (domain, "_common"):
            try:
                mod = import_module(f"skills._common.web_search_enricher.parsers.{d}.{gap_type}")
                return mod
            except ModuleNotFoundError:
                continue
        return None
```

**Parser pattern** — `skills/_common/web_search_enricher/parsers/real_estate/postal_unresolved.py`:

```python
"""Parses Tavily output for postal_unresolved gap (real_estate domain)."""
import re

def parse(search_result: str, record: dict) -> dict | None:
    """Extract municipality + confidence from search snippets.

    Returns: {"fields": {...}, "source_url": "...", "snippet": "...", "confidence": 0.8}
             or None if cannot parse.
    """
    # Look for known municipality keywords near postal code
    m = re.search(r"(Toronto|Scarborough|North York|Etobicoke|Mississauga|Vaughan)", search_result, re.I)
    if not m:
        return None
    url_m = re.search(r"URL:\s*(\S+)", search_result)
    return {
        "fields": {"municipality": m.group(1).title()},
        "source_url": url_m.group(1) if url_m else None,
        "snippet": search_result[:200],
        "confidence": 0.75,
    }
```

- [ ] **Step 1: skill.md + failing tests (mock cache + canned Tavily result)**
- [ ] **Step 2: Implement core enricher — gap detection, parser load, query loop**
- [ ] **Step 3: Implement 3 real_estate parsers (postal_unresolved, municipality_ambiguous, unknown_country)**
- [ ] **Step 4: Implement 2 _common parsers (unknown_phone_format, unknown_email_domain)**
- [ ] **Step 5: Register skill in `skills/_common/skills.yaml` AND wire into `real_estate/skills.yaml` via `class: skills._common.web_search_enricher...`**
- [ ] **Step 6: Update registry to scan `_common/` first, then domain — `_common` skills available across domains**
- [ ] **Step 7: Pass + commit**

---

### Task C3: Trigger gating + budget tracking

**Files:**
- Modify: `cleaning/orchestrator_v2.py`
- Modify: `skills/_common/web_search_enricher/web_search_enricher.py` (per-batch budget)

**Why:** Web search is slow + costs money. Gate aggressively. Track per-batch query budget so 1000-record batch doesn't burn $50 in Tavily calls.

Default trigger logic:
- Run web search ONLY if `_triage_route == "needs_review"` AND `_identify_gaps()` returns non-empty
- Skip if `_triage_route == "done"` (high conf already)
- Skip if `_triage_route == "unsalvageable"` (no point)
- Per-batch budget: `max_total_queries` (default 100). Once spent, all further enrichment skipped (logged loud).

```python
class BatchBudget:
    def __init__(self, max_queries: int):
        self.remaining = max_queries
        self.spent = 0
    def take(self, n=1) -> bool:
        if self.remaining < n: return False
        self.remaining -= n; self.spent += n; return True
```

Pass `BatchBudget` into `OrchestrationTeam` at batch start.

- [ ] **Step 1: Test budget exhaustion → enrichment skipped**
- [ ] **Step 2: Test gate logic per route**
- [ ] **Step 3: Implement gate + budget**
- [ ] **Step 4: Batch summary: `"Web search budget: 23/100 used, 5 records enriched, 2 cache-only"`**
- [ ] **Step 5: Commit:** `feat(orchestrator): web-search gated on triage route + per-batch budget`

---

## Stage D — AI Planner Skill (domain-agnostic)

LLM reads available `skill.md` files + record + decides skill order. Replaces hardcoded `OrchestrationTeam` stages with dynamic plan per record. Works for any domain — real_estate, sports_ticketing, future industries.

### Task D1: Planner skill

**Files:**
- New: `skills/_common/skill_planner/skill.md`
- New: `skills/_common/skill_planner/skill_planner.py`
- New: `db/migrations/005_plan_cache.sql`

**skill.md outline:**
- Purpose: "Read available skill docs + record. Output ordered skill list with reasoning. Domain-neutral."
- When to Use: ambiguous records, low confidence after deterministic pass, multiple gap_types
- Input: `record`, registry (via tools), domain (auto)
- Output: `_planned_skills` (ordered names) + `_plan_reasoning` (why each)
- Cost: HIGH (LLM call) — gated by triage route + plan cache

**Implementation sketch:**

```python
import hashlib, json
from cleaning.llm_client import build_client_for_tier

class SkillPlanner(BaseSkill):
    PLANNER_SYSTEM = """You orchestrate a data-cleaning pipeline for the {domain} domain.
Given a record and skill menu, output JSON: {{"plan": ["skill1", "skill2"], "reasoning": "..."}}.

Rules:
- Cheap deterministic skills first (cost: low)
- Fuzzy/decision skills only on partial matches
- web_search_enricher only when conf < 0.70 AND identifiable gap
- Respect skill `depends_on` in menu
- Output only skill names from menu (no inventions)
"""

    def __init__(self, config=None):
        super().__init__(config)
        self.llm = config.get("llm_client") or build_client_for_tier(
            config.get("tier", "fast")
        )
        self.conn = config.get("pg_conn")
        self.cache_ttl = config.get("plan_cache_ttl_hours", 24)

    def run(self, record, tools=None):
        registry = tools.get("registry") if tools else None
        if not registry:
            return record  # no menu → no planning

        sig = self._record_signature(record, registry)
        cached = self._lookup_plan_cache(sig) if self.conn else None
        if cached:
            record["_planned_skills"] = cached["plan"]
            record["_plan_reasoning"] = cached["reasoning"]
            record["_plan_source"] = "cache"
            return record

        menu = self._build_menu(registry)  # [{name, doc, cost, latency, depends_on}, ...]
        prompt = self._build_prompt(record, menu)
        resp = self.llm.messages_create(
            system=self.PLANNER_SYSTEM.format(domain=self.domain or "general"),
            messages=[{"role": "user", "content": prompt}],
            tools=[],
            max_tokens=512,
        )
        text = next((b.text for b in resp.content if hasattr(b, "text")), "{}")
        plan = self._parse_and_validate(text, registry)

        record["_planned_skills"] = plan["plan"]
        record["_plan_reasoning"] = plan["reasoning"]
        record["_plan_source"] = "llm"

        if self.conn:
            self._save_plan_cache(sig, plan)
        return record

    def _record_signature(self, record, registry) -> str:
        """Hash of record shape (which fields present, conf bucket) + skills.yaml version.
        Same signature → same plan → reuse from cache."""
        shape = {
            "fields_present": sorted(k for k, v in record.items() if v and not k.startswith("_")),
            "conf_bucket": round(record.get("_triage_data_confidence", 1.0), 1),
            "route": record.get("_triage_route"),
            "gaps": sorted(record.get("_gap_hints", [])),
            "domain": self.domain,
            "skills_version": registry.config.get("version", "1.0"),
        }
        return hashlib.sha256(json.dumps(shape, sort_keys=True).encode()).hexdigest()

    def _build_menu(self, registry) -> list[dict]:
        """Per-skill: name, doc (skill.md), cost, latency, depends_on."""
        menu = []
        for name in registry.list_skills():
            meta = registry.get_metadata(name) or {}
            menu.append({
                "name": name,
                "doc": meta.get("skill_doc", "")[:1500],  # truncate to control prompt size
                "cost": meta.get("cost", "medium"),
                "latency_ms": meta.get("latency_estimate_ms", 500),
                "depends_on": meta.get("depends_on", []),
            })
        return menu

    def _parse_and_validate(self, text: str, registry) -> dict:
        """Parse LLM JSON. Reject hallucinated skill names. Validate dep order."""
        try:
            data = json.loads(text.strip())
        except json.JSONDecodeError:
            # Fallback: regex JSON extraction
            import re
            m = re.search(r"\{.*\}", text, re.DOTALL)
            data = json.loads(m.group(0)) if m else {"plan": [], "reasoning": "parse_failed"}

        valid_skills = set(registry.list_skills())
        plan = [s for s in data.get("plan", []) if s in valid_skills]  # drop hallucinations
        # Topological sort against deps (uses registry.validate_dependencies — see D3)
        plan = registry.topological_sort(plan)
        return {"plan": plan, "reasoning": data.get("reasoning", "")}
```

```sql
-- db/migrations/005_plan_cache.sql
CREATE TABLE plan_cache (
    signature TEXT PRIMARY KEY,
    domain TEXT NOT NULL,
    plan JSONB NOT NULL,
    reasoning TEXT,
    created_at TIMESTAMPTZ DEFAULT NOW(),
    expires_at TIMESTAMPTZ NOT NULL
);
CREATE INDEX idx_plan_cache_expires ON plan_cache(expires_at);
```

- [ ] **Step 1: skill.md** (Purpose, When to Use, Input/Output, Cost: HIGH)
- [ ] **Step 2: Tests with mocked LLM client (canned plan JSON, hallucinated names rejected, cache hit/miss)**
- [ ] **Step 3: Migration + plan_cache helpers**
- [ ] **Step 4: Implement planner with `_build_menu`, `_record_signature`, `_parse_and_validate`**
- [ ] **Step 5: Test — same record signature twice → second call hits cache (no LLM call)**
- [ ] **Step 6: Commit:** `feat(skill_planner): LLM-driven skill orchestration with plan cache`

---

### Task D2: Orchestrator planner integration (multi-phase pipeline)

**Files:**
- Rewrite: `cleaning/orchestrator_v2.py` (replace hardcoded `OrchestrationTeam` stages)

**Pipeline phases (any domain):**

```python
def process_record(self, record, batch_budget):
    # Phase 1: ALWAYS run deterministic tier (cost=low skills, no deps unmet)
    deterministic = self.registry.skills_by_cost("low")
    record = self._run_skills(record, deterministic)

    # Phase 2: Triage to decide further work
    record = self.registry.get("data_quality_triage").run(record)
    if record["_triage_route"] == "done":
        return record
    if record["_triage_route"] == "unsalvageable":
        return record  # don't waste planning on garbage

    # Phase 3: AI Planner picks medium/high-cost skills for ambiguous case
    record = self.planner.run(record, tools={"registry": self.registry})
    planned = record.get("_planned_skills", [])

    # Phase 4: Run planned skills with budget enforcement
    for skill_name in planned:
        skill = self.registry.get(skill_name)
        meta = self.registry.get_metadata(skill_name)
        if meta.get("cost") == "high" and not batch_budget.take(1):
            record.setdefault("_decisions", []).append(self.log_decision(
                f"Skipped {skill_name} — batch budget exhausted",
                f"Spent {batch_budget.spent}/{batch_budget.spent + batch_budget.remaining}",
                confidence=0.0,
            ))
            break
        record = skill.run(record)

    # Phase 5: Re-triage with new evidence
    return self.registry.get("data_quality_triage").run(record)
```

**Domain-agnostic:** No skill names hardcoded except `data_quality_triage` (a required skill in every domain manifest). Stage selection by `cost` metadata, order by planner.

- [ ] **Step 1: Tests with full mocked LLM + multiple domains (real_estate + a fake test domain)**
- [ ] **Step 2: Replace `OrchestrationTeam` with phased loop**
- [ ] **Step 3: Pass `BatchBudget` from `run_cleaning_workflow_v2`**
- [ ] **Step 4: Batch-level logging:**

```
Batch 100 records (real_estate):
  Phase 1 (deterministic):  100 records processed in 0.3s
  Phase 2 (triage):         60 done, 35 needs_review, 5 unsalvageable
  Phase 3 (planner):        35 records planned (cache: 22 hits, 13 LLM calls)
  Phase 4 (enrichment):     12 web_search hits, 8 cache, 15 skipped (budget)
  Phase 5 (re-triage):      28 → done, 7 still needs_review
Final: 88 done, 7 review, 5 unsalvageable
Cost: 13 plan LLM calls, 8 Tavily calls (budget 8/100 used)
```

- [ ] **Step 5: Commit:** `feat(orchestrator): multi-phase pipeline with AI Planner + budget`

---

### Task D3: Skill dependency validation + topological sort

**Files:**
- Modify: `skills/registry.py` (add `validate_dependencies()`, `topological_sort()`, `skills_by_cost()`)

**Why:** YAML declares `depends_on` but never enforced. Planner output may violate order. Hardcoded order in orchestrator goes away — order computed from dependencies.

```python
def validate_dependencies(self):
    """Detect circular deps + missing skills. Raises on bad config."""
    for name in self.skills:
        deps = self.metadata[name].get("depends_on", [])
        for dep in deps:
            if dep not in self.skills:
                raise ValueError(f"Skill {name} depends on unknown skill: {dep}")
    # Detect cycles via DFS coloring
    self._detect_cycles()

def topological_sort(self, skill_names: list[str]) -> list[str]:
    """Return skill_names in dependency order. Drop unknowns."""
    valid = [s for s in skill_names if s in self.skills]
    visited, order = set(), []
    def visit(n):
        if n in visited: return
        visited.add(n)
        for dep in self.metadata[n].get("depends_on", []):
            if dep in valid:
                visit(dep)
        order.append(n)
    for s in valid:
        visit(s)
    return order

def skills_by_cost(self, cost: str) -> list[str]:
    """All skills with given cost level, in dependency order."""
    matches = [n for n, m in self.metadata.items() if m.get("cost") == cost]
    return self.topological_sort(matches)
```

- [ ] **Step 1: Tests — circular dep → ValueError; missing dep → ValueError; toposort correct**
- [ ] **Step 2: Implement validation on `load_domain()`**
- [ ] **Step 3: Planner uses `topological_sort` to fix LLM ordering mistakes**
- [ ] **Step 4: Orchestrator uses `skills_by_cost("low")` for deterministic phase**
- [ ] **Step 5: Commit:** `feat(registry): dep validation + toposort + cost-tier filters`

---

## Stage E — Validation: Add Second Domain (Proof of Generality)

Goal: prove framework is domain-agnostic by scaffolding sports_ticketing with minimal effort. NOT a full implementation — just enough to demonstrate the pattern works end-to-end.

### Task E1: Scaffold sports_ticketing domain

**Files:**
- Created via `scripts/scaffold_domain.py --domain sports_ticketing`
- Then edit: `skills/sports_ticketing/skills.yaml`, `seeders/sports_ticketing/manifest.yaml`

- [ ] **Step 1: Run scaffold CLI**
- [ ] **Step 2: Add 2 skills:**
  - `event_normalizer` — normalize event names ("Leafs vs Habs" ≈ "Toronto Maple Leafs vs Montreal Canadiens")
  - `ticket_product_categorizer` — categorize: full_season / half_season / individual / voucher
- [ ] **Step 3: 1 seeder:** `wikipedia_teams` — scrapes NHL/NBA team list → `team_name_aliases` table
- [ ] **Step 4: 1 query pack:** `data/seeds/sports_ticketing/query_packs.yaml` (gap_types: `unknown_team`, `unknown_venue`)
- [ ] **Step 5: 5 sample records, end-to-end test**
- [ ] **Step 6: Commit:** `feat(sports_ticketing): scaffold + 2 skills + 1 seeder (proves generality)`

### Task E2: Cross-domain regression test

**Files:**
- New: `tests/test_multi_domain.py`

**Why:** Confirm changing `--domain` is the only switch needed. Same pipeline code runs both.

```python
def test_real_estate_pipeline_runs():
    report = run_cleaning_workflow_v2(records=REAL_ESTATE_SAMPLES, domain="real_estate")
    assert report.cleaned_count > 0

def test_sports_ticketing_pipeline_runs():
    report = run_cleaning_workflow_v2(records=SPORTS_SAMPLES, domain="sports_ticketing")
    assert report.cleaned_count > 0

def test_planner_uses_correct_domain_skills():
    # Plan for real_estate must NOT include sports skills, vice versa
    ...
```

- [ ] **Step 1: Tests fail without code change**
- [ ] **Step 2: Modify `run_cleaning_workflow_v2(records, domain="real_estate")` to take domain param**
- [ ] **Step 3: Pass through to `SkillRegistry.load(domain)`**
- [ ] **Step 4: Tests pass**
- [ ] **Step 5: Commit:** `test: multi-domain regression — real_estate + sports_ticketing same pipeline`

---

## Final Steps

After all stages:

- [ ] **Final review:** Dispatch superpowers:code-reviewer on full implementation
- [ ] **End-to-end test:** 100 sample records per domain, verify batch logging, cache hit rates, budget tracking
- [ ] **Update `CLAUDE.md`:**
  - How to run pipeline: `python -m cleaning --domain real_estate`
  - Env vars: `TAVILY_API_KEY`, `OPENROUTER_API_KEY`, `POSTGRES_DSN`, `ANTHROPIC_API_KEY`
  - Bootstrap: `python scripts/init_db.py && python scripts/init_data.py --domain real_estate`
  - New industry: `python scripts/scaffold_domain.py --domain X` then edit manifests
  - LLM-assisted: `python scripts/generate_seed.py --domain X --description "..." --sample data/raw/X.csv`
- [ ] **Update `README.md`** with architecture diagram + adding new industry section
- [ ] **PR:** title `feat: skills framework P0/P1 + AI planner + web search + multi-domain`

---

## Risks + Mitigations

| Risk | Mitigation |
|------|------------|
| LLM planner hallucinates skill names | Validate against `registry.list_skills()`; toposort drops unknowns |
| Web search costs blow up | `BatchBudget` per run; per-record `max_queries` cap; gate on triage route; cache aggressively |
| FSA loader empty on first run | Bootstrap docs prominent in CLAUDE.md; orchestrator warns loud on empty cache |
| Plan cache poisoning | TTL 24h; signature includes `skills.yaml` version (auto-invalidates on skill change) |
| Single-letter directional regression | Locked with explicit "Doe N Main" test (Task A3) |
| Hardcoded data sneaks back in | Lock tests assert FSA dict + misspelling dict NOT in source files |
| New domain fails silently | Manifest schema validation; `init_data.py` rejects missing manifest |
| LLM seed-gen produces bad data | Output to `proposed_*` files only; never auto-applied; license check required |
| Per-domain skills clash with `_common` | Registry namespace check on load; `_common.skill_X` distinct from `real_estate.skill_X` |

---

## Out of Scope (future)

- Multi-domain mixing (real-estate + sports same record — needs domain detection skill)
- Online/automatic source trust score updates (currently periodic batch only)
- Parallelization (one record at a time still — switch to async/batch when proven)
- LLM-tier auto-selection (planner uses fixed tier — could pick haiku vs sonnet per record complexity)
- Active-learning loop (humans correct triage, model retrains thresholds)
- Embedding-based skill recommendation (currently LLM reads markdown — could pre-embed skill.md for faster planning)
