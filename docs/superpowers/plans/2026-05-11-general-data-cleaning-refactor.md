# General Data Cleaning Refactor — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

> **⚠️ NO COMMITS:** Do not run `git commit` at any step. User reviews all changes before committing. `git add` steps are included so the user can see staged state, but commits are user-triggered.

**Goal:** Refactor the data cleaning pipeline to move general skills (spell_checker, address_standardizer) to truly domain-agnostic `_common/` implementations driven by config, replace `fuzzy_matcher` with a proper `record_linker`, separate audit logs from data records, and run Phase 1 skills in parallel.

**Architecture:** 3-tier skills (Universal → Field-type → Domain). `_common/` skills have zero hardcoded field names — all driven by `text_fields`/`address_fields`/`match_rules` config in each domain's `skills.yaml`. `BaseSkill` accumulates audit entries internally; orchestrator extracts them separately from records. Phase 1 skills run concurrently via `ThreadPoolExecutor`.

**Tech Stack:** Python 3.12, symspellpy, pydantic>=2.0, concurrent.futures (stdlib), PyYAML, existing psycopg3 + skill registry.

---

## File Map

```
MODIFY:
  requirements.txt                                        add symspellpy, pydantic
  skills/base.py                                          audit accumulation pattern
  skills/_common/spell_checker/spell_checker.py           symspellpy + config text_fields
  skills/_common/spell_checker/skill.md                   generalize content
  skills/_common/address_standardizer/address_standardizer.py   config address_fields
  skills/_common/address_standardizer/skill.md            generalize content
  skills/real_estate/skills.yaml                          text_fields, address_fields, phase, record_linker, skill_doc paths
  skills/real_estate/geographic_validator/geographic_validator.py   remove _decisions from record
  skills/real_estate/municipality_authority/municipality_authority.py  remove _decisions from record
  skills/real_estate/nominatim_geocoder/nominatim_geocoder.py          remove _decisions from record
  skills/real_estate/data_quality_triage/data_quality_triage.py        remove _decisions from record
  skills/sports_ticketing/skills.yaml                     add spell_checker, record_linker
  skills/registry.py                                      phase field, Phase 1 disjointness validation
  cleaning/orchestrator_v2.py                             parallel Phase 1, audit separation, batch record_linker pass

CREATE:
  skills/models.py                                        AuditEntry, SkillResult pydantic models
  skills/_common/record_linker/__init__.py
  skills/_common/record_linker/record_linker.py           Union-Find transitive matching
  skills/_common/record_linker/skill.md
  skills/real_estate/record_linker/__init__.py
  skills/real_estate/record_linker/record_linker.py       thin re-export wrapper
  skills/sports_ticketing/record_linker/__init__.py
  skills/sports_ticketing/record_linker/record_linker.py  thin re-export wrapper

DELETE:
  skills/_common/fuzzy_matcher/fuzzy_matcher.py
  skills/_common/fuzzy_matcher/__init__.py
  skills/real_estate/fuzzy_matcher/fuzzy_matcher.py
  skills/real_estate/fuzzy_matcher/skill.md

UPDATE TESTS:
  tests/test_skill_registry.py                            _decisions → audit, fuzzy→record_linker
  tests/test_full_agent_pipeline.py                       _decisions → audit, field configs
  tests/cleaning/test_spell_corrections.py                text_fields config, _overrides attr
```

---

## Task 1: Add Dependencies

**Files:**
- Modify: `requirements.txt`

- [ ] **Step 1: Add symspellpy and pydantic**

```
anthropic>=0.40
psycopg[binary]>=3.2
requests>=2.32
beautifulsoup4>=4.12
pyshp>=2.3
symspellpy>=6.7
pydantic>=2.0
```

- [ ] **Step 2: Install**

```bash
pip install symspellpy pydantic
```

Expected: both install without errors.

- [ ] **Step 3: Smoke test symspellpy loads its bundled dictionary**

```bash
python -c "
import pkg_resources
from symspellpy import SymSpell, Verbosity
ss = SymSpell(max_dictionary_edit_distance=2)
path = pkg_resources.resource_filename('symspellpy', 'frequency_dictionary_en_82_765.txt')
ss.load_dictionary(path, term_index=0, count_index=1)
hits = ss.lookup('toronot', Verbosity.CLOSEST, max_edit_distance=2)
print(hits[0].term)  # expected: toronto
"
```

Expected output: `toronto`

---

## Task 2: Create Pydantic Models

**Files:**
- Create: `skills/models.py`

- [ ] **Step 1: Write the failing test**

Add to `tests/test_skill_registry.py`:

```python
def test_audit_entry_model():
    from skills.models import AuditEntry
    entry = AuditEntry(
        skill="SpellChecker",
        field="city",
        original="toronot",
        corrected="toronto",
        reason="symspellpy edit_dist=1",
        confidence=0.9,
    )
    assert entry.skill == "SpellChecker"
    assert entry.confidence == 0.9
```

- [ ] **Step 2: Run to verify it fails**

```bash
cd /mnt/f/AI_learning_project && python -m pytest tests/test_skill_registry.py::test_audit_entry_model -v
```

Expected: `ModuleNotFoundError: No module named 'skills.models'`

- [ ] **Step 3: Create `skills/models.py`**

```python
from pydantic import BaseModel, ConfigDict
from typing import Any


class AuditEntry(BaseModel):
    skill: str
    field: str
    original: Any
    corrected: Any
    reason: str
    confidence: float


class SkillResult(BaseModel):
    model_config = ConfigDict(extra="allow")
    record: dict[str, Any]
    audit: list[AuditEntry]
    confidence: float
```

- [ ] **Step 4: Run test to verify it passes**

```bash
python -m pytest tests/test_skill_registry.py::test_audit_entry_model -v
```

Expected: PASS

---

## Task 3: Update BaseSkill — Audit Accumulation

**Files:**
- Modify: `skills/base.py`

Skills currently call `self.log_decision()` and store the return value in a local list, then set `input_data["_decisions"] = decisions`. After this task, `log_decision()` still returns a dict (backward compat for existing skills) but ALSO appends an `AuditEntry` to `self._audit_entries`. Orchestrator will extract audit via `get_audit()` and strip `_decisions` from records.

- [ ] **Step 1: Write the failing test**

Add to `tests/test_skill_registry.py`:

```python
def test_baseskill_audit_accumulation():
    from skills.models import AuditEntry
    registry = SkillRegistry.load("real_estate")
    spell = registry.get("spell_checker")

    spell.clear_audit()
    spell.run({"city": "toronot"}, {})  # no corrections loaded but audit API must exist
    entries = spell.get_audit()
    assert isinstance(entries, list)
```

- [ ] **Step 2: Run to verify it fails**

```bash
python -m pytest tests/test_skill_registry.py::test_baseskill_audit_accumulation -v
```

Expected: `AttributeError: 'SpellChecker' object has no attribute 'clear_audit'`

- [ ] **Step 3: Update `skills/base.py`**

```python
"""Base skill class for all domain skills."""

from abc import ABC, abstractmethod
from typing import Any, Dict, List, Optional


class BaseSkill(ABC):
    """Base class for all skills. Subclass for domain-specific implementations."""

    def __init__(self, config: Optional[Dict[str, Any]] = None):
        self.config = config or {}
        self.name = self.__class__.__name__
        self.domain = None
        self._audit_entries: List[Dict] = []

    @abstractmethod
    def run(self, input_data: Dict[str, Any], tools: Dict[str, Any] = None) -> Dict[str, Any]:
        pass

    def validate_config(self, required_keys: list) -> bool:
        return all(key in self.config for key in required_keys)

    def log_decision(self, decision: str, reason: str, confidence: float = 1.0) -> dict:
        entry = {
            "skill": self.name,
            "decision": decision,
            "reason": reason,
            "confidence": confidence,
        }
        self._audit_entries.append(entry)
        return entry

    def get_audit(self) -> List[Dict]:
        return list(self._audit_entries)

    def clear_audit(self):
        self._audit_entries = []
```

- [ ] **Step 4: Run test to verify it passes**

```bash
python -m pytest tests/test_skill_registry.py::test_baseskill_audit_accumulation -v
```

Expected: PASS

- [ ] **Step 5: Run full test suite to check no regressions**

```bash
python -m pytest tests/ -v --tb=short 2>&1 | tail -30
```

Expected: same pass/fail count as before this task.

---

## Task 4: Refactor SpellChecker — symspellpy + Config Fields

**Files:**
- Modify: `skills/_common/spell_checker/spell_checker.py`

Replaces hardcoded `("municipality", "address", "city")` with config-driven `text_fields`. Adds `symspellpy` as primary correction engine. Domain DB table becomes override layer for proper nouns. Removes `_decisions` from record — audit is on the instance.

- [ ] **Step 1: Write the failing tests**

Add to `tests/cleaning/test_spell_corrections.py`:

```python
def test_spell_checker_uses_symspellpy_for_obvious_typo():
    """symspellpy catches 'toronot' without any DB corrections loaded."""
    sc = SpellChecker({"threshold": 0.85, "text_fields": ["city"]})
    result = sc.run({"city": "toronot"})
    assert result["city"] == "toronto"


def test_spell_checker_only_touches_text_fields():
    """Fields not in text_fields must not be modified."""
    sc = SpellChecker({"threshold": 0.85, "text_fields": ["city"]})
    result = sc.run({"city": "toronot", "last_name": "Smyth"})
    assert result["city"] == "toronto"
    assert result["last_name"] == "Smyth"  # untouched


def test_spell_checker_no_text_fields_config_touches_nothing():
    """Empty text_fields → nothing processed."""
    sc = SpellChecker({"threshold": 0.85, "text_fields": []})
    result = sc.run({"city": "toronot"})
    assert result["city"] == "toronot"


def test_spell_checker_override_takes_priority():
    """Domain override table beats symspellpy — exact match wins at confidence=1.0."""
    overrides = {"scarbbrough": "scarborough"}
    with patch("cleaning.spell_corrections_data.get_corrections_dict", return_value=overrides):
        sc = SpellChecker({"pg_conn": MagicMock(), "threshold": 0.85, "text_fields": ["municipality"]})
    result = sc.run({"municipality": "scarbbrough"})
    assert result["municipality"] == "scarborough"
    audit = sc.get_audit()
    assert any("override" in e["reason"] for e in audit)


def test_spell_checker_audit_not_in_record():
    """_decisions must NOT appear in the returned record."""
    sc = SpellChecker({"threshold": 0.85, "text_fields": ["city"]})
    result = sc.run({"city": "toronot"})
    assert "_decisions" not in result
```

- [ ] **Step 2: Run to verify they fail**

```bash
python -m pytest tests/cleaning/test_spell_corrections.py::test_spell_checker_uses_symspellpy_for_obvious_typo tests/cleaning/test_spell_corrections.py::test_spell_checker_only_touches_text_fields tests/cleaning/test_spell_corrections.py::test_spell_checker_audit_not_in_record -v
```

Expected: all FAIL.

- [ ] **Step 3: Rewrite `skills/_common/spell_checker/spell_checker.py`**

```python
"""Domain-agnostic spell checker skill — symspellpy base + domain override table."""

import pkg_resources
from typing import Any, Dict, List, Optional

from skills.base import BaseSkill


class SpellChecker(BaseSkill):
    """Correct obvious spelling mistakes in configured text fields.

    Uses symspellpy (general English dictionary) as the primary engine.
    Domain-specific proper nouns are handled via an override table loaded from DB.
    Only fields listed in text_fields config are ever touched.
    """

    def __init__(self, config: Optional[Dict[str, Any]] = None):
        super().__init__(config)
        self.domain = self.config.get("domain", "")
        self.threshold = self.config.get("threshold", 0.85)
        self.text_fields: List[str] = self.config.get("text_fields", [])
        self.max_edit_distance = self.config.get("max_edit_distance", 2)

        self._sym_spell = self._load_symspell()
        conn = self.config.get("pg_conn")
        self._overrides: Dict[str, str] = self._load_overrides(conn)

    def _load_symspell(self):
        from symspellpy import SymSpell
        ss = SymSpell(max_dictionary_edit_distance=self.max_edit_distance, prefix_length=7)
        dict_path = pkg_resources.resource_filename(
            "symspellpy", "frequency_dictionary_en_82_765.txt"
        )
        ss.load_dictionary(dict_path, term_index=0, count_index=1)
        return ss

    def _load_overrides(self, conn) -> Dict[str, str]:
        if conn is None:
            return {}
        try:
            from cleaning.spell_corrections_data import get_corrections_dict
            return get_corrections_dict(conn, self.domain)
        except Exception:
            return {}

    def run(self, input_data: Dict[str, Any], tools: Dict[str, Any] = None) -> Dict[str, Any]:
        self.clear_audit()
        for field in self.text_fields:
            value = input_data.get(field)
            if not value or not isinstance(value, str):
                continue
            corrected = self._correct(value, field)
            if corrected != value:
                input_data[field] = corrected
        return input_data

    def _correct(self, text: str, field: str) -> str:
        from symspellpy import Verbosity

        text_lower = text.lower()

        # 1. Domain override table — exact match, confidence=1.0
        if text_lower in self._overrides:
            corrected = self._overrides[text_lower]
            out = corrected.title() if text[0].isupper() else corrected
            self.log_decision(
                f"{field}: '{text}' → '{out}'",
                "domain override",
                confidence=1.0,
            )
            return out

        # 2. symspellpy — general English dictionary
        suggestions = self._sym_spell.lookup(
            text_lower, Verbosity.CLOSEST, max_edit_distance=self.max_edit_distance
        )
        if not suggestions:
            return text
        best = suggestions[0]
        if best.term == text_lower:
            return text  # already correct
        confidence = max(0.0, 1.0 - best.distance * 0.1)
        if confidence < self.threshold:
            return text
        out = best.term.title() if text[0].isupper() else best.term
        self.log_decision(
            f"{field}: '{text}' → '{out}'",
            f"symspellpy (edit_dist={best.distance})",
            confidence=confidence,
        )
        return out
```

- [ ] **Step 4: Run new tests**

```bash
python -m pytest tests/cleaning/test_spell_corrections.py -v --tb=short 2>&1 | tail -40
```

Expected: new tests pass. Some old tests that check `sc.corrections` will fail — fix in Step 5.

- [ ] **Step 5: Update old tests in `tests/cleaning/test_spell_corrections.py` that reference removed attributes**

The attribute `sc.corrections` no longer exists — it is now `sc._overrides`. Update these tests:

```python
def test_spell_checker_no_conn_empty_corrections():
    """No pg_conn → SpellChecker._overrides is empty dict."""
    sc = SpellChecker({"threshold": 0.85, "text_fields": []})
    assert sc._overrides == {}


def test_spell_checker_db_error_falls_back_to_empty():
    """DB error during load → _overrides empty, no exception raised."""
    with patch("cleaning.spell_corrections_data.get_corrections_dict", side_effect=Exception("DB down")):
        mock_conn = MagicMock()
        sc = SpellChecker({"pg_conn": mock_conn, "threshold": 0.85, "text_fields": []})
    assert sc._overrides == {}


def test_spell_checker_with_db_corrections():
    """With DB corrections injected, SpellChecker corrects misspellings in text_fields."""
    corrections = {"toronot": "toronto", "scarbbrough": "scarborough"}
    with patch("cleaning.spell_corrections_data.get_corrections_dict", return_value=corrections):
        mock_conn = MagicMock()
        sc = SpellChecker({
            "pg_conn": mock_conn,
            "threshold": 0.85,
            "text_fields": ["city", "municipality"],
        })

    record = {"city": "toronot", "municipality": "scarbbrough", "address": "123 Main St"}
    result = sc.run(record)

    assert result["city"] == "toronto"
    assert result["municipality"] == "scarborough"
    assert result["address"] == "123 Main St"  # not in text_fields — untouched
    assert "_decisions" not in result           # audit is on the skill instance now
    audit = sc.get_audit()
    assert len(audit) == 2
```

Also update the lock test — it checks `spell_checker_module` source (still `_common` via thin wrapper import, so source is still checked correctly):

```python
def test_no_hardcoded_corrections_in_spell_checker_source():
    """Lock: hardcoded misspellings must NOT appear in SpellChecker source."""
    import skills._common.spell_checker.spell_checker as spell_checker_module
    src = inspect.getsource(spell_checker_module)
    assert "scarbbrough" not in src
    assert "toronot" not in src
    assert "etobicoe" not in src
```

- [ ] **Step 6: Run all spell correction tests**

```bash
python -m pytest tests/cleaning/test_spell_corrections.py -v
```

Expected: all PASS

---

## Task 5: Refactor AddressStandardizer — Config-Driven Fields

**Files:**
- Modify: `skills/_common/address_standardizer/address_standardizer.py`

Removes hardcoded `"address"` field. Reads `address_fields` list from config.

- [ ] **Step 1: Write the failing tests**

Add to `tests/test_skill_registry.py`:

```python
def test_address_standardizer_config_fields():
    """Standardizer only touches address_fields — not other fields."""
    from skills._common.address_standardizer.address_standardizer import AddressStandardizer
    std = AddressStandardizer({"address_fields": ["address", "mailing_address"]})
    result = std.run({
        "address": "123 Main St",
        "mailing_address": "456 Oak Ave",
        "notes": "Near Muir Ave",   # not in address_fields
    })
    assert "Street" in result["address"]
    assert "Avenue" in result["mailing_address"]
    assert result["notes"] == "Near Muir Ave"   # untouched


def test_address_standardizer_audit_not_in_record():
    """_decisions must NOT be in returned record."""
    from skills._common.address_standardizer.address_standardizer import AddressStandardizer
    std = AddressStandardizer({"address_fields": ["address"]})
    result = std.run({"address": "123 Main St"})
    assert "_decisions" not in result
```

- [ ] **Step 2: Run to verify they fail**

```bash
python -m pytest tests/test_skill_registry.py::test_address_standardizer_config_fields tests/test_skill_registry.py::test_address_standardizer_audit_not_in_record -v
```

Expected: FAIL

- [ ] **Step 3: Rewrite `skills/_common/address_standardizer/address_standardizer.py`**

```python
"""Domain-agnostic address standardization skill."""

import re
from typing import Any, Dict, List, Optional

from skills.base import BaseSkill


class AddressStandardizer(BaseSkill):
    """Expand address abbreviations in configured address fields.

    Runs on any field listed in address_fields config.
    Never touches fields not in address_fields.
    """

    STREET_TYPES = {
        r"\bst\b": "Street", r"\bave\b": "Avenue", r"\bavenue\b": "Avenue",
        r"\bblvd\b": "Boulevard", r"\brd\b": "Road", r"\blane\b": "Lane",
        r"\bln\b": "Lane", r"\bdr\b": "Drive", r"\bct\b": "Court",
        r"\bctr\b": "Center", r"\bpk\b": "Park", r"\bpkwy\b": "Parkway",
        r"\bter\b": "Terrace", r"\bpl\b": "Place", r"\bsq\b": "Square",
    }
    # Single-letter directionals intentionally omitted — too many false positives
    QUADRANTS = {
        r"\bNE\b": "Northeast", r"\bNW\b": "Northwest",
        r"\bSE\b": "Southeast", r"\bSW\b": "Southwest",
    }

    def __init__(self, config: Optional[Dict[str, Any]] = None):
        super().__init__(config)
        self.address_fields: List[str] = self.config.get("address_fields", [])
        self.strip_unit_numbers = self.config.get("strip_unit_numbers", False)

    def run(self, input_data: Dict[str, Any], tools: Dict[str, Any] = None) -> Dict[str, Any]:
        self.clear_audit()
        for field in self.address_fields:
            value = input_data.get(field)
            if not value or not isinstance(value, str):
                continue
            standardized = self._standardize(value)
            if standardized != value:
                self.log_decision(
                    f"{field}: '{value}' → '{standardized}'",
                    "address abbreviation expansion",
                    confidence=1.0,
                )
                input_data[field] = standardized
        return input_data

    def _standardize(self, address: str) -> str:
        if not address:
            return address
        if self.strip_unit_numbers:
            address = re.sub(
                r",?\s*(apt|apt\.|unit|unit\.|#)\s*\w+", "", address, flags=re.IGNORECASE
            )
        for pattern, expansion in self.QUADRANTS.items():
            address = re.sub(pattern, expansion, address, flags=re.IGNORECASE)
        for pattern, expansion in self.STREET_TYPES.items():
            address = re.sub(pattern, expansion, address, flags=re.IGNORECASE)
        return " ".join(address.split())
```

- [ ] **Step 4: Run new and existing standardizer tests**

```bash
python -m pytest tests/test_skill_registry.py::test_address_standardizer_config_fields tests/test_skill_registry.py::test_address_standardizer_audit_not_in_record tests/test_skill_registry.py::test_address_standardizer_skill -v
```

The existing `test_address_standardizer_skill` loads from registry — registry config now needs `address_fields`. This test will fail until Task 9 (YAML update). Note the failure, continue.

---

## Task 6: Create RecordLinker

**Files:**
- Create: `skills/_common/record_linker/__init__.py`
- Create: `skills/_common/record_linker/record_linker.py`
- Create: `skills/_common/record_linker/skill.md`

RecordLinker finds records referring to the same real-world entity. Config-driven match rules (exact or fuzzy), composite field keys, blocking to narrow candidates. Never mutates record fields. Two modes: per-record (`run()`) and batch with transitive closure (`link_batch()`).

- [ ] **Step 1: Write the failing tests**

Create `tests/test_record_linker.py`:

```python
"""Tests for RecordLinker skill."""

import pytest
from skills._common.record_linker.record_linker import RecordLinker


BASIC_CONFIG = {
    "blocking_fields": [],
    "match_rules": [
        {"name": "email_exact", "fields": ["email"], "match_type": "exact", "weight": 1.0},
        {
            "name": "name_company",
            "fields": ["first_name", "last_name", "company"],
            "match_type": "fuzzy",
            "threshold": 0.85,
            "weight": 0.90,
        },
    ],
}


def test_exact_match_links_records():
    linker = RecordLinker(BASIC_CONFIG)
    record = {"id": "A", "email": "user@example.com"}
    candidates = [
        {"id": "B", "email": "user@example.com"},
        {"id": "C", "email": "other@example.com"},
    ]
    result = linker.run(record, tools={"candidates": candidates})
    linked = result.get("_linked_records", [])
    ids = [r["id"] for r in linked]
    assert "B" in ids
    assert "C" not in ids


def test_fuzzy_match_links_near_identical():
    linker = RecordLinker(BASIC_CONFIG)
    record = {"id": "A", "first_name": "John", "last_name": "Smith", "company": "Acme Inc"}
    candidates = [{"id": "B", "first_name": "John", "last_name": "Smyth", "company": "Acme Inc"}]
    result = linker.run(record, tools={"candidates": candidates})
    linked = result.get("_linked_records", [])
    assert len(linked) == 1
    assert linked[0]["matched_rule"] == "name_company"


def test_no_match_returns_empty():
    linker = RecordLinker(BASIC_CONFIG)
    record = {"id": "A", "email": "a@a.com"}
    candidates = [{"id": "B", "email": "b@b.com"}]
    result = linker.run(record, tools={"candidates": candidates})
    assert result.get("_linked_records", []) == []


def test_record_fields_not_mutated():
    """RecordLinker must never change source field values."""
    linker = RecordLinker(BASIC_CONFIG)
    record = {"id": "A", "email": "user@example.com", "first_name": "John"}
    candidates = [{"id": "B", "email": "user@example.com", "first_name": "Jane"}]
    result = linker.run(record, tools={"candidates": candidates})
    assert result["first_name"] == "John"   # not overwritten with candidate's value
    assert result["email"] == "user@example.com"


def test_link_batch_transitive_grouping():
    """A→B on email, B→C on name → A,B,C same group."""
    config = {
        "blocking_fields": [],
        "match_rules": [
            {"name": "email", "fields": ["email"], "match_type": "exact", "weight": 1.0},
            {
                "name": "name",
                "fields": ["first_name", "last_name"],
                "match_type": "fuzzy",
                "threshold": 0.85,
                "weight": 0.9,
            },
        ],
    }
    linker = RecordLinker(config)
    records = [
        {"id": "A", "email": "shared@x.com", "first_name": "Alice", "last_name": "Smith"},
        {"id": "B", "email": "shared@x.com", "first_name": "Alice", "last_name": "Smyth"},
        {"id": "C", "email": "other@x.com",  "first_name": "Alice", "last_name": "Smith"},
    ]
    result = linker.link_batch(records)
    groups = {r["id"]: r["_group_id"] for r in result}
    # A and B share email → same group
    assert groups["A"] == groups["B"]
    # C matches B/A on name → same group transitively
    assert groups["C"] == groups["A"]


def test_link_batch_no_cross_contamination():
    """Unrelated records get distinct group_ids."""
    linker = RecordLinker(BASIC_CONFIG)
    records = [
        {"id": "X", "email": "x@x.com"},
        {"id": "Y", "email": "y@y.com"},
    ]
    result = linker.link_batch(records)
    groups = {r["id"]: r["_group_id"] for r in result}
    assert groups["X"] != groups["Y"]


def test_audit_not_in_record():
    linker = RecordLinker(BASIC_CONFIG)
    result = linker.run({"id": "A", "email": "a@a.com"}, tools={"candidates": []})
    assert "_decisions" not in result
```

- [ ] **Step 2: Run to verify they fail**

```bash
python -m pytest tests/test_record_linker.py -v
```

Expected: `ModuleNotFoundError: No module named 'skills._common.record_linker'`

- [ ] **Step 3: Create `skills/_common/record_linker/__init__.py`**

Empty file.

- [ ] **Step 4: Create `skills/_common/record_linker/record_linker.py`**

```python
"""Domain-agnostic record linker — config-driven match rules, transitive grouping."""

from typing import Any, Dict, List, Optional

from skills.base import BaseSkill


def _levenshtein(s1: str, s2: str) -> int:
    if len(s1) < len(s2):
        return _levenshtein(s2, s1)
    if not s2:
        return len(s1)
    prev = range(len(s2) + 1)
    for c1 in s1:
        curr = [prev[0] + 1]
        for j, c2 in enumerate(s2):
            curr.append(min(curr[j] + 1, prev[j + 1] + 1, prev[j] + (c1 != c2)))
        prev = curr
    return prev[-1]


def _fuzzy_similarity(a: str, b: str) -> float:
    a, b = a.lower().strip(), b.lower().strip()
    if a == b:
        return 1.0
    if not a or not b:
        return 0.0
    tokens_a, tokens_b = set(a.split()), set(b.split())
    token_sim = len(tokens_a & tokens_b) / max(len(tokens_a | tokens_b), 1)
    max_len = max(len(a), len(b))
    char_sim = 1.0 - _levenshtein(a, b) / max_len
    return 0.5 * token_sim + 0.5 * char_sim


class _UnionFind:
    def __init__(self, ids):
        self.parent = {i: i for i in ids}

    def find(self, x):
        while self.parent[x] != x:
            self.parent[x] = self.parent[self.parent[x]]
            x = self.parent[x]
        return x

    def union(self, x, y):
        self.parent[self.find(x)] = self.find(y)


class RecordLinker(BaseSkill):
    """Link records that refer to the same real-world entity.

    Never modifies source field values.
    Per-record mode: run(record, tools={"candidates": [...]}) → _linked_records
    Batch mode: link_batch(records) → records with _group_id assigned
    """

    def __init__(self, config: Optional[Dict[str, Any]] = None):
        super().__init__(config)
        self.blocking_fields: List[str] = self.config.get("blocking_fields", [])
        self.match_rules: List[Dict] = self.config.get("match_rules", [])

    def run(self, input_data: Dict[str, Any], tools: Dict[str, Any] = None) -> Dict[str, Any]:
        self.clear_audit()
        candidates = (tools or {}).get("candidates", [])
        linked = []
        for candidate in candidates:
            if candidate.get("id") == input_data.get("id"):
                continue
            match = self._apply_rules(input_data, candidate)
            if match:
                linked.append(match)
                self.log_decision(
                    f"linked {input_data.get('id')} → {candidate.get('id')} via {match['matched_rule']}",
                    f"confidence={match['confidence']:.2f}",
                    confidence=match["confidence"],
                )
        if linked:
            input_data["_linked_records"] = linked
        return input_data

    def link_batch(self, records: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
        """Full batch pass: find all matches, apply Union-Find, assign _group_id."""
        ids = [r["id"] for r in records]
        uf = _UnionFind(ids)

        blocks = self._build_blocks(records)

        for block_records in blocks.values():
            for i, rec_a in enumerate(block_records):
                for rec_b in block_records[i + 1:]:
                    match = self._apply_rules(rec_a, rec_b)
                    if match:
                        uf.union(rec_a["id"], rec_b["id"])

        for record in records:
            record["_group_id"] = uf.find(record["id"])

        return records

    def _build_blocks(self, records: List[Dict]) -> Dict[str, List[Dict]]:
        if not self.blocking_fields:
            return {"__all__": records}
        blocks: Dict[str, List[Dict]] = {}
        for record in records:
            key = tuple(str(record.get(f, "")) for f in self.blocking_fields)
            blocks.setdefault(str(key), []).append(record)
        return blocks

    def _apply_rules(self, rec_a: Dict, rec_b: Dict) -> Optional[Dict]:
        for rule in self.match_rules:
            fields = rule["fields"]
            match_type = rule["match_type"]
            weight = rule.get("weight", 1.0)
            threshold = rule.get("threshold", 1.0)

            vals_a = [str(rec_a.get(f, "")).lower().strip() for f in fields]
            vals_b = [str(rec_b.get(f, "")).lower().strip() for f in fields]

            if any(not v for v in vals_a + vals_b):
                continue  # skip rule if any required field is empty

            if match_type == "exact":
                if vals_a == vals_b:
                    return {"id": rec_b.get("id"), "matched_rule": rule["name"], "confidence": weight}

            elif match_type == "fuzzy":
                combined_a = " ".join(vals_a)
                combined_b = " ".join(vals_b)
                sim = _fuzzy_similarity(combined_a, combined_b)
                if sim >= threshold:
                    confidence = sim * weight
                    return {"id": rec_b.get("id"), "matched_rule": rule["name"], "confidence": round(confidence, 3)}

        return None
```

- [ ] **Step 5: Create `skills/_common/record_linker/skill.md`**

```markdown
# Record Linker Skill

## Purpose
Find records that refer to the same real-world entity using config-driven match rules.
Never modifies source field values — outputs linkage metadata only.
Supports transitive grouping: A→B on email, B→C on name → A,B,C same group.

## When to Use
- **DO**: Use for entity resolution / record grouping across a batch
- **DO**: Use `link_batch()` for transitive group assignment
- **DO**: Use `run()` for per-record candidate matching (requires candidates in tools)
- **DON'T**: Use for deduplication on primary key — use DB constraints for that
- **DON'T**: Use to overwrite field values — linker only annotates, never mutates

## Configuration
```yaml
record_linker:
  config:
    blocking_fields: [postal_code]     # narrow candidate pool (optional)
    match_rules:
      - name: email_exact
        fields: [email]
        match_type: exact              # exact | fuzzy
        weight: 1.0
      - name: name_company
        fields: [first_name, last_name, company]
        match_type: fuzzy
        threshold: 0.85
        weight: 0.90
```

## Output
Per-record (`run()`):
```python
{
  "_linked_records": [
    {"id": "rec_002", "matched_rule": "email_exact", "confidence": 1.0}
  ]
}
```

Batch (`link_batch()`):
```python
# Each record annotated with:
{"_group_id": "rec_001"}   # shared across all group members
```

## Match Types
- `exact`: all fields must match exactly (case-insensitive)
- `fuzzy`: combined field values scored via token + Levenshtein similarity

## Blocking
`blocking_fields` reduces O(n²) comparison to O(k²) per block.
Example: `blocking_fields: [postal_code]` compares only records in same postal code.
Omit for small batches or when no natural blocking key exists.

## Dependencies
- None (pure Python, no external services)
```

- [ ] **Step 6: Run all record linker tests**

```bash
python -m pytest tests/test_record_linker.py -v
```

Expected: all PASS

---

## Task 7: Create Domain Thin Wrappers for RecordLinker

**Files:**
- Create: `skills/real_estate/record_linker/__init__.py`
- Create: `skills/real_estate/record_linker/record_linker.py`
- Create: `skills/sports_ticketing/record_linker/__init__.py`
- Create: `skills/sports_ticketing/record_linker/record_linker.py`

- [ ] **Step 1: Create `skills/real_estate/record_linker/__init__.py`**

Empty file.

- [ ] **Step 2: Create `skills/real_estate/record_linker/record_linker.py`**

```python
from skills._common.record_linker.record_linker import RecordLinker  # noqa: F401
```

- [ ] **Step 3: Create `skills/sports_ticketing/record_linker/__init__.py`**

Empty file.

- [ ] **Step 4: Create `skills/sports_ticketing/record_linker/record_linker.py`**

```python
from skills._common.record_linker.record_linker import RecordLinker  # noqa: F401
```

---

## Task 8: Update skill.md Docs — Move to _common/, Generalize

**Files:**
- Modify: `skills/_common/spell_checker/skill.md`
- Modify: `skills/_common/address_standardizer/skill.md`

- [ ] **Step 1: Overwrite `skills/_common/spell_checker/skill.md`**

```markdown
# Spell Checker Skill

## Purpose
Fix obvious spelling mistakes in configured text fields using a general English dictionary.
Domain-specific proper nouns are handled via an override table in the DB.
Only processes fields listed in `text_fields` config — everything else is untouched.

## When to Use
- **DO**: On raw input data with entry errors or OCR mistakes in free-text fields
- **DO**: Before address standardization — typos confuse abbreviation expansion
- **DON'T**: On PII fields (names, emails, IDs) — omit them from text_fields
- **DON'T**: On already-validated canonical data
- **DON'T**: Expect it to fix domain proper nouns not in the override table

## Configuration
```yaml
spell_checker:
  config:
    text_fields: [city, description, notes]   # only these are processed
    threshold: 0.85                           # min confidence to apply correction
    domain: real_estate                       # which DB override table to load
    max_edit_distance: 2                      # symspellpy max edit distance
```

## Input / Output
```python
# Input
{"city": "toronot", "last_name": "Smyth", "notes": "near the parkk"}

# Output (last_name untouched — not in text_fields)
{"city": "toronto", "last_name": "Smyth", "notes": "near the park"}
```

Audit entries available via `skill.get_audit()` — not in the returned record.

## Correction Logic
1. Check domain override table (DB) — exact match wins at confidence=1.0
2. Check symspellpy general English dictionary — corrects if confidence ≥ threshold
3. No match → original value returned unchanged

## Dependencies
- symspellpy (bundled English dictionary, no external calls)
- DB connection optional — without it, override table is empty
```

- [ ] **Step 2: Overwrite `skills/_common/address_standardizer/skill.md`**

```markdown
# Address Standardizer Skill

## Purpose
Expand address abbreviations in configured address fields.
Works for any domain that has street address data — real estate, delivery, HR, etc.
Only processes fields listed in `address_fields` config.

## When to Use
- **DO**: After SpellChecker — typos should be fixed before abbreviation expansion
- **DO**: Before geographic validation — standardized form validates better
- **DON'T**: On non-address fields — only put address-like fields in address_fields

## Configuration
```yaml
address_standardizer:
  config:
    address_fields: [address, mailing_address]
    strip_unit_numbers: false          # remove apt/unit/# suffixes if true
```

## Transformations
- Street types: St→Street, Ave→Avenue, Blvd→Boulevard, Rd→Road, Dr→Drive, Ln→Lane, Ct→Court, Pkwy→Parkway, Ter→Terrace, Pl→Place, Sq→Square
- Quadrant directionals: NE→Northeast, NW→Northwest, SE→Southeast, SW→Southwest
- Single-letter directionals (N, E, S, W) intentionally NOT expanded — too many false positives
- Unit removal: ", Apt 123" / ", Unit 456" / ", #789" → removed (if strip_unit_numbers=true)
- Whitespace normalization

## Dependencies
- None (pure Python, deterministic rule-based)
```

---

## Task 9: Update real_estate/skills.yaml

**Files:**
- Modify: `skills/real_estate/skills.yaml`

Adds `text_fields`/`address_fields` config, moves `skill_doc` paths to `_common/`, adds `record_linker`, adds `phase` annotations, removes `fuzzy_matcher`.

- [ ] **Step 1: Replace `skills/real_estate/skills.yaml`**

```yaml
domain: real_estate
version: 1.0

config:
  spell_check_threshold: 0.85
  fuzzy_match_threshold: 0.90
  nominatim_rate_limit: 1
  web_search_timeout: 5

skills:
  spell_checker:
    class: skills._common.spell_checker.spell_checker.SpellChecker
    skill_doc: skills/_common/spell_checker/skill.md
    tools: []
    config:
      text_fields: [city, municipality, description]
      threshold: 0.85
      domain: real_estate
      pg_conn: "${runtime.pg_conn}"
    cost: low
    phase: 1
    latency_estimate_ms: 100
    depends_on: []

  address_standardizer:
    class: skills._common.address_standardizer.address_standardizer.AddressStandardizer
    skill_doc: skills/_common/address_standardizer/skill.md
    tools: []
    config:
      address_fields: [address]
      strip_unit_numbers: false
    cost: low
    phase: 1
    latency_estimate_ms: 50
    depends_on: []

  record_linker:
    class: skills._common.record_linker.record_linker.RecordLinker
    skill_doc: skills/_common/record_linker/skill.md
    tools: []
    config:
      blocking_fields: [postal_code]
      match_rules:
        - name: email_exact
          fields: [email]
          match_type: exact
          weight: 1.0
        - name: address_composite
          fields: [address, city, postal_code]
          match_type: fuzzy
          threshold: 0.90
          weight: 0.80
    cost: low
    phase: 1
    latency_estimate_ms: 150
    depends_on: []

  municipality_authority:
    class: skills.real_estate.municipality_authority.municipality_authority.MunicipalityAuthorityAgent
    skill_doc: skills/real_estate/municipality_authority/skill.md
    tools:
      - fsa_lookup
      - boundary_checker
      - confidence_scorer
    config:
      trust_postal_over_name: true
      escalate_confidence_threshold: 0.60
      pg_conn: "${runtime.pg_conn}"
    cost: high
    phase: 2
    latency_estimate_ms: 1000
    depends_on: [address_standardizer]

  geographic_validator:
    class: skills.real_estate.geographic_validator.geographic_validator.GeographicValidator
    skill_doc: skills/real_estate/geographic_validator/skill.md
    tools:
      - hierarchy_validator
      - boundary_checker
      - postal_validator
    config:
      strict_mode: false
    cost: medium
    phase: 2
    latency_estimate_ms: 500
    depends_on: [municipality_authority]

  nominatim_geocoder:
    class: skills.real_estate.nominatim_geocoder.nominatim_geocoder.NominatimGeocoderSkill
    skill_doc: skills/real_estate/nominatim_geocoder/skill.md
    tools:
      - nominatim_api
      - geocode_cache
    config:
      rate_limit: 1
      cache_ttl_days: 30
      pg_conn: "${runtime.pg_conn}"
    cost: high
    phase: 2
    latency_estimate_ms: 1500
    depends_on: [address_standardizer]

  data_quality_triage:
    class: skills.real_estate.data_quality_triage.data_quality_triage.DataQualityTriageAgent
    skill_doc: skills/real_estate/data_quality_triage/skill.md
    tools:
      - confidence_scorer
      - completeness_analyzer
      - pattern_validator
    config:
      min_confidence_auto_complete: 0.85
      min_confidence_agent_review: 0.60
    cost: medium
    phase: triage
    latency_estimate_ms: 300
    depends_on: [geographic_validator]

  web_search_enricher:
    class: skills._common.web_search_enricher.web_search_enricher.WebSearchEnricher
    skill_doc: skills/_common/web_search_enricher/skill.md
    tools:
      - tavily_search
      - query_memory
    config:
      max_queries: 3
      trigger_below: 0.70
      pg_conn: "${runtime.pg_conn}"
      web_cache: "${runtime.web_cache}"
    cost: high
    phase: 3
    latency_estimate_ms: 3000
    depends_on: [data_quality_triage]

  skill_planner:
    class: skills._common.skill_planner.skill_planner.SkillPlanner
    skill_doc: skills/_common/skill_planner/skill.md
    tools:
      - registry
    config:
      tier: "fast"
      plan_cache_ttl_hours: 24
      pg_conn: "${runtime.pg_conn}"
      llm_client: "${runtime.llm_client}"
    cost: high
    phase: 4
    latency_estimate_ms: 2000
    depends_on: [data_quality_triage]
```

- [ ] **Step 2: Verify registry loads without errors**

```bash
python -c "
from skills.registry import SkillRegistry
r = SkillRegistry.load('real_estate')
print(r)
print('Phase 1 skills:', [n for n, m in r.metadata.items() if m.get('phase') == 1])
"
```

Expected: prints registry with `spell_checker`, `address_standardizer`, `record_linker` as Phase 1.

---

## Task 10: Update sports_ticketing/skills.yaml

**Files:**
- Modify: `skills/sports_ticketing/skills.yaml`

- [ ] **Step 1: Replace `skills/sports_ticketing/skills.yaml`**

```yaml
domain: sports_ticketing
version: 0.1

config:
  spell_check_threshold: 0.85

skills:
  spell_checker:
    class: skills._common.spell_checker.spell_checker.SpellChecker
    skill_doc: skills/_common/spell_checker/skill.md
    tools: []
    config:
      text_fields: [event_description, venue_notes]
      threshold: 0.85
      domain: sports_ticketing
    cost: low
    phase: 1
    latency_estimate_ms: 100
    depends_on: []

  record_linker:
    class: skills._common.record_linker.record_linker.RecordLinker
    skill_doc: skills/_common/record_linker/skill.md
    tools: []
    config:
      blocking_fields: [event_date]
      match_rules:
        - name: venue_team_date
          fields: [venue_name, home_team, event_date]
          match_type: fuzzy
          threshold: 0.85
          weight: 0.90
    cost: low
    phase: 1
    latency_estimate_ms: 150
    depends_on: []

  event_normalizer:
    class: skills.sports_ticketing.event_normalizer.event_normalizer.EventNormalizer
    tools: []
    config:
      pg_conn: "${runtime.pg_conn}"
    cost: low
    phase: 2
    latency_estimate_ms: 50
    depends_on: []

  ticket_product_categorizer:
    class: skills.sports_ticketing.ticket_product_categorizer.ticket_product_categorizer.TicketProductCategorizer
    tools: []
    config: {}
    cost: low
    phase: 2
    latency_estimate_ms: 20
    depends_on: []
```

- [ ] **Step 2: Verify sports_ticketing loads**

```bash
python -c "
from skills.registry import SkillRegistry
r = SkillRegistry.load('sports_ticketing')
print(r)
"
```

Expected: prints registry with spell_checker and record_linker.

---

## Task 11: Update Orchestrator — Parallel Phase 1 + Audit Separation

**Files:**
- Modify: `cleaning/orchestrator_v2.py`

Phase 1 skills (phase==1) run in parallel via `ThreadPoolExecutor`. Audit entries extracted from skills, never in the record. `_decisions` stripped from records. Batch method runs `record_linker.link_batch()` after per-record Phase 1.

- [ ] **Step 1: Write the failing tests**

Add to `tests/test_full_agent_pipeline.py`:

```python
def test_phase1_audit_not_in_record():
    """After process_record, _decisions and _agent_decisions must not be in record."""
    registry = SkillRegistry.load("real_estate")
    team = OrchestrationTeam(registry)
    record = {"id": "r1", "city": "toronot", "address": "123 Main St"}
    result, audit = team.process_record(record)
    assert "_decisions" not in result
    assert "_agent_decisions" not in result
    assert isinstance(audit, list)


def test_process_record_returns_tuple():
    """process_record must return (record_dict, audit_list)."""
    registry = SkillRegistry.load("real_estate")
    team = OrchestrationTeam(registry)
    result = team.process_record({"id": "r1"})
    assert isinstance(result, tuple)
    assert len(result) == 2
```

- [ ] **Step 2: Run to verify they fail**

```bash
python -m pytest tests/test_full_agent_pipeline.py::test_phase1_audit_not_in_record tests/test_full_agent_pipeline.py::test_process_record_returns_tuple -v
```

Expected: FAIL — `process_record` currently returns a dict, not a tuple.

- [ ] **Step 3: Rewrite `cleaning/orchestrator_v2.py`**

```python
"""Orchestrator v2: Agent team + skill registry based cleaning pipeline."""

import logging
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from typing import Any, Dict, List, Optional, Tuple
from dataclasses import dataclass, field

from skills.registry import SkillRegistry
from skills.agent import BaseAgent


class BatchBudget:
    """Per-batch query budget for expensive operations (Tavily, LLM calls)."""

    def __init__(self, max_queries: int = 100):
        self.max_queries = max_queries
        self.remaining = max_queries
        self.spent = 0

    def take(self, n: int = 1) -> bool:
        if self.remaining < n:
            return False
        self.remaining -= n
        self.spent += n
        return True

    def summary(self) -> str:
        return f"Budget: {self.spent}/{self.max_queries} used, {self.remaining} remaining"


@dataclass
class CleaningRunReport:
    records_processed: int
    cleaned_count: int
    flagged_count: int
    flags_by_type: Dict
    cache_stats: Dict
    timing: Dict
    flag_summary: list
    errors: list
    summary_text: str
    audit_log: List[Dict] = field(default_factory=list)


logger = logging.getLogger(__name__)


class OrchestrationTeam:
    """Multi-phase cleaning pipeline: parallel deterministic → triage → domain skills → web search → (last resort) LLM planner."""

    def __init__(self, registry: SkillRegistry, batch_budget: Optional[BatchBudget] = None):
        self.registry = registry
        self.batch_budget = batch_budget
        self.planner = registry.get("skill_planner")
        self.triage_skill = registry.get("data_quality_triage")

    def _run_skill(self, skill, record: dict, tools: dict = None) -> Tuple[dict, List]:
        """Run a skill, collect audit, strip _decisions from record."""
        skill.clear_audit()
        result = skill.run(dict(record), tools or {})
        result.pop("_decisions", None)   # backward compat strip during transition
        return result, skill.get_audit()

    def _phase1_skills(self) -> List:
        """Return skills with phase==1 in dependency order."""
        return [
            self.registry.get(name)
            for name, meta in self.registry.metadata.items()
            if meta.get("phase") == 1 and self.registry.get(name)
        ]

    def process_record(self, record: Dict[str, Any]) -> Tuple[Dict[str, Any], List]:
        """Process a single record through the pipeline.

        Returns (cleaned_record, audit_entries).
        Audit entries are never placed into the record.
        """
        audit_log = []

        # Phase 1: Deterministic skills (phase=1) — run in parallel
        phase1 = self._phase1_skills()
        if phase1:
            merged = dict(record)
            with ThreadPoolExecutor(max_workers=len(phase1)) as executor:
                futures = {
                    executor.submit(self._run_skill, skill, record): skill
                    for skill in phase1
                    # record_linker run() needs candidates — skip in per-record mode
                    if not hasattr(skill, "link_batch")
                       or skill.__class__.__name__ != "RecordLinker"
                }
                for future in as_completed(futures):
                    result, entries = future.result()
                    audit_log.extend(entries)
                    merged.update(result)
            record = merged

        # Phase 2: Initial triage
        if self.triage_skill:
            record, entries = self._run_skill(self.triage_skill, record)
            audit_log.extend(entries)

        route = record.get("_triage_route")
        if route in ("done", "unsalvageable"):
            return record, audit_log

        # Phase 3: Domain skills (phase=2, in dependency order)
        phase2_names = [
            name for name, meta in self.registry.metadata.items()
            if meta.get("phase") == 2
        ]
        for skill_name in self.registry.topological_sort(phase2_names):
            skill = self.registry.get(skill_name)
            if not skill:
                continue
            meta = self.registry.get_metadata(skill_name) or {}
            if meta.get("cost") == "high" and self.batch_budget:
                if not self.batch_budget.take():
                    audit_log.append({
                        "skill": "OrchestrationTeam",
                        "decision": f"Skipped {skill_name} — budget exhausted",
                        "reason": self.batch_budget.summary(),
                        "confidence": 0.0,
                    })
                    continue
            tools = {"batch_budget": self.batch_budget} if self.batch_budget else {}
            record, entries = self._run_skill(skill, record, tools)
            audit_log.extend(entries)

        # Phase 4: Web search enrichment (phase=3)
        web_enricher = self.registry.get("web_search_enricher")
        if web_enricher and record.get("_triage_route") == "needs_review":
            tools = {}
            if self.batch_budget:
                tools["batch_budget"] = self.batch_budget
            record, entries = self._run_skill(web_enricher, record, tools)
            audit_log.extend(entries)

        # Re-triage with enriched evidence
        if self.triage_skill:
            record, entries = self._run_skill(self.triage_skill, record)
            audit_log.extend(entries)

        route = record.get("_triage_route")
        if route in ("done", "unsalvageable"):
            return record, audit_log

        # Phase 5: LLM Planner — LAST RESORT only
        if self.planner:
            record, entries = self._run_skill(
                self.planner, record, tools={"registry": self.registry}
            )
            audit_log.extend(entries)
            planned_skills = record.get("_planned_skills", [])
            skip = {"data_quality_triage", "skill_planner"}
            for skill_name in planned_skills:
                if skill_name in skip:
                    continue
                skill = self.registry.get(skill_name)
                if skill:
                    record, entries = self._run_skill(skill, record)
                    audit_log.extend(entries)

        # Final triage
        if self.triage_skill:
            record, entries = self._run_skill(self.triage_skill, record)
            audit_log.extend(entries)

        return record, audit_log

    def process_batch(self, records: List[Dict[str, Any]]) -> Tuple[List[Dict], List]:
        """Process a batch. Runs record_linker.link_batch() after Phase 1 per-record pass."""
        all_audit = []
        processed = []

        for record in records:
            cleaned, audit = self.process_record(record)
            processed.append(cleaned)
            all_audit.extend(audit)

        # Batch record linkage — transitive group assignment across all records
        record_linker = self.registry.get("record_linker")
        if record_linker and hasattr(record_linker, "link_batch"):
            processed = record_linker.link_batch(processed)

        return processed, all_audit


def run_cleaning_workflow_v2(
    records: list,
    verbose: bool = False,
    domain: str = "real_estate",
) -> CleaningRunReport:
    timing: Dict[str, float] = {}

    try:
        t = time.time()
        registry = SkillRegistry.load(domain)
        timing["skill_registry_load"] = time.time() - t

        if verbose:
            print(f"Loaded skill registry: {registry}")

        t = time.time()
        team = OrchestrationTeam(registry)
        timing["agent_team_init"] = time.time() - t

        if not records:
            return _empty_report(timing, "No records to process.")

        t = time.time()
        processed_records, audit_log = team.process_batch(records)
        timing["agent_team_processing"] = time.time() - t

        if verbose:
            for i, r in enumerate(processed_records):
                print(f"  [{i+1}/{len(records)}] id={r.get('id')} route={r.get('_triage_route')}")

        summary_text = (
            f"Cleaned {len(processed_records)}/{len(records)} records. "
            f"{len(audit_log)} audit entries. "
            f"Total: {sum(timing.values()):.2f}s."
        )

        return CleaningRunReport(
            records_processed=len(records),
            cleaned_count=len(processed_records),
            flagged_count=0,
            flags_by_type={},
            cache_stats={"hits": 0, "misses": 0, "pg_hits": 0, "queries_cached": 0},
            timing=timing,
            flag_summary=[],
            errors=[],
            summary_text=summary_text,
            audit_log=audit_log,
        )

    except Exception as e:
        logger.error(f"Error in orchestration: {e}")
        return _empty_report(timing, f"Error: {str(e)}")


def _empty_report(timing: dict, message: str) -> CleaningRunReport:
    return CleaningRunReport(
        records_processed=0,
        cleaned_count=0,
        flagged_count=0,
        flags_by_type={},
        cache_stats={"hits": 0, "misses": 0, "pg_hits": 0, "queries_cached": 0},
        timing=timing,
        flag_summary=[],
        errors=[],
        summary_text=message,
    )
```

- [ ] **Step 4: Run new orchestrator tests**

```bash
python -m pytest tests/test_full_agent_pipeline.py::test_phase1_audit_not_in_record tests/test_full_agent_pipeline.py::test_process_record_returns_tuple -v
```

Expected: PASS

---

## Task 12: Update Existing Tests

**Files:**
- Modify: `tests/test_full_agent_pipeline.py`
- Modify: `tests/test_skill_registry.py`

Tests that called `team.process_record(record)` and got a dict now get a tuple `(record, audit)`. Tests checking `_decisions`/`_agent_decisions` in the record need to check `audit` instead.

- [ ] **Step 1: Run the full test suite to see all failures**

```bash
python -m pytest tests/ -v --tb=line 2>&1 | grep FAILED
```

Note every failing test name. Fix each one below.

- [ ] **Step 2: Fix `test_decisions_log_isolated_per_record` in `test_full_agent_pipeline.py`**

Old test checked `result.get("_decisions", [])`. New pattern: check `skill.get_audit()`.

```python
def test_decisions_log_isolated_per_record():
    """Each run() must only accumulate its own audit, not prior calls'."""
    with patch("cleaning.spell_corrections_data.get_corrections_dict", return_value=_REAL_ESTATE_CORRECTIONS):
        registry_with_conn = SkillRegistry.load("real_estate", runtime={"pg_conn": MagicMock()})
    spell = registry_with_conn.get("spell_checker")

    spell.clear_audit()
    spell.run({"city": "scarbbrough"}, {})
    audit1 = spell.get_audit()

    spell.clear_audit()
    spell.run({"city": "toronot"}, {})
    audit2 = spell.get_audit()

    assert not any("scarbbrough" in e.get("decision", "") for e in audit2), (
        "rec1 audit leaked into rec2"
    )
    assert any("toronot" in e.get("decision", "") for e in audit2), (
        "rec2 missing its own correction"
    )
```

- [ ] **Step 3: Fix any test in `test_full_agent_pipeline.py` that unpacks `process_record` result**

Search for `team.process_record` calls and update to unpack tuple:
```python
# Old
result = team.process_record(record)

# New
result, audit = team.process_record(record)
```

- [ ] **Step 4: Fix `test_geographic_validator_valid_postal` and similar tests that check `_decisions` in record**

These are skill-level tests (`validator.run(record)`) — skills still produce `_decisions` until they're updated individually. Check if the test passes by verifying the skill still works, and note that `_decisions` removal from validator/triage/geocoder skills is in Task 13.

```python
def test_geographic_validator_valid_postal():
    registry = SkillRegistry.load("real_estate")
    validator = registry.get("geographic_validator")
    record = {
        "country": "CA",
        "postal_code": "M9L 1H7",
        "state_province": "ON",
        "municipality": "North York",
    }
    result = validator.run(record)
    assert result.get("_geographic_validated") == True
    # audit via get_audit() once Task 13 is done; _decisions still present for now
```

- [ ] **Step 5: Fix `test_address_standardizer_skill` in `test_skill_registry.py`**

Registry now has `address_fields: [address]` config. The test record must have an `"address"` field. Existing test already does — verify it passes.

- [ ] **Step 6: Update the `test_spell_checker_with_injected_corrections` test in `test_skill_registry.py`**

```python
def test_spell_checker_with_injected_corrections():
    with patch("cleaning.spell_corrections_data.get_corrections_dict", return_value={"scarbbrough": "scarborough"}):
        spell_checker = SpellChecker({
            "pg_conn": MagicMock(),
            "threshold": 0.85,
            "text_fields": ["municipality"],
        })
    result = spell_checker.run({"municipality": "scarbbrough", "address": "123 Main St"})
    assert result["municipality"] == "scarborough"
    assert result["address"] == "123 Main St"   # not in text_fields
    assert "_decisions" not in result
    assert len(spell_checker.get_audit()) == 1
```

- [ ] **Step 7: Update the import in `test_skill_registry.py` that imports from `skills.real_estate.spell_checker`**

```python
# Old
from skills.real_estate.spell_checker.spell_checker import SpellChecker
# New (thin wrapper still works, but use _common directly for clarity)
from skills._common.spell_checker.spell_checker import SpellChecker
```

- [ ] **Step 8: Update imports in `test_full_agent_pipeline.py`**

```python
# Old
from skills.real_estate.address_standardizer.address_standardizer import AddressStandardizer
from skills.real_estate.fuzzy_matcher.fuzzy_matcher import FuzzyMatcher
# New
from skills._common.address_standardizer.address_standardizer import AddressStandardizer
from skills._common.record_linker.record_linker import RecordLinker
```

- [ ] **Step 9: Replace any FuzzyMatcher test cases in `test_full_agent_pipeline.py` with RecordLinker equivalents**

```python
# Old — test fuzzy address matching
def test_fuzzy_matches_st_to_street():
    ...

# New — test record linker composite matching
def test_record_linker_address_composite():
    linker = RecordLinker({
        "blocking_fields": [],
        "match_rules": [{
            "name": "address_composite",
            "fields": ["address", "city"],
            "match_type": "fuzzy",
            "threshold": 0.85,
            "weight": 0.9,
        }]
    })
    record = {"id": "A", "address": "123 Main St", "city": "toronto"}
    candidates = [{"id": "B", "address": "123 Main Street", "city": "toronto"}]
    result = linker.run(record, tools={"candidates": candidates})
    assert len(result.get("_linked_records", [])) == 1
```

- [ ] **Step 10: Run full test suite**

```bash
python -m pytest tests/ -v --tb=short 2>&1 | tail -50
```

Expected: all PASS (or only pre-existing failures unrelated to this refactor).

---

## Task 13: Remove _decisions from Existing Domain Skills

**Files:**
- Modify: `skills/real_estate/geographic_validator/geographic_validator.py`
- Modify: `skills/real_estate/municipality_authority/municipality_authority.py`
- Modify: `skills/real_estate/nominatim_geocoder/nominatim_geocoder.py`
- Modify: `skills/real_estate/data_quality_triage/data_quality_triage.py`

For each file: remove `input_data["_decisions"] = decisions` (or similar). `log_decision()` calls are fine — they now accumulate on the instance. Orchestrator extracts via `get_audit()`.

- [ ] **Step 1: Read each file and locate `_decisions` assignment**

```bash
grep -n "_decisions" \
  skills/real_estate/geographic_validator/geographic_validator.py \
  skills/real_estate/municipality_authority/municipality_authority.py \
  skills/real_estate/nominatim_geocoder/nominatim_geocoder.py \
  skills/real_estate/data_quality_triage/data_quality_triage.py
```

- [ ] **Step 2: For each file — remove `input_data["_decisions"] = decisions` line and any associated local `decisions = []` list if it's only used for that assignment**

Pattern to remove in each skill:
```python
# REMOVE these lines:
decisions = []
...
decisions.append(self.log_decision(...))
...
if decisions:
    input_data["_decisions"] = decisions
```

`self.log_decision()` still accumulates to `self._audit_entries`. The local list is no longer needed.

- [ ] **Step 3: Run full test suite**

```bash
python -m pytest tests/ -v --tb=short 2>&1 | tail -30
```

Expected: all PASS — `_decisions` removal from domain skills should not break tests after Task 12 updates.

---

## Task 14: Delete Obsolete fuzzy_matcher Files

**Files:**
- Delete: `skills/_common/fuzzy_matcher/fuzzy_matcher.py`
- Delete: `skills/_common/fuzzy_matcher/__init__.py`
- Delete: `skills/real_estate/fuzzy_matcher/fuzzy_matcher.py`
- Delete: `skills/real_estate/fuzzy_matcher/skill.md`

- [ ] **Step 1: Verify nothing imports fuzzy_matcher**

```bash
grep -rn "fuzzy_matcher" /mnt/f/AI_learning_project/skills/ /mnt/f/AI_learning_project/tests/ /mnt/f/AI_learning_project/cleaning/ \
  --include="*.py" --include="*.yaml" --include="*.md" \
  | grep -v "__pycache__" | grep -v "record_linker"
```

Expected: no output (no remaining references).

- [ ] **Step 2: Delete files**

```bash
rm skills/_common/fuzzy_matcher/fuzzy_matcher.py
rm skills/_common/fuzzy_matcher/__init__.py
rm skills/real_estate/fuzzy_matcher/fuzzy_matcher.py
rm skills/real_estate/fuzzy_matcher/skill.md
rmdir skills/_common/fuzzy_matcher 2>/dev/null || true
rmdir skills/real_estate/fuzzy_matcher 2>/dev/null || true
```

- [ ] **Step 3: Run full test suite one final time**

```bash
python -m pytest tests/ -v 2>&1 | tail -30
```

Expected: all PASS. No import errors from deleted files.

---

## Final Verification

- [ ] **Registry loads both domains**

```bash
python -c "
from skills.registry import SkillRegistry
re = SkillRegistry.load('real_estate')
st = SkillRegistry.load('sports_ticketing')
print('real_estate:', re.list_skills())
print('sports_ticketing:', st.list_skills())
"
```

- [ ] **Phase 1 parallel smoke test**

```bash
python -c "
from skills.registry import SkillRegistry
from cleaning.orchestrator_v2 import OrchestrationTeam
registry = SkillRegistry.load('real_estate')
team = OrchestrationTeam(registry)
record = {'id': 'test1', 'city': 'toronot', 'address': '123 Main St', 'postal_code': 'M9L 1H7'}
result, audit = team.process_record(record)
print('city:', result['city'])          # expected: toronto (symspellpy)
print('address:', result['address'])    # expected: 123 Main Street
print('audit entries:', len(audit))
print('_decisions in record:', '_decisions' in result)  # expected: False
"
```

- [ ] **Record linker batch grouping smoke test**

```bash
python -c "
from skills._common.record_linker.record_linker import RecordLinker
linker = RecordLinker({
    'blocking_fields': [],
    'match_rules': [{'name': 'email', 'fields': ['email'], 'match_type': 'exact', 'weight': 1.0}]
})
records = [
    {'id': 'A', 'email': 'x@x.com'},
    {'id': 'B', 'email': 'x@x.com'},
    {'id': 'C', 'email': 'y@y.com'},
]
result = linker.link_batch(records)
for r in result:
    print(r['id'], r['_group_id'])
# Expected: A and B share group_id, C different
"
```

- [ ] **Stage all changes for user review**

```bash
git add -A
git status
```

Do NOT commit. User reviews staged changes and commits or asks questions.
