# Fuzzy Matcher Skill

## Purpose
Compare two address strings that may be written differently and score their similarity.
Canonicalizes abbreviations before scoring so variants like "123 Main st" and "123 main street",
or "st Catherine" and "saint catherine", score as near-identical.
Enables cross-record deduplication and record linking without requiring prior standardization.

## When to Use
- **DO**: Call `compare(text1, text2)` to get a similarity score between any two address strings
- **DO**: Pass `candidates` list via `tools` to `run()` for cross-record matching in batch pipelines
- **DO**: Use on raw or lightly-cleaned data — `_canonicalize()` handles common abbreviation variants
- **DON'T**: Use `match()` when `compare()` suffices — `match()` is for legacy exact/fuzzy decisions only
- **DON'T**: Expect perfect results on heavily misspelled data (canonicalization is rule-based, not ML)

## Public API

### `compare(text1, text2) -> float`
Main entry point. Canonicalizes both strings, then computes similarity.
```python
fm = FuzzyMatcher({"threshold": 0.85})
sim = fm.compare("123 Main st", "123 main street")   # → 1.0
sim = fm.compare("st Catherine", "saint catherine")  # → 1.0
```

### `run(input_data, tools=None) -> dict`
Cross-record matching. Compares `input_data["address"]` against a list of candidate records.
Candidates are passed via `tools["candidates"]` — a list of dicts with `"address"` and `"id"` keys.
Matches above threshold are appended to `input_data["_address_match_candidates"]`.
```python
tools = {"candidates": [{"id": 2, "address": "123 main street"}]}
result = fm.run({"address": "123 Main st"}, tools)
# result["_address_match_candidates"] → [{"id": 2, "address": "123 main street", "similarity": 1.0}]
```

### `match(text1, text2) -> (float, dict)`
Legacy API. Returns (similarity_score, decision_log). Use `compare()` instead for scoring.

## Input / Output

### `run()` Input
```python
{
  "address": str,           # Address to match against candidates
}
```
tools dict:
```python
{
  "candidates": [           # List of records to compare against
    {"id": any, "address": str},
    ...
  ]
}
```

### `run()` Output
```python
{
  "address": str,
  "_address_match_candidates": [   # Only present when matches found
    {"id": any, "address": str, "similarity": float},
    ...
  ],
  "_decisions": [...],             # Decision log entries for each match
}
```

## Canonicalization
`_canonicalize()` applies before any comparison:
1. Lowercase + strip
2. Remove punctuation (commas, periods → spaces)
3. Collapse whitespace
4. Expand tokens: `st` → `street`, `saint` → `street`, `ave` → `avenue`, `blvd` → `boulevard`, etc.

## Matching Algorithm
Two-stage scoring (token-based + character-based) on canonicalized strings:

1. **Token Matching** (word-level, 50% weight)
   - Jaccard similarity of token sets

2. **Character Matching** (Levenshtein, 50% weight)
   - Normalized Levenshtein distance

Combined score = (0.5 × token_sim) + (0.5 × char_sim)

## Configuration
```yaml
fuzzy_matcher:
  threshold: 0.90           # Match threshold (0.0-1.0)
  token_weight: 0.5         # Weight for token vs character matching
```

## Constraints
- Threshold 0.90 by default (high bar for matches)
- Canonicalization is rule-based — does not handle arbitrary misspellings
- `saint` and `st` both normalize to `street` for comparison purposes

## Dependencies
- None (standalone, pure Python)

## Complements
- **With**: DataQualityTriage (similarity scores inform triage confidence)
- **After**: AddressStandardizer (optional — canonicalization in this skill handles common cases)
