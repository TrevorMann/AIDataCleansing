# Field Cleaner Skill Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build a single `FieldCleanerSkill` in `skills/_common/field_cleaner/` that validates and normalises gender, city, postal_code, and country fields for any domain — deterministic first, LLM only for residual ambiguity, self-learning via DB.

**Architecture:** `FieldTypeResolver` maps record field names to field types once at startup (priority: config override → sensitive flag → column_metadata annotation → convention patterns). `FieldCleanerSkill` runs a deterministic pass per field (base rules YAML + enabled learned corrections from DB), then makes a single batched LLM call for anything unresolved. High-confidence LLM corrections (≥ 0.90) are upserted to `learned_field_corrections` so they fire deterministically on future records. Sensitive fields are skipped and noted in audit without touching or logging their values.

**Tech Stack:** Python 3.12, PyYAML, psycopg2, existing `BaseSkill` / `SkillRegistry` / `OrchestrationTeam` patterns, existing `llm_client.messages_create()` pattern from `skill_planner.py`, pytest with `unittest.mock`.

---

## File Map

| Action | Path | Responsibility |
|--------|------|----------------|
| Create | `db/migrations/007_learned_field_corrections.sql` | New table + `column_metadata.field_type` column |
| Create | `skills/_common/field_cleaner/__init__.py` | Package marker |
| Create | `skills/_common/field_cleaner/resolver.py` | `FieldTypeResolver` — field name → type mapping |
| Create | `skills/_common/field_cleaner/field_cleaner.py` | `FieldCleanerSkill` — deterministic + LLM + learning |
| Create | `skills/_common/field_cleaner/skill.md` | LLM planner documentation |
| Create | `skills/_common/field_cleaner/rules/gender.yaml` | Gender normalization rules + guardrails |
| Create | `skills/_common/field_cleaner/rules/city.yaml` | City normalization rules + guardrails |
| Create | `skills/_common/field_cleaner/rules/postal_code.yaml` | Postal code validation rules + guardrails |
| Create | `skills/_common/field_cleaner/rules/country.yaml` | Country ISO normalization rules + guardrails |
| Create | `tests/skills/__init__.py` | Test package marker |
| Create | `tests/skills/test_field_cleaner_resolver.py` | Unit tests for FieldTypeResolver |
| Create | `tests/skills/test_field_cleaner.py` | Unit tests for FieldCleanerSkill |
| Modify | `skills/real_estate/skills.yaml` | Add field_cleaner, remove geographic_validator |
| Modify | `skills/sports_ticketing/skills.yaml` | Add field_cleaner, remove event_normalizer |
| Delete | `skills/real_estate/geographic_validator/` | Superseded by field_cleaner rules |
| Delete | `skills/real_estate/data_quality_triage/` | Superseded by `_common` version |
| Delete | `skills/sports_ticketing/event_normalizer/` | Team aliases move to learned_field_corrections |

---

## Task 1: DB Migration

**Files:**
- Create: `db/migrations/007_learned_field_corrections.sql`

- [ ] **Step 1: Write the migration**

```sql
-- db/migrations/007_learned_field_corrections.sql

-- Add field_type column to column_metadata so the resolver can use annotation layer
ALTER TABLE column_metadata
  ADD COLUMN IF NOT EXISTS field_type TEXT DEFAULT NULL;

-- Self-learning corrections table
CREATE TABLE IF NOT EXISTS learned_field_corrections (
    id              SERIAL PRIMARY KEY,
    field_type      TEXT NOT NULL,
    domain          TEXT NOT NULL,
    raw_value       TEXT NOT NULL,
    corrected_value TEXT NOT NULL,
    confidence      FLOAT NOT NULL,
    times_seen      INT DEFAULT 1,
    enabled         BOOLEAN DEFAULT TRUE,
    promoted        BOOLEAN DEFAULT FALSE,
    created_at      TIMESTAMPTZ DEFAULT NOW(),
    last_seen_at    TIMESTAMPTZ DEFAULT NOW(),
    UNIQUE (field_type, domain, raw_value)
);

CREATE INDEX IF NOT EXISTS idx_lfc_domain_type
    ON learned_field_corrections (domain, field_type)
    WHERE enabled = TRUE;
```

- [ ] **Step 2: Apply migration (PostgreSQL)**

```bash
psql $POSTGRES_DSN -f db/migrations/007_learned_field_corrections.sql
```

Expected: `ALTER TABLE`, `CREATE TABLE`, `CREATE INDEX` — no errors.

- [ ] **Step 3: Verify idempotency**

Run the same command again. Expected: no errors (all statements are `IF NOT EXISTS` / `IF NOT EXISTS`).

- [ ] **Step 4: Commit**

```bash
git add db/migrations/007_learned_field_corrections.sql
git commit -m "feat: migration 007 — learned_field_corrections table + column_metadata.field_type"
```

---

## Task 2: Rules Files

**Files:**
- Create: `skills/_common/field_cleaner/rules/gender.yaml`
- Create: `skills/_common/field_cleaner/rules/city.yaml`
- Create: `skills/_common/field_cleaner/rules/postal_code.yaml`
- Create: `skills/_common/field_cleaner/rules/country.yaml`

- [ ] **Step 1: Create gender.yaml**

```yaml
# skills/_common/field_cleaner/rules/gender.yaml
field_type: gender
description: "Normalize gender values to a canonical form"

canonical_values:
  - Male
  - Female
  - Non-binary
  - Unknown

normalization_map:
  m: Male
  f: Female
  male: Male
  female: Female
  man: Male
  woman: Female
  boy: Male
  girl: Female
  nb: Non-binary
  non binary: Non-binary
  nonbinary: Non-binary
  non-binary: Non-binary
  enby: Non-binary
  unknown: Unknown
  unspecified: Unknown
  "n/a": Unknown
  prefer not to say: Unknown

guardrails:
  - "Single letters other than M and F are not valid — do not use them as output"
  - "If the value cannot be resolved with high confidence, output Unknown"
  - "Never infer gender from a name or any other indirect signal"
  - "Output must be one of: Male, Female, Non-binary, Unknown"

reject_patterns:
  - "^[a-zA-Z]$"
```

- [ ] **Step 2: Create city.yaml**

```yaml
# skills/_common/field_cleaner/rules/city.yaml
field_type: city
description: "Normalize city names to proper title case; flag obviously invalid values"

guardrails:
  - "Output title case (e.g. New York, Los Angeles, Saint John)"
  - "Do not abbreviate city names"
  - "If value is purely numeric or a single character, output null"
  - "Do not invent or guess a city — if uncertain, return the value unchanged"
  - "Expand common abbreviations: St → Saint (when a city name prefix), Mt → Mount, Ft → Fort"

reject_patterns:
  - "^\\d+$"
  - "^.$"
```

- [ ] **Step 3: Create postal_code.yaml**

```yaml
# skills/_common/field_cleaner/rules/postal_code.yaml
field_type: postal_code
description: "Validate and normalize postal/zip codes; format is country-dependent"

guardrails:
  - "Use the country field in the same record to determine the expected format"
  - "Canadian format: A1A 1A1 — letter digit letter space digit letter digit (e.g. M5V 2T6)"
  - "US format: 12345 or 12345-6789"
  - "UK format: variable length, letter(s) digit(s) space digit letter letter (e.g. SW1A 1AA)"
  - "Do not invent or guess a postal code — output null if the value is unresolvable"
  - "Uppercase all letters in the corrected output"
  - "Add the standard space separator where the format requires it"

validation_patterns:
  CA: "^[A-Z]\\d[A-Z]\\s?\\d[A-Z]\\d$"
  US: "^\\d{5}(-\\d{4})?$"
  GB: "^[A-Z]{1,2}\\d[A-Z\\d]?\\s?\\d[A-Z]{2}$"

reject_if_empty: true

reject_patterns:
  - "^0+$"
  - "^1234$"
  - "^00000$"
```

- [ ] **Step 4: Create country.yaml**

```yaml
# skills/_common/field_cleaner/rules/country.yaml
field_type: country
description: "Normalize country values to ISO 3166-1 alpha-2 two-letter codes"

validation_pattern: "^[A-Z]{2}$"   # already a valid ISO alpha-2 code — checked before LLM

normalization_map:
  canada: CA
  canadian: CA
  united states: US
  united states of america: US
  usa: US
  us: US
  america: US
  american: US
  united kingdom: GB
  uk: GB
  great britain: GB
  england: GB
  scotland: GB
  wales: GB
  northern ireland: GB
  australia: AU
  australian: AU
  france: FR
  french: FR
  germany: DE
  german: DE
  mexico: MX
  mexican: MX
  netherlands: NL
  holland: NL
  dutch: NL
  japan: JP
  japanese: JP
  china: CN
  chinese: CN
  india: IN
  indian: IN
  brazil: BR
  brazilian: BR
  spain: ES
  spanish: ES
  italy: IT
  italian: IT
  portugal: PT
  portuguese: PT
  new zealand: NZ
  south africa: ZA
  ireland: IE
  singapore: SG
  sweden: SE
  norway: NO
  denmark: DK
  finland: FI
  switzerland: CH

guardrails:
  - "Output ISO 3166-1 alpha-2 two-letter code only (e.g. CA, US, GB, AU)"
  - "Never output the full country name — always use the two-letter code"
  - "If value is already a valid two-letter ISO code, return it uppercased unchanged"
  - "If uncertain, output null — do not guess"
```

- [ ] **Step 5: Commit**

```bash
git add skills/_common/field_cleaner/rules/
git commit -m "feat: field cleaner rules files — gender, city, postal_code, country"
```

---

## Task 3: FieldTypeResolver

**Files:**
- Create: `skills/_common/field_cleaner/__init__.py`
- Create: `skills/_common/field_cleaner/resolver.py`
- Create: `tests/skills/__init__.py`
- Create: `tests/skills/test_field_cleaner_resolver.py`

- [ ] **Step 1: Write the failing tests**

```python
# tests/skills/test_field_cleaner_resolver.py
import pytest
from unittest.mock import MagicMock
from skills._common.field_cleaner.resolver import FieldTypeResolver


def _resolver(overrides=None, sensitive=None, conn=None, domain="test"):
    config = {
        "field_overrides": overrides or {},
        "sensitive_fields": sensitive or [],
    }
    return FieldTypeResolver(config, pg_conn=conn, domain=domain)


def test_config_override_wins():
    r = _resolver(overrides={"zip": "postal_code"})
    result = r.resolve(["zip"])
    assert result["zip"] == "postal_code"


def test_config_override_beats_convention():
    # "gender" would be detected as gender by convention,
    # but override remaps it to something else
    r = _resolver(overrides={"gender": "custom_type"})
    result = r.resolve(["gender"])
    assert result["gender"] == "custom_type"


def test_sensitive_field_marked_correctly():
    r = _resolver(sensitive=["ssn", "sin"])
    result = r.resolve(["ssn", "city", "sin"])
    assert result["ssn"] == "sensitive"
    assert result["sin"] == "sensitive"
    assert result.get("city") == "city"


def test_sensitive_beats_override():
    # Field listed as both sensitive and overridden — sensitive wins
    r = _resolver(overrides={"ssn": "postal_code"}, sensitive=["ssn"])
    result = r.resolve(["ssn"])
    assert result["ssn"] == "sensitive"


def test_convention_gender():
    r = _resolver()
    result = r.resolve(["gender", "sex"])
    assert result["gender"] == "gender"
    assert result["sex"] == "gender"


def test_convention_city():
    r = _resolver()
    result = r.resolve(["city", "town", "suburb"])
    assert result["city"] == "city"
    assert result["town"] == "city"
    assert result["suburb"] == "city"


def test_convention_postal_code():
    r = _resolver()
    result = r.resolve(["postal_code", "zip", "zip_code", "postcode", "zipcode"])
    for field in ["postal_code", "zip", "zip_code", "postcode", "zipcode"]:
        assert result[field] == "postal_code", f"{field} should resolve to postal_code"


def test_convention_country():
    r = _resolver()
    result = r.resolve(["country", "nation", "country_code"])
    assert result["country"] == "country"
    assert result["nation"] == "country"
    assert result["country_code"] == "country"


def test_unknown_field_not_in_result():
    r = _resolver()
    result = r.resolve(["some_random_field", "internal_id"])
    assert "some_random_field" not in result
    assert "internal_id" not in result


def test_annotation_fallback(monkeypatch):
    conn = MagicMock()
    cursor = MagicMock()
    cursor.__enter__ = lambda s: s
    cursor.__exit__ = MagicMock(return_value=False)
    cursor.fetchall.return_value = [("cust_gender", "gender"), ("post_cd", "postal_code")]
    conn.cursor.return_value = cursor

    monkeypatch.setattr(
        "skills._common.field_cleaner.resolver.get_framework_schema",
        lambda: "public"
    )
    r = FieldTypeResolver({"field_overrides": {}, "sensitive_fields": []}, pg_conn=conn, domain="test")
    result = r.resolve(["cust_gender", "post_cd", "city"])
    assert result["cust_gender"] == "gender"
    assert result["post_cd"] == "postal_code"
    assert result["city"] == "city"  # falls through to convention


def test_annotation_beats_convention(monkeypatch):
    conn = MagicMock()
    cursor = MagicMock()
    cursor.__enter__ = lambda s: s
    cursor.__exit__ = MagicMock(return_value=False)
    # "city" annotated as postal_code (unusual but should win over convention)
    cursor.fetchall.return_value = [("city", "postal_code")]
    conn.cursor.return_value = cursor

    monkeypatch.setattr(
        "skills._common.field_cleaner.resolver.get_framework_schema",
        lambda: "public"
    )
    r = FieldTypeResolver({"field_overrides": {}, "sensitive_fields": []}, pg_conn=conn, domain="test")
    result = r.resolve(["city"])
    assert result["city"] == "postal_code"


def test_resolve_empty_list():
    r = _resolver()
    assert r.resolve([]) == {}
```

- [ ] **Step 2: Run tests to confirm they fail**

```bash
cd /mnt/f/AI_learning_project && python -m pytest tests/skills/test_field_cleaner_resolver.py -v 2>&1 | head -20
```

Expected: `ModuleNotFoundError` or `ImportError` — `resolver` doesn't exist yet.

- [ ] **Step 3: Create package init**

```python
# skills/_common/field_cleaner/__init__.py
```

```python
# tests/skills/__init__.py
```

- [ ] **Step 4: Implement FieldTypeResolver**

```python
# skills/_common/field_cleaner/resolver.py
import re
from typing import Dict, List, Optional

from db.schema_config import get_framework_schema


_CONVENTIONS: List[tuple] = [
    ("gender",      ["gender", "sex"]),
    ("city",        ["city", "town", "suburb"]),
    ("postal_code", ["postal_code", "postal", "zip", "postcode", "zipcode"]),
    ("country",     ["country", "nation", "country_code"]),
]


class FieldTypeResolver:
    """Maps record field names to field types once at startup.

    Priority (highest to lowest):
      1. config field_overrides  — explicit, always wins
      2. sensitive_fields        — marked sensitive regardless of type
      3. column_metadata.field_type — DB annotation (when available)
      4. convention patterns     — name-based heuristic, last resort
    """

    def __init__(self, config: dict, pg_conn=None, domain: str = ""):
        self._overrides: Dict[str, str] = config.get("field_overrides", {})
        self._sensitive: set = set(config.get("sensitive_fields", []))
        self._pg_conn = pg_conn
        self._domain = domain
        self._annotations: Dict[str, str] = self._load_annotations()

    def resolve(self, field_names: List[str]) -> Dict[str, str]:
        """Return {field_name → field_type | 'sensitive'} for known fields only."""
        result: Dict[str, str] = {}
        for field in field_names:
            resolved = self._resolve_one(field)
            if resolved is not None:
                result[field] = resolved
        return result

    def _resolve_one(self, field: str) -> Optional[str]:
        # 1. Sensitive always wins — even if also in overrides
        if field in self._sensitive:
            return "sensitive"
        # 2. Config override
        if field in self._overrides:
            return self._overrides[field]
        # 3. Annotation
        if field in self._annotations:
            return self._annotations[field]
        # 4. Convention
        return self._from_convention(field)

    def _load_annotations(self) -> Dict[str, str]:
        if not self._pg_conn or not self._domain:
            return {}
        try:
            schema = get_framework_schema()
            with self._pg_conn.cursor() as cur:
                cur.execute(
                    f"SELECT column_name, field_type FROM {schema}.column_metadata "
                    f"WHERE domain = %s AND field_type IS NOT NULL",
                    (self._domain,),
                )
                return {row[0]: row[1] for row in cur.fetchall()}
        except Exception:
            return {}

    def _from_convention(self, field_name: str) -> Optional[str]:
        norm = field_name.lower().replace("-", "_")
        for field_type, patterns in _CONVENTIONS:
            for pattern in patterns:
                if norm == pattern or norm.startswith(pattern + "_") or norm.endswith("_" + pattern):
                    return field_type
        return None
```

- [ ] **Step 5: Run tests to confirm they pass**

```bash
python -m pytest tests/skills/test_field_cleaner_resolver.py -v
```

Expected: all tests pass.

- [ ] **Step 6: Commit**

```bash
git add skills/_common/field_cleaner/__init__.py skills/_common/field_cleaner/resolver.py \
        tests/skills/__init__.py tests/skills/test_field_cleaner_resolver.py
git commit -m "feat: FieldTypeResolver — field name to type mapping with 4-layer priority"
```

---

## Task 4: FieldCleanerSkill — Deterministic Path

**Files:**
- Create: `skills/_common/field_cleaner/field_cleaner.py`
- Create: `tests/skills/test_field_cleaner.py` (deterministic section)

- [ ] **Step 1: Write the failing tests**

```python
# tests/skills/test_field_cleaner.py
import pytest
from unittest.mock import MagicMock, patch
from skills._common.field_cleaner.field_cleaner import FieldCleanerSkill


def _skill(overrides=None, sensitive=None, conn=None, domain="test", learning_threshold=0.90):
    config = {
        "field_overrides": overrides or {},
        "sensitive_fields": sensitive or [],
        "pg_conn": conn,
        "domain": domain,
        "learning_confidence_threshold": learning_threshold,
        "llm_client": None,
    }
    return FieldCleanerSkill(config)


# --- Deterministic: normalization_map ---

def test_gender_normalization_map_hit():
    skill = _skill()
    record = {"gender": "M"}
    result = skill.run(record, {})
    assert result["gender"] == "Male"
    audit = skill.get_audit()
    assert any("Male" in e["decision"] for e in audit)
    assert any(e["confidence"] == 1.0 for e in audit)


def test_gender_lowercase_normalization():
    skill = _skill()
    record = {"gender": "female"}
    result = skill.run(record, {})
    assert result["gender"] == "Female"


def test_country_normalization_map_hit():
    skill = _skill()
    record = {"country": "Canada"}
    result = skill.run(record, {})
    assert result["country"] == "CA"


def test_canonical_value_passes_through():
    skill = _skill()
    record = {"gender": "Male"}
    result = skill.run(record, {})
    assert result["gender"] == "Male"
    audit = skill.get_audit()
    assert any("canonical" in e["reason"] or "valid" in e["reason"].lower() for e in audit)


def test_unknown_field_untouched():
    skill = _skill()
    record = {"invoice_number": "INV-001", "gender": "Male"}
    result = skill.run(record, {})
    assert result["invoice_number"] == "INV-001"


def test_sensitive_field_skipped_and_value_unchanged():
    skill = _skill(sensitive=["ssn"])
    record = {"ssn": "123-45-6789", "gender": "Male"}
    result = skill.run(record, {})
    assert result["ssn"] == "123-45-6789"  # value never touched
    audit = skill.get_audit()
    sensitive_entries = [e for e in audit if "ssn" in e["decision"]]
    assert len(sensitive_entries) == 1
    assert "sensitive" in sensitive_entries[0]["reason"]
    # Value must not appear in any audit entry
    for entry in audit:
        assert "123-45-6789" not in str(entry)


def test_sensitive_field_value_not_in_audit():
    skill = _skill(sensitive=["credit_card"])
    record = {"credit_card": "4111111111111111"}
    skill.run(record, {})
    audit = skill.get_audit()
    for entry in audit:
        assert "4111111111111111" not in str(entry)


def test_reject_pattern_escalates_to_needs_llm(monkeypatch):
    """Single letter gender should not be resolved deterministically."""
    skill = _skill()
    # Patch _batch_llm to capture what gets escalated
    captured = []
    monkeypatch.setattr(skill, "_process_llm_batch", lambda record, dirty: captured.extend(dirty))
    record = {"gender": "T"}
    skill.run(record, {})
    assert any(item["field"] == "gender" for item in captured)


def test_learned_correction_applied(monkeypatch):
    skill = _skill(domain="test")
    # Inject a learned correction directly into the in-memory cache
    skill._learned[("gender", "t")] = "Male"
    record = {"gender": "T"}
    result = skill.run(record, {})
    assert result["gender"] == "Male"
    audit = skill.get_audit()
    assert any("learned" in e["reason"].lower() for e in audit)


def test_audit_cleared_between_runs():
    skill = _skill()
    skill.run({"gender": "Male"}, {})
    audit1 = skill.get_audit()
    skill.run({"gender": "Female"}, {})
    audit2 = skill.get_audit()
    assert not any("Male" in e["decision"] for e in audit2)


def test_non_string_field_skipped():
    skill = _skill()
    record = {"gender": None, "country": 42}
    result = skill.run(record, {})
    assert result["gender"] is None
    assert result["country"] == 42


# --- City deterministic path ---

def test_city_all_lower_title_cased():
    skill = _skill()
    result = skill.run({"city": "new york"}, {})
    assert result["city"] == "New York"
    audit = skill.get_audit()
    assert any("title case" in e["reason"] for e in audit)


def test_city_all_upper_title_cased():
    skill = _skill()
    result = skill.run({"city": "TORONTO"}, {})
    assert result["city"] == "Toronto"


def test_city_already_title_case_unchanged():
    skill = _skill()
    result = skill.run({"city": "San Francisco"}, {})
    assert result["city"] == "San Francisco"
    audit = skill.get_audit()
    assert any("already title case" in e["reason"] for e in audit)


def test_city_numeric_escalates_to_llm(monkeypatch):
    skill = _skill()
    captured = []
    monkeypatch.setattr(skill, "_process_llm_batch", lambda record, dirty: captured.extend(dirty))
    skill.run({"city": "12345"}, {})
    assert any(item["field"] == "city" for item in captured)


# --- Country validation_pattern ---

def test_country_already_iso_code_returned_uppercase():
    skill = _skill()
    result = skill.run({"country": "us"}, {})
    # "us" matches normalization_map → "US" via normalization_map, not validation_pattern
    # Test with a code not in normalization_map but valid ISO format
    result2 = skill.run({"country": "NZ"}, {})
    assert result2["country"] == "NZ"
    audit = skill.get_audit()
    assert any("valid ISO code" in e["reason"] or "normalization_map" in e["reason"] for e in audit)


def test_country_validation_pattern_matches_unlisted_iso():
    """A valid-format ISO code not in normalization_map passes deterministically."""
    skill = _skill()
    result = skill.run({"country": "sg"}, {})
    # "sg" is in normalization_map (singapore: SG)
    assert result["country"] == "SG"


# --- Postal code validation_patterns ---

def test_postal_valid_ca_format_no_llm(monkeypatch):
    """A well-formed CA postal code must resolve deterministically — no LLM call."""
    mock_llm = MagicMock()
    skill = _skill()
    skill._llm = mock_llm
    result = skill.run({"postal_code": "M5V 2T6", "country": "CA"}, {})
    assert result["postal_code"] == "M5V 2T6"
    assert mock_llm.messages_create.call_count == 0


def test_postal_ca_spaceless_normalized():
    """CA postal code without space gets the standard separator inserted."""
    skill = _skill()
    result = skill.run({"postal_code": "M5V2T6", "country": "CA"}, {})
    assert result["postal_code"] == "M5V 2T6"


def test_postal_invalid_format_escalates(monkeypatch):
    """A postal code that fails its country pattern escalates to LLM."""
    skill = _skill()
    captured = []
    monkeypatch.setattr(skill, "_process_llm_batch", lambda record, dirty: captured.extend(dirty))
    skill.run({"postal_code": "XYZ", "country": "US"}, {})
    assert any(item["field"] == "postal_code" for item in captured)


def test_postal_no_country_escalates(monkeypatch):
    """Without country context, postal code format cannot be validated — escalate."""
    skill = _skill()
    captured = []
    monkeypatch.setattr(skill, "_process_llm_batch", lambda record, dirty: captured.extend(dirty))
    skill.run({"postal_code": "M5V 2T6"}, {})  # no country key
    assert any(item["field"] == "postal_code" for item in captured)


def test_postal_llm_receives_country_context():
    """When postal_code goes to LLM, the country value from the record is included."""
    mock_llm = MagicMock()
    mock_content = MagicMock()
    mock_content.text = json.dumps([
        {"field": "postal_code", "original": "XYZ", "corrected": None, "confidence": 0.2, "reason": "invalid"},
    ])
    mock_llm.messages_create.return_value = MagicMock(content=[mock_content])
    skill = _skill()
    skill._llm = mock_llm
    skill.run({"postal_code": "XYZ", "country": "CA"}, {})
    call_kwargs = mock_llm.messages_create.call_args
    user_message = str(call_kwargs)
    assert "CA" in user_message  # country_context injected into payload
```

- [ ] **Step 2: Run tests to confirm they fail**

```bash
python -m pytest tests/skills/test_field_cleaner.py -v 2>&1 | head -20
```

Expected: `ImportError` — `field_cleaner.py` doesn't exist yet.

- [ ] **Step 3: Implement FieldCleanerSkill (deterministic path only — LLM stub)**

```python
# skills/_common/field_cleaner/field_cleaner.py
import json
import logging
import re
import yaml
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

logger = logging.getLogger(__name__)

from skills.base import BaseSkill
from skills._common.field_cleaner.resolver import FieldTypeResolver


_RULES_DIR = Path(__file__).parent / "rules"


class FieldCleanerSkill(BaseSkill):
    """Validate and normalise field values for any domain.

    Deterministic first (base rules + learned DB corrections).
    Single batched LLM call per record for residual ambiguity.
    High-confidence LLM corrections written back to DB for future deterministic use.
    Sensitive fields skipped entirely — values never touched or logged.
    """

    def __init__(self, config: Optional[Dict[str, Any]] = None):
        super().__init__(config)
        self._conn = self.config.get("pg_conn")
        self._llm = self.config.get("llm_client")
        self.domain = self.config.get("domain", "")
        self.learning_threshold = self.config.get("learning_confidence_threshold", 0.90)

        self._resolver = FieldTypeResolver(self.config, pg_conn=self._conn, domain=self.domain)
        self._rules: Dict[str, dict] = self._load_all_rules()
        self._learned: Dict[Tuple[str, str], str] = self._load_learned()

    def _load_all_rules(self) -> Dict[str, dict]:
        rules = {}
        if not _RULES_DIR.exists():
            return rules
        for path in _RULES_DIR.glob("*.yaml"):
            try:
                with open(path) as f:
                    data = yaml.safe_load(f)
                field_type = data.get("field_type")
                if field_type:
                    rules[field_type] = data
            except Exception as e:
                logger.warning("Failed to load rules from %s: %s", path, e)
        return rules

    def _load_learned(self) -> Dict[Tuple[str, str], str]:
        if not self._conn:
            return {}
        try:
            from db.schema_config import get_framework_schema
            schema = get_framework_schema()
            domain = self.domain or "_common"
            with self._conn.cursor() as cur:
                cur.execute(
                    f"SELECT field_type, raw_value, corrected_value "
                    f"FROM {schema}.learned_field_corrections "
                    f"WHERE domain = %s AND enabled = TRUE",
                    (domain,),
                )
                return {(row[0], row[1].lower()): row[2] for row in cur.fetchall()}
        except Exception:
            return {}

    def run(self, record: Dict[str, Any], tools: Dict[str, Any] = None) -> Dict[str, Any]:
        self.clear_audit()
        field_names = [k for k in record if not k.startswith("_")]
        type_map = self._resolver.resolve(field_names)

        needs_llm: List[Dict] = []

        for field, field_type in type_map.items():
            value = record.get(field)

            if field_type == "sensitive":
                self.log_decision(f"{field}: skipped", "sensitive field", confidence=1.0)
                continue

            if not value or not isinstance(value, str):
                continue

            rules = self._rules.get(field_type, {})
            result = self._deterministic(field, value, field_type, rules, record)

            if result is not None:
                corrected, confidence, source = result
                if corrected != value:
                    record[field] = corrected
                decision = f"{field}: '{value}' → '{corrected}'" if corrected != value else f"{field}: valid"
                self.log_decision(decision, source, confidence=confidence)
            else:
                needs_llm.append({"field": field, "value": value, "field_type": field_type, "rules": rules})

        if needs_llm:
            self._process_llm_batch(record, needs_llm)

        return record

    def _deterministic(
        self, field: str, value: str, field_type: str, rules: dict, record: dict
    ) -> Optional[Tuple[str, float, str]]:
        """Return (corrected, confidence, source) or None if LLM needed.

        Order matters: base rules before learned corrections so static rules cannot
        be overridden by a bad learned correction that was written at high confidence.
        """
        value_lower = value.lower().strip()
        stripped = value.strip()

        # 1. normalization_map — base rules file; immutable ground truth, always wins
        norm_map = {k.lower(): v for k, v in rules.get("normalization_map", {}).items()}
        if value_lower in norm_map:
            return (norm_map[value_lower], 1.0, "normalization_map")

        # 2. Learned corrections — fill gaps the base map doesn't cover
        learned_key = (field_type, value_lower)
        if learned_key in self._learned:
            return (self._learned[learned_key], 1.0, "learned correction")

        # 3. City: deterministic title case — no LLM needed for case normalization
        if field_type == "city":
            if any(re.fullmatch(p, stripped) for p in rules.get("reject_patterns", [])):
                return None  # numeric-only / single char → LLM
            if stripped == stripped.lower() or stripped == stripped.upper():
                return (stripped.title(), 0.95, "title case normalization")
            return (stripped, 1.0, "already title case")

        # 4. Country: single-pattern check — already a valid ISO alpha-2 code
        vp = rules.get("validation_pattern")
        if vp and re.fullmatch(vp, stripped.upper()):
            return (stripped.upper(), 1.0, "already valid ISO code")

        # 5. validation_patterns — country-aware format validation (postal_code primary use)
        patterns = rules.get("validation_patterns", {})
        if patterns:
            country_code = record.get("country", "").upper()
            if country_code and country_code in patterns:
                normalized = stripped.upper()
                spaceless = normalized.replace(" ", "")
                if re.fullmatch(patterns[country_code], normalized) or re.fullmatch(patterns[country_code], spaceless):
                    # Enforce standard CA spacing: A1A1A1 → A1A 1A1
                    if country_code == "CA" and len(spaceless) == 6 and " " not in normalized:
                        normalized = spaceless[:3] + " " + spaceless[3:]
                    return (normalized, 1.0, "valid format")
            elif not country_code:
                # Cannot validate format without country context — escalate
                return None

        # 6. reject_patterns — escalate immediately
        for pattern in rules.get("reject_patterns", []):
            if re.fullmatch(pattern, stripped):
                return None

        # 7. reject_if_empty
        if rules.get("reject_if_empty") and not stripped:
            return None

        # 8. No rules matched and nothing rejected — escalate as ambiguous
        return None

    def _process_llm_batch(self, record: Dict[str, Any], dirty_fields: List[Dict]) -> None:
        """Single LLM call covering all unresolved fields. Stub — implemented in Task 5."""
        pass

    def _save_learned(self, field_type: str, raw_value: str, corrected_value: str, confidence: float) -> None:
        """Upsert to learned_field_corrections. Implemented in Task 6."""
        pass

    def _get_llm(self):
        if self._llm is None:
            from cleaning.llm_client import build_client_for_tier
            self._llm = build_client_for_tier("fast")
        return self._llm
```

- [ ] **Step 4: Run tests to confirm they pass**

```bash
python -m pytest tests/skills/test_field_cleaner.py -v
```

Expected: all tests pass. The `_process_llm_batch` stub means LLM tests don't exist yet.

- [ ] **Step 5: Commit**

```bash
git add skills/_common/field_cleaner/field_cleaner.py tests/skills/test_field_cleaner.py
git commit -m "feat: FieldCleanerSkill deterministic path — normalization, canonical check, reject escalation"
```

---

## Task 5: FieldCleanerSkill — LLM Path

**Files:**
- Modify: `skills/_common/field_cleaner/field_cleaner.py` (implement `_process_llm_batch`)
- Modify: `tests/skills/test_field_cleaner.py` (add LLM tests)

- [ ] **Step 1: Add LLM tests to test file**

Append to `tests/skills/test_field_cleaner.py`:

```python
# --- LLM path ---

def _mock_llm_response(corrections: list):
    """Build a mock llm_client that returns the given corrections as JSON."""
    mock_llm = MagicMock()
    mock_content = MagicMock()
    mock_content.text = json.dumps(corrections)
    mock_llm.messages_create.return_value = MagicMock(content=[mock_content])
    return mock_llm


def test_llm_called_once_for_batch():
    mock_llm = _mock_llm_response([
        {"field": "gender", "original": "T", "corrected": "Male", "confidence": 0.95, "reason": "likely abbreviation"},
    ])
    skill = _skill()
    skill._llm = mock_llm
    record = {"gender": "T"}
    skill.run(record, {})
    assert mock_llm.messages_create.call_count == 1


def test_llm_high_confidence_applied():
    mock_llm = _mock_llm_response([
        {"field": "gender", "original": "T", "corrected": "Male", "confidence": 0.95, "reason": "abbreviation"},
    ])
    skill = _skill()
    skill._llm = mock_llm
    record = {"gender": "T"}
    result = skill.run(record, {})
    assert result["gender"] == "Male"


def test_llm_medium_confidence_applied_not_learned():
    mock_llm = _mock_llm_response([
        {"field": "gender", "original": "T", "corrected": "Male", "confidence": 0.80, "reason": "likely"},
    ])
    save_calls = []
    skill = _skill()
    skill._llm = mock_llm
    skill._save_learned = lambda *a: save_calls.append(a)
    record = {"gender": "T"}
    result = skill.run(record, {})
    assert result["gender"] == "Male"
    assert len(save_calls) == 0  # not learned — below threshold


def test_llm_low_confidence_not_applied():
    mock_llm = _mock_llm_response([
        {"field": "gender", "original": "T", "corrected": "Male", "confidence": 0.50, "reason": "guess"},
    ])
    skill = _skill()
    skill._llm = mock_llm
    record = {"gender": "T"}
    result = skill.run(record, {})
    assert result["gender"] == "T"  # unchanged
    audit = skill.get_audit()
    assert any("unresolvable" in e["decision"] for e in audit)


def test_llm_not_called_when_deterministic_resolves():
    mock_llm = _mock_llm_response([])
    skill = _skill()
    skill._llm = mock_llm
    record = {"gender": "Male"}  # already canonical
    skill.run(record, {})
    assert mock_llm.messages_create.call_count == 0


def test_llm_not_called_when_no_llm_client():
    skill = _skill()  # llm_client=None
    record = {"gender": "T"}
    # Should not raise — logs decision and moves on
    result = skill.run(record, {})
    assert result["gender"] == "T"
    audit = skill.get_audit()
    assert any("gender" in e["decision"] for e in audit)


def test_llm_guardrails_injected_in_system_prompt():
    mock_llm = _mock_llm_response([
        {"field": "gender", "original": "T", "corrected": "Unknown", "confidence": 0.92, "reason": "invalid"},
    ])
    skill = _skill()
    skill._llm = mock_llm
    record = {"gender": "T"}
    skill.run(record, {})
    call_kwargs = mock_llm.messages_create.call_args
    system_prompt = call_kwargs[1].get("system", "") or call_kwargs[0][0]
    assert "Single letters" in system_prompt or "Never infer gender from a name" in system_prompt


def test_llm_invalid_json_handled_gracefully():
    mock_llm = MagicMock()
    mock_content = MagicMock()
    mock_content.text = "this is not json {"
    mock_llm.messages_create.return_value = MagicMock(content=[mock_content])
    skill = _skill()
    skill._llm = mock_llm
    record = {"gender": "T"}
    result = skill.run(record, {})
    assert result["gender"] == "T"  # unchanged, no crash
```

- [ ] **Step 2: Run new tests to confirm they fail**

```bash
python -m pytest tests/skills/test_field_cleaner.py::test_llm_high_confidence_applied -v
```

Expected: FAIL — `_process_llm_batch` is a stub that does nothing.

- [ ] **Step 3: Implement `_process_llm_batch` and `_apply_llm_results`**

Replace the `_process_llm_batch` stub in `skills/_common/field_cleaner/field_cleaner.py`:

```python
    def _process_llm_batch(self, record: Dict[str, Any], dirty_fields: List[Dict]) -> None:
        llm = self._get_llm()
        if not llm:
            for item in dirty_fields:
                self.log_decision(f"{item['field']}: skipped", "no llm_client configured", 0.0)
            return

        # Build guardrails block — one section per unique field type
        seen_types: set = set()
        guardrails_sections = []
        for item in dirty_fields:
            ft = item["field_type"]
            if ft not in seen_types:
                seen_types.add(ft)
                guardrails = item["rules"].get("guardrails", [])
                if guardrails:
                    block = f"## Rules for {ft}:\n" + "\n".join(f"- {g}" for g in guardrails)
                    guardrails_sections.append(block)

        system = (
            "You are a data cleaning assistant. Normalise and validate the provided field values "
            "using general data quality knowledge and the field-specific rules below.\n\n"
            + "\n\n".join(guardrails_sections)
            + "\n\nReturn ONLY a JSON array — one object per field — with this exact shape:\n"
            '[{"field": "<name>", "original": "<value>", "corrected": "<value or null>", '
            '"confidence": 0.0, "reason": "<brief reason>"}]'
        )

        payload_items = []
        for i in dirty_fields:
            item = {"field": i["field"], "field_type": i["field_type"], "value": i["value"]}
            # Postal code validation is country-dependent — pass country from same record
            if i["field_type"] == "postal_code" and record.get("country"):
                item["country_context"] = record["country"]
            payload_items.append(item)
        fields_payload = json.dumps(payload_items, indent=2)

        try:
            resp = llm.messages_create(
                system=system,
                messages=[{"role": "user", "content": f"Clean these fields:\n{fields_payload}"}],
                tools=[],
                max_tokens=1024,
            )
            text = next((b.text for b in resp.content if hasattr(b, "text")), "[]")
        except Exception as e:
            for item in dirty_fields:
                self.log_decision(f"{item['field']}: LLM error", str(e)[:80], 0.0)
            return

        self._apply_llm_results(record, text, dirty_fields)

    def _apply_llm_results(
        self, record: Dict[str, Any], text: str, dirty_fields: List[Dict]
    ) -> None:
        text = re.sub(r"```(?:json)?", "", text).strip().rstrip("`")
        try:
            results = json.loads(text)
        except Exception:
            for item in dirty_fields:
                self.log_decision(f"{item['field']}: parse error", "LLM returned invalid JSON", 0.0)
            return

        result_map = {r.get("field"): r for r in results if isinstance(r, dict)}

        for item in dirty_fields:
            field = item["field"]
            r = result_map.get(field)
            if not r:
                self.log_decision(f"{field}: no LLM result", "field missing from LLM response", 0.0)
                continue

            confidence = float(r.get("confidence", 0.0))
            corrected = r.get("corrected")
            reason = r.get("reason", "")
            original = item["value"]

            if confidence >= 0.70 and corrected and corrected != "null":
                record[field] = corrected
                if confidence >= self.learning_threshold:
                    self._save_learned(item["field_type"], original, corrected, confidence)
                    source = f"LLM (learned): {reason}"
                else:
                    source = f"LLM (needs_review): {reason}"
                self.log_decision(f"{field}: '{original}' → '{corrected}'", source, confidence)
            else:
                self.log_decision(
                    f"{field}: unresolvable",
                    f"LLM confidence {confidence:.2f}: {reason}",
                    confidence,
                )
```

- [ ] **Step 4: Run all tests**

```bash
python -m pytest tests/skills/test_field_cleaner.py -v
```

Expected: all tests pass.

- [ ] **Step 5: Commit**

```bash
git add skills/_common/field_cleaner/field_cleaner.py tests/skills/test_field_cleaner.py
git commit -m "feat: FieldCleanerSkill LLM path — batched call, confidence routing, guardrail injection"
```

---

## Task 6: Self-Learning Write-Back

**Files:**
- Modify: `skills/_common/field_cleaner/field_cleaner.py` (implement `_save_learned`)
- Modify: `tests/skills/test_field_cleaner.py` (add learning tests)

- [ ] **Step 1: Add learning tests**

Append to `tests/skills/test_field_cleaner.py`:

```python
# --- Self-learning ---

def test_high_confidence_llm_writes_to_learned():
    mock_llm = _mock_llm_response([
        {"field": "gender", "original": "T", "corrected": "Male", "confidence": 0.95, "reason": "abbrev"},
    ])
    conn = MagicMock()
    cursor = MagicMock()
    cursor.__enter__ = lambda s: s
    cursor.__exit__ = MagicMock(return_value=False)
    conn.cursor.return_value = cursor

    with patch("skills._common.field_cleaner.field_cleaner.get_framework_schema", return_value="public"):
        skill = _skill(conn=conn)
    skill._llm = mock_llm
    skill.run({"gender": "T"}, {})

    assert cursor.execute.called
    sql_call = cursor.execute.call_args[0][0]
    assert "learned_field_corrections" in sql_call
    assert "INSERT" in sql_call


def test_high_confidence_updates_in_memory_learned_cache():
    mock_llm = _mock_llm_response([
        {"field": "gender", "original": "T", "corrected": "Male", "confidence": 0.95, "reason": "abbrev"},
    ])
    skill = _skill()
    skill._llm = mock_llm
    skill.run({"gender": "T"}, {})
    # After learning, in-memory cache should have the correction
    assert skill._learned.get(("gender", "t")) == "Male"


def test_second_occurrence_hits_deterministic_path():
    """After learning, the same value should not call LLM again."""
    mock_llm = _mock_llm_response([
        {"field": "gender", "original": "T", "corrected": "Male", "confidence": 0.95, "reason": "abbrev"},
    ])
    skill = _skill()
    skill._llm = mock_llm

    skill.run({"gender": "T"}, {})  # first — LLM fires + learns
    mock_llm.messages_create.reset_mock()
    skill.run({"gender": "T"}, {})  # second — deterministic

    assert mock_llm.messages_create.call_count == 0


def test_below_threshold_confidence_does_not_write_to_db():
    mock_llm = _mock_llm_response([
        {"field": "gender", "original": "T", "corrected": "Male", "confidence": 0.80, "reason": "likely"},
    ])
    conn = MagicMock()
    cursor = MagicMock()
    cursor.__enter__ = lambda s: s
    cursor.__exit__ = MagicMock(return_value=False)
    conn.cursor.return_value = cursor

    with patch("skills._common.field_cleaner.field_cleaner.get_framework_schema", return_value="public"):
        skill = _skill(conn=conn)
    skill._llm = mock_llm
    skill.run({"gender": "T"}, {})

    # cursor.execute called for _load_learned at init, but NOT for insert
    insert_calls = [c for c in cursor.execute.call_args_list if "INSERT" in str(c)]
    assert len(insert_calls) == 0
```

- [ ] **Step 2: Run new tests to confirm they fail**

```bash
python -m pytest tests/skills/test_field_cleaner.py::test_high_confidence_llm_writes_to_learned -v
```

Expected: FAIL — `_save_learned` is a stub.

- [ ] **Step 3: Implement `_save_learned`**

Replace the `_save_learned` stub in `skills/_common/field_cleaner/field_cleaner.py`:

```python
    def _save_learned(
        self, field_type: str, raw_value: str, corrected_value: str, confidence: float
    ) -> None:
        raw_lower = raw_value.lower()
        # Update in-memory cache immediately so next record in batch benefits
        self._learned[(field_type, raw_lower)] = corrected_value

        if not self._conn:
            return
        try:
            from db.schema_config import get_framework_schema
            schema = get_framework_schema()
            domain = self.domain or "_common"
            with self._conn.cursor() as cur:
                cur.execute(
                    f"""
                    INSERT INTO {schema}.learned_field_corrections
                        (field_type, domain, raw_value, corrected_value, confidence, times_seen, last_seen_at)
                    VALUES (%s, %s, %s, %s, %s, 1, NOW())
                    ON CONFLICT (field_type, domain, raw_value) DO UPDATE SET
                        times_seen = {schema}.learned_field_corrections.times_seen + 1,
                        last_seen_at = NOW(),
                        corrected_value = EXCLUDED.corrected_value,
                        confidence = EXCLUDED.confidence
                    """,
                    (field_type, domain, raw_lower, corrected_value, confidence),
                )
        except Exception as e:
            logger.warning("Failed to write learned correction (%s, %s): %s", field_type, raw_lower, e)
```

- [ ] **Step 4: Run all tests**

```bash
python -m pytest tests/skills/test_field_cleaner.py -v
```

Expected: all tests pass.

- [ ] **Step 5: Commit**

```bash
git add skills/_common/field_cleaner/field_cleaner.py tests/skills/test_field_cleaner.py
git commit -m "feat: FieldCleanerSkill self-learning — upsert to learned_field_corrections on high-confidence LLM corrections"
```

---

## Task 7: skill.md

**Files:**
- Create: `skills/_common/field_cleaner/skill.md`

- [ ] **Step 1: Write skill.md**

```markdown
# Field Cleaner Skill

## Purpose
Validate and normalise field values for any industry domain.
Handles gender, city, postal_code, and country out of the box.
New field types are added by dropping a new `rules/<type>.yaml` file — no code changes.

## When to Use
- **DO**: After spell_checker and address_standardizer (fields are already typo-corrected)
- **DO**: On any record with gender, city, postal_code, or country fields
- **DO**: When a field value is present but may be in a non-canonical form (abbreviation, wrong case, full name instead of code)
- **DON'T**: On PII fields — list them in `sensitive_fields` config and they will be skipped
- **DON'T**: For address abbreviation expansion — that is address_standardizer's job

## Configuration
```yaml
field_cleaner:
  config:
    field_overrides:                    # explicit field_name → field_type mapping
      zip: postal_code
      sex: gender
    sensitive_fields: [ssn, sin, dob]   # always skipped; value never logged
    learning_confidence_threshold: 0.90 # min LLM confidence to write learned correction
```

## Field Type Resolution (priority order)
1. `sensitive_fields` config or annotation → skip entirely
2. `field_overrides` config → explicit type wins
3. `column_metadata.field_type` annotation → DB-driven type
4. Convention (field name pattern) → last-resort heuristic

## Processing Flow
1. Deterministic: normalization_map → learned corrections → city title case / validation_pattern / validation_patterns → reject_pattern
2. LLM (single batched call per record) for anything unresolved; postal_code fields include country context
3. Corrections ≥ 0.90 confidence written to `learned_field_corrections` table

## Output Fields Added
- Field values modified in place (no new keys)
- `_<field>: skipped` audit entry for sensitive fields (value never logged)

## Supported Field Types
| Type | Key normalizations |
|------|-------------------|
| `gender` | M→Male, F→Female, NB→Non-binary; rejects single letters via LLM |
| `city` | Title case; rejects numeric-only values |
| `postal_code` | Country-aware format validation; CA/US/GB patterns |
| `country` | Full name → ISO 3166-1 alpha-2 code |

## Adding a New Field Type
Drop `skills/_common/field_cleaner/rules/<type>.yaml` with keys:
`field_type`, `normalization_map`, `canonical_values`, `guardrails`, `reject_patterns`
No code changes required.

## Dependencies
- spell_checker (run first — typos corrected before type validation)
- address_standardizer (run first — address fields already expanded)
- DB connection optional — without it, learned corrections are empty (still works)
- LLM client optional — without it, unresolved fields are logged and left unchanged
```

- [ ] **Step 2: Commit**

```bash
git add skills/_common/field_cleaner/skill.md
git commit -m "docs: field_cleaner skill.md for LLM planner"
```

---

## Task 8: Wire into skills.yaml

**Files:**
- Modify: `skills/real_estate/skills.yaml`
- Modify: `skills/sports_ticketing/skills.yaml`

- [ ] **Step 1: Add field_cleaner to real_estate and remove geographic_validator**

In `skills/real_estate/skills.yaml`, add after `record_linker` and before `municipality_authority`:

```yaml
  field_cleaner:
    class: skills._common.field_cleaner.field_cleaner.FieldCleanerSkill
    skill_doc: skills/_common/field_cleaner/skill.md
    tools: []
    config:
      field_overrides:
        zip: postal_code
        postcode: postal_code
        sex: gender
        nation: country
      sensitive_fields: [ssn, sin, dob, credit_card, tax_id, passport]
      pg_conn: "${runtime.pg_conn}"
      llm_client: "${runtime.llm_client}"
      learning_confidence_threshold: 0.90
      domain: real_estate
    cost: medium   # deterministic path is low; LLM path (unresolved fields) is high — medium is the honest estimate
    phase: 1
    latency_estimate_ms: 200   # deterministic ~50ms; LLM path ~1500ms; medium reflects mixed batches
    depends_on: [spell_checker, address_standardizer]
```

Remove the entire `geographic_validator:` block from `skills/real_estate/skills.yaml`.

Also remove `geographic_validator` from `data_quality_triage.depends_on` — change it to:

```yaml
    depends_on: [municipality_authority]
```

- [ ] **Step 2: Add field_cleaner to sports_ticketing and remove event_normalizer**

In `skills/sports_ticketing/skills.yaml`, add after `record_linker`:

```yaml
  field_cleaner:
    class: skills._common.field_cleaner.field_cleaner.FieldCleanerSkill
    skill_doc: skills/_common/field_cleaner/skill.md
    tools: []
    config:
      field_overrides:
        sex: gender
        nation: country
      sensitive_fields: [ssn, sin, dob, credit_card]
      pg_conn: "${runtime.pg_conn}"
      llm_client: "${runtime.llm_client}"
      learning_confidence_threshold: 0.90
      domain: sports_ticketing
    cost: medium
    phase: 1
    latency_estimate_ms: 200
    depends_on: [spell_checker]
```

Remove the entire `event_normalizer:` block from `skills/sports_ticketing/skills.yaml`.

- [ ] **Step 3: Verify registry loads cleanly**

```bash
python -c "
from skills.registry import SkillRegistry
r = SkillRegistry.load('real_estate')
print(r)
r.validate_dependencies()
print('real_estate OK')
r2 = SkillRegistry.load('sports_ticketing')
print(r2)
r2.validate_dependencies()
print('sports_ticketing OK')
"
```

Expected output contains `field_cleaner` in both registries, no `ValueError`.

- [ ] **Step 4: Commit**

```bash
git add skills/real_estate/skills.yaml skills/sports_ticketing/skills.yaml
git commit -m "feat: wire field_cleaner into real_estate and sports_ticketing skills.yaml"
```

---

## Task 9: Remove Scrapped Skills

**Files:**
- Delete: `skills/real_estate/geographic_validator/`
- Delete: `skills/real_estate/data_quality_triage/`
- Delete: `skills/sports_ticketing/event_normalizer/`

- [ ] **Step 1: Delete the directories**

```bash
rm -rf skills/real_estate/geographic_validator/
rm -rf skills/real_estate/data_quality_triage/
rm -rf skills/sports_ticketing/event_normalizer/
```

- [ ] **Step 2: Verify no imports reference removed modules**

```bash
grep -r "geographic_validator\|event_normalizer\|real_estate.data_quality_triage" \
  skills/ cleaning/ tests/ --include="*.py" -l
```

Expected: no output. If files are listed, remove the import from each.

- [ ] **Step 3: Run the full test suite**

```bash
python -m pytest tests/ -v
```

Expected: all tests pass. If any import errors appear from the deleted directories, fix the imports.

- [ ] **Step 4: Commit**

```bash
git add -A
git commit -m "chore: remove geographic_validator, real_estate data_quality_triage, event_normalizer — superseded by field_cleaner"
```

---

## Task 10: Hardcoded-Data Lock Test

**Files:**
- Modify: `tests/skills/test_field_cleaner.py`

This test ensures no normalization dictionaries or canonical value lists get hardcoded into the skill source files in the future (matching the project policy for `spell_corrections` and FSA data).

- [ ] **Step 1: Add lock test**

Append to `tests/skills/test_field_cleaner.py`:

```python
# --- Lock test: no hardcoded domain data in source files ---

def test_no_hardcoded_normalization_data_in_field_cleaner_source():
    """field_cleaner.py must not contain hardcoded normalization maps or canonical value lists.
    All such data must live in rules/*.yaml or the DB.
    """
    import ast
    from pathlib import Path

    source_path = Path("skills/_common/field_cleaner/field_cleaner.py")
    source = source_path.read_text()
    tree = ast.parse(source)

    suspicious_dicts = []
    for node in ast.walk(tree):
        # Flag module-level dict literals with more than 3 key-value pairs
        if isinstance(node, ast.Assign):
            for target in node.targets:
                if isinstance(target, ast.Name) and target.id.startswith("_"):
                    if isinstance(node.value, ast.Dict) and len(node.value.keys) > 3:
                        suspicious_dicts.append(target.id)

    assert suspicious_dicts == [], (
        f"Hardcoded data dicts found in field_cleaner.py: {suspicious_dicts}. "
        "Move them to skills/_common/field_cleaner/rules/<type>.yaml"
    )


def test_no_hardcoded_normalization_data_in_resolver_source():
    """resolver.py must not contain data — only field name patterns."""
    import ast
    from pathlib import Path

    source = Path("skills/_common/field_cleaner/resolver.py").read_text()
    tree = ast.parse(source)

    for node in ast.walk(tree):
        if isinstance(node, ast.Assign):
            for target in node.targets:
                if isinstance(node.value, ast.Dict) and len(node.value.keys) > 10:
                    assert False, (
                        f"Large dict '{getattr(target, 'id', '?')}' found in resolver.py — "
                        "domain data belongs in rules/*.yaml"
                    )
```

- [ ] **Step 2: Run the lock test**

```bash
python -m pytest tests/skills/test_field_cleaner.py::test_no_hardcoded_normalization_data_in_field_cleaner_source \
                 tests/skills/test_field_cleaner.py::test_no_hardcoded_normalization_data_in_resolver_source -v
```

Expected: both pass.

- [ ] **Step 3: Run full test suite one final time**

```bash
python -m pytest tests/ -v
```

Expected: all tests pass.

- [ ] **Step 4: Final commit**

```bash
git add tests/skills/test_field_cleaner.py
git commit -m "test: lock test — no hardcoded normalization data in field_cleaner source files"
```
