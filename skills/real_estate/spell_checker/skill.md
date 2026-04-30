# Spell Checker Skill

## Purpose
Fix spelling mistakes in real estate data using a domain-specific dictionary.
Corrects common misspellings in addresses, municipalities, and city names.
Runs BEFORE other skills since spelling errors corrupt downstream processing.

## When to Use
- **DO**: On raw input data with known typos (data entry errors, OCR mistakes)
- **DO**: Before address standardization (typos confuse validation)
- **DON'T**: On already-validated/canonical data
- **DON'T**: As post-processing (corrections should happen early)

## Input
```python
{
  "address": str,      # Raw street address
  "city": str,         # Raw city name
  "municipality": str, # Raw municipality name
}
```

## Output
```python
{
  "address": str,           # Corrected address
  "city": str,              # Corrected city
  "municipality": str,      # Corrected municipality
  "_decisions": [           # Decision log
    {
      "skill": "SpellChecker",
      "decision": "Corrected municipality: 'scarbbrough' → 'scarborough'",
      "reason": "Found in domain dictionary",
      "confidence": 1.0,
    }
  ]
}
```

## Examples

### Example 1: Exact Dictionary Match
```
Input:  {"city": "toronot"}
Output: {"city": "toronto", confidence: 1.0}
Reason: Exact match in dictionary
```

### Example 2: Fuzzy Match
```
Input:  {"municipality": "scarbbrough"}
Output: {"municipality": "scarborough", confidence: 0.95}
Reason: Fuzzy match (similarity > threshold)
```

### Example 3: No Match
```
Input:  {"city": "Toronto"}
Output: {"city": "Toronto"}
Reason: Already correct, no match needed
```

## Dictionary Coverage
Real estate domains:
- Toronto municipalities: Toronto, Scarborough, North York, Etobicoke, York, East York
- Common typos: scarbbrough, scarbbro, toronot, yorkk, northyork, n.york
- Postal terms: postal cod, provice, municpality

## Configuration
```yaml
spell_checker:
  threshold: 0.85        # Fuzzy match threshold (0.0-1.0)
  domain_dictionary: real_estate
```

## Constraints
- Only knows Toronto real estate domain (~50 entries)
- Fuzzy matching with 0.85 similarity threshold
- Case-insensitive matching (preserves original case on output)
- Returns original if no match found

## Dependencies
- None (standalone skill)

## Complements
- **Next**: AddressStandardizer (after spelling is fixed)
- **Next**: FuzzyMatcher (detects address variants after spelling)
