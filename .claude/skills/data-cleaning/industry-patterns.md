# Industry Pattern Catalog

Cross-industry abstractions derived from real_estate skill. Update this file when a new industry skill is built.

## Abstracted to Base (skills/base.py or skills/common/)

| Pattern | Location | Derived From |
|---|---|---|
| `canonicalize(text)` | BaseSkill method | real_estate/fuzzy_matcher |
| Two-stage fuzzy score (token + char) | FuzzyMatcher tool | real_estate/fuzzy_matcher |
| Weakest-link confidence aggregation | Triage base logic | real_estate/data_quality_triage |
| `_decisions` log structure | BaseSkill.log_decision | real_estate (all skills) |
| YAML registry loader | SkillRegistry | real_estate/skills.yaml |
| Triage routing (done/needs_review/unsalvageable) | Triage base | real_estate/data_quality_triage |

## Industry Skills Catalog

### Real Estate (`skills/real_estate/`)

**Domain fields:** address, city, postal_code, municipality, country, state_province

**Industry-specific components:**
- `SpellChecker` — address/city typo correction via fuzzy dict
- `AddressStandardizer` — street abbreviation expansion (St→Street, NE→Northeast)
- `MunicipalityAuthority` — FSA (first 3 postal chars) → municipality lookup; 95 Toronto M-series codes
- `GeographicValidator` — postal format + province code + hierarchy validation
- Confidence thresholds: auto_complete=0.85, agent_review=0.60

**Abbreviation dict lives in:** `skills.yaml` config block per skill

---

## Pattern Templates for New Industries

### Healthcare

**Likely field types:** patient name, DOB, MRN, ICD code, facility name, address

| Field | Deterministic | Fuzzy/LLM |
|---|---|---|
| Patient name | Lowercase, strip punctuation | Nickname variants (Bill↔William), typos |
| MRN / ID | Regex format check | Transposed digits → flag, not auto-correct |
| ICD code | Lookup table validation | None — hard error if invalid |
| Facility name | Canonical name map | Abbreviations, common aliases |
| Address | Same as real_estate | Same as real_estate |

**Key difference from real estate:** IDs (MRN, SSN) should never be auto-corrected — flag for human review.

---

### Financial Services

**Likely field types:** company name, ticker, ISIN, address, country, SIC code

| Field | Deterministic | Fuzzy/LLM |
|---|---|---|
| Company name | Strip legal suffix (Inc, Ltd, LLC) | Alias matching, subsidiary names |
| Ticker | Uppercase, exchange prefix | None — deterministic or reject |
| ISIN | Format validation (2-char + 9-char + check digit) | None — validate or reject |
| Country | ISO 3166 normalization | Common variants (USA→US, England→GB) |

**Key difference:** Legal entity resolution requires authority source (OpenCorporates, LEI registry) — not fuzzy.

---

### E-commerce / Logistics

**Likely field types:** product name, SKU, seller name, shipping address, country

| Field | Deterministic | Fuzzy/LLM |
|---|---|---|
| Product name | Lowercase, strip HTML, normalize units | Brand/model variant matching |
| SKU | Format normalization, prefix strip | None — source of truth |
| Seller name | Canonical seller map | DBA names, rebrands |
| Address | Same as real_estate | Same as real_estate |

---

### HR / People Data

**Likely field types:** full name, email, job title, department, office location

| Field | Deterministic | Fuzzy/LLM |
|---|---|---|
| Full name | Trim, title case, strip honorifics | Initials vs full name, international ordering |
| Email | Lowercase, validate format | None — accept or reject |
| Job title | Canonical title map | "Sr Eng" ↔ "Senior Engineer" |
| Department | Canonical dept map | Historical names, restructuring aliases |

---

## Abstraction Decision Log

When you add a new industry skill, record your abstraction decisions here:

| Date | Industry | Pattern Found | Decision | Reason |
|---|---|---|---|---|
| 2026-05-04 | Real Estate | Two-stage fuzzy score | Abstracted to base FuzzyMatcher tool | Identical logic applies to any text field comparison |
| 2026-05-04 | Real Estate | Weakest-link triage | Abstracted to base Triage | Any pipeline with multiple confidence signals needs this |
| 2026-05-04 | Real Estate | Canonicalize-before-compare | Abstracted to BaseSkill | Universal — must run before any string comparison |

---

## Signals a Pattern Is General Enough to Abstract

- Used identically in 2+ industry skills (no domain-specific parameters)
- The only variation is config values (thresholds, dicts) — not logic
- Removing it from industry skill and adding to base requires zero behavior change
- Other teams would naturally re-implement it for their domain
