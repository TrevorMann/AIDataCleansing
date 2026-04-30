# Fuzzy Matcher Skill

## Purpose
Detect address variants and similarities using fuzzy matching.
Identifies when two addresses are likely the same (e.g., "25 Muir Ave" vs "25 Muir Avenue").
Enables deduplication and record linking.

## When to Use
- **DO**: After standardization (standardized forms match better)
- **DO**: For deduplication (find duplicate records)
- **DO**: For record linking (match variants across systems)
- **DON'T**: Before standardization (abbreviations confuse matching)
- **DON'T**: On raw, unclean data (typos lower scores)

## Input
```python
{
  "address": str,  # Standardized address
}
```

## Output
```python
{
  "address": str,
  "_address_fuzzy_confidence": float,  # Similarity score 0.0-1.0
}
```

## Examples

### Example 1: Exact Match
```
Input:  match("25 Muir Avenue", "25 Muir Avenue")
Output: (confidence: 1.0, "Exact match")
```

### Example 2: Variant Match
```
Input:  match("25 Muir Ave", "25 Muir Avenue")
Output: (confidence: 0.64, "Fuzzy match: similarity 0.64")
```

### Example 3: Different Addresses
```
Input:  match("123 Main St", "456 Queen Ave")
Output: (confidence: 0.15, "No match: similarity below threshold")
```

## Matching Algorithm
Two-stage scoring (token-based + character-based):

1. **Token Matching** (word-level, 50% weight)
   - Split into words, compare sets
   - "25 Muir Avenue" → {"25", "muir", "avenue"}
   - Jaccard similarity of token sets

2. **Character Matching** (Levenshtein, 50% weight)
   - Normalized Levenshtein distance
   - Case-insensitive comparison

Combined score = (0.5 × token_sim) + (0.5 × char_sim)

## Configuration
```yaml
fuzzy_matcher:
  threshold: 0.90           # Match threshold (0.0-1.0)
  token_weight: 0.5         # Weight for token vs character matching
```

## Constraints
- Threshold 0.90 by default (high bar for matches)
- Case-insensitive but preserves case in output
- Token-based matching favors whole-word matches over character-level
- No semantic understanding (doesn't know "Street" = "St")

## Dependencies
- None (standalone)

## Complements
- **Before**: AddressStandardizer (abbreviations must be expanded first)
- **With**: DataQualityTriage (confidence scores inform triage decisions)
