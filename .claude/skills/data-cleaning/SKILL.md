---
name: data-cleaning
description: Use when building data cleaning pipelines, fuzzy matching logic, entity resolution, or field normalization for any industry. Use when designing confidence-scored cleaning steps, triage routing, or extracting common patterns across industry-specific cleaning skills. Use when encountering messy real-world data with typos, abbreviations, inconsistent formats, or duplicate records.
---

# Data Cleaning Patterns

## Overview

Deterministic rules handle known transformations (abbreviation expansion, format normalization). Reasoning handles ambiguous cases (typos, partial matches, conflicting sources). Pipelines combine both with confidence scoring to route records automatically.

The reference implementation is `/mnt/f/AI_learning_project/skills/real_estate/`.

## Core Pipeline Architecture

```
Input Record (dict)
       ↓
  SkillRegistry   ← skills.yaml (defines sequence, dependencies, config)
       ↓
  BaseAgent       ← executes skills in order, collects _decisions
       ↓
  Skill 1 (e.g. canonicalize)
  Skill 2 (e.g. fuzzy_match)
  Skill 3 (e.g. validate)
  Skill N (triage gate)
       ↓
Output Record + _decisions + _triage_route
```

**Record contract** — every skill receives and returns same dict shape:
```python
# Fields it processes (domain-specific)
{"address": str, "city": str, ...}

# Fields it appends (universal)
"_decisions": [{"skill": str, "decision": str, "reason": str, "confidence": float}]
"_<skill>_confidence": float    # domain metadata
"_triage_route": "done" | "needs_review" | "unsalvageable"
```

## Fuzzy Matching Strategy

**Two-stage scoring (production pattern from real_estate/fuzzy_matcher):**

```python
def score(a: str, b: str) -> float:
    a, b = canonicalize(a), canonicalize(b)   # ALWAYS normalize first
    token = token_sort_ratio(a, b) / 100       # word-order-invariant
    char  = partial_ratio(a, b) / 100          # substring tolerance
    return 0.5 * token + 0.5 * char            # equal weight default
```

**Canonicalize before ANY comparison:**
```python
def canonicalize(text: str) -> str:
    text = text.lower().strip()
    text = re.sub(r'[^\w\s]', '', text)        # remove punctuation
    for abbr, full in DOMAIN_ABBREVS.items():   # domain-specific expansion
        text = re.sub(rf'\b{abbr}\b', full, text)
    return re.sub(r'\s+', ' ', text)
```

**When to use which scorer:**

| Scenario | Approach |
|---|---|
| Same field, different format | Deterministic canonicalize only |
| Typos / OCR errors | `ratio` (character edit distance) |
| Word-order variants | `token_sort_ratio` |
| Partial / abbreviated values | `partial_ratio` |
| Cross-record deduplication | Two-stage + threshold filter |
| Ambiguous / low confidence | LLM reasoning with structured output |

**Threshold guidelines:**
- `>= 0.90` → auto-accept match
- `0.70–0.89` → flag for review
- `< 0.70` → reject / escalate

## Confidence Scoring Design

**Assign confidence per decision, not per record:**
```python
self.log_decision(record, decision="Expanded St → Street", reason="abbrev map", confidence=1.0)
self.log_decision(record, decision="Corrected 'Toroto' → 'Toronto'", reason="fuzzy 0.91", confidence=0.91)
```

**Triage aggregation — use weakest-link, not average:**
```python
# Average masks a single bad signal; weakest-link surfaces it
data_confidence = min(
    municipality_confidence,
    geographic_confidence,
    spell_check_confidence,
)
```

**Routing thresholds (calibrate per domain):**
```python
AUTO_COMPLETE  = 0.85   # high confidence → done
AGENT_REVIEW   = 0.60   # medium → human review queue
# below → unsalvageable
```

**Completeness scoring:**
```python
completeness = len([f for f in REQUIRED_FIELDS if record.get(f)]) / len(REQUIRED_FIELDS)
final = min(completeness, data_confidence)   # missing fields cap the score
```

## Building a New Industry Skill

**Step 1 — Map field types to cleaning strategies:**

| Field Type | Deterministic First | Fuzzy When |
|---|---|---|
| Address / location | Abbreviation expansion, format normalization | Typos, partial matches |
| Name / entity | Case normalization, punctuation strip | Nickname variants, OCR errors |
| Identifier (postal, tax ID) | Regex format validation | Partial / transposed digits |
| Category / enum | Canonical mapping dict | Free-text entries |
| Date / time | Format parsing (dateutil) | Ambiguous formats via LLM |
| Currency / numeric | Strip symbols, normalize decimals | None — always deterministic |

**Step 2 — Define `skills.yaml`:**
```yaml
skills:
  - name: canonicalizer
    class: myindustry.skills.Canonicalizer
    doc: skills/canonicalizer/skill.md
    config:
      abbreviations: {Dr: Drive, Ave: Avenue}
    metadata:
      cost: low
      latency_estimate_ms: 5

  - name: fuzzy_matcher
    class: myindustry.skills.FuzzyMatcher
    depends_on: [canonicalizer]
    metadata:
      cost: low
      latency_estimate_ms: 20

  - name: validator
    class: myindustry.skills.Validator
    depends_on: [fuzzy_matcher]
    metadata:
      cost: low
      latency_estimate_ms: 10

  - name: triage
    class: myindustry.skills.Triage
    depends_on: [validator]
    config:
      auto_complete_threshold: 0.85
      agent_review_threshold: 0.60
    metadata:
      cost: low
      latency_estimate_ms: 5
```

**Step 3 — Subclass BaseSkill:**
```python
from skills.base import BaseSkill

class MySkill(BaseSkill):
    def run(self, record: dict, tools: dict) -> dict:
        # 1. Extract field
        # 2. Apply deterministic rules first
        # 3. Apply fuzzy/LLM for ambiguous cases
        # 4. Log every decision with confidence
        # 5. Return modified record
        return record
```

## Cross-Industry Pattern Abstraction Protocol

When multiple industry skills share the same logic, abstract it. Run this check when adding a second industry skill:

```
1. List all deterministic transforms in new skill
2. For each: does real_estate (or other existing skill) do same thing?
   YES → move to BaseSkill or shared utility in skills/common/
   NO  → keep in industry skill
3. For abbreviation maps: industry-specific dict stays in YAML config
   The canonicalize() function itself lives in base
4. For confidence thresholds: keep in industry YAML config
   The aggregation logic (weakest-link) lives in base
5. Document abstraction in industry-patterns.md
```

**Signals that something belongs in base vs industry:**

| Belongs in Base | Belongs in Industry Skill |
|---|---|
| Canonicalize algorithm | Domain abbreviation dictionary |
| Two-stage fuzzy scorer | Field names being compared |
| Weakest-link aggregation | Confidence threshold values |
| Decision log structure | Authority lookup tables |
| Triage routing logic | Required fields list |
| YAML registry loader | Skills sequence and dependencies |

## LLM Reasoning Integration

Use LLM when deterministic confidence < threshold:
```python
if match_confidence < 0.75:
    result = llm.complete(
        f"Canonical form of '{raw_value}' given context {context}. "
        f"Return JSON: {{value: str, confidence: float, reason: str}}"
    )
    self.log_decision(record, result["value"], result["reason"], result["confidence"])
```

**Always ask LLM for structured output** — parse confidence from response, don't infer it.

**Cost control:** Gate LLM calls behind deterministic pre-filter. If regex/dict handles 80% of cases, LLM only runs on the 20% remainder.

## Common Mistakes

| Mistake | Fix |
|---|---|
| Compare before canonicalize | Always canonicalize both sides first |
| Average confidence scores | Use weakest-link for triage decisions |
| LLM for all cases | Deterministic first; LLM for residual ambiguity |
| Hard-code field names in base | Field names in industry YAML config only |
| Skip `depends_on` in YAML | Skills that read another skill's output must declare dependency |
| One skill doing too much | Each skill: one responsibility, one field group |

## Quick Reference — Real Estate Skill Map

```
SpellChecker        → typo correction in address/city fields (fuzzy dict lookup)
AddressStandardizer → abbreviation expansion + whitespace normalize
FuzzyMatcher        → cross-record deduplication + variant matching
MunicipalityAuthority → resolve municipality from postal code (FSA lookup)
GeographicValidator → format + hierarchy validation (postal, province, country)
DataQualityTriage   → final gate: done / needs_review / unsalvageable
```

See `industry-patterns.md` in this directory for cross-industry pattern catalog.
