# Address Standardizer Skill

## Purpose
Normalize address format for consistency and validation.
Expands abbreviations (St→Street, Ave→Avenue, NE→Northeast) and strips unit numbers.
Makes addresses machine-readable after spelling is corrected.

## When to Use
- **DO**: After SpellChecker (spelling → standardization → fuzzy matching)
- **DO**: Before geographic validation (standardized addresses validate better)
- **DON'T**: On already-standardized addresses
- **DON'T**: Before spell checking (ambiguous abbreviations corrupt correction)

## Input
```python
{
  "address": str,  # Raw address like "25 Muir Ave, Apt 123"
}
```

## Output
```python
{
  "address": str,  # Standardized address like "25 Muir Avenue"
  "_decisions": [
    {
      "skill": "AddressStandardizer",
      "decision": "Standardized address: '25 Muir Ave' → '25 Muir Avenue'",
      "reason": "Applied address standardization rules",
      "confidence": 1.0,
    }
  ]
}
```

## Examples

### Example 1: Expand Abbreviations
```
Input:  {"address": "123 Main St"}
Output: {"address": "123 Main Street"}
```

### Example 2: Strip Unit Numbers
```
Input:  {"address": "25 Muir Ave, Apt 456"}
Output: {"address": "25 Muir Avenue"}  # (if strip_unit_numbers=true)
```

### Example 3: Expand Quadrant Directionals
```
Input:  {"address": "123 Main St NE"}
Output: {"address": "123 Main Street Northeast"}
```

## Transformations
- Street type abbreviations: St→Street, Ave→Avenue, Blvd→Boulevard, Rd→Road, Dr→Drive, Ln→Lane, Ct→Court, etc.
- Quadrant directionals: NE→Northeast, NW→Northwest, SE→Southeast, SW→Southwest
- Unit/apt removal: ", Apt 123" / ", Unit 456" / ", #789" → removed
- Whitespace normalization: Multiple spaces → single space

## Configuration
```yaml
address_standardizer:
  strip_unit_numbers: false         # Remove apt/unit/# numbers
```

## Constraints
- Only standardizes known abbreviations (limited to ~30 patterns)
- Cannot infer missing information
- Cannot validate address exists
- Case-insensitive but preserves original case pattern on output
- Single-letter directionals (N, E, S, W) are intentionally NOT expanded: the
  pattern `\bN\b` matches "N" anywhere in a token sequence and produces false
  expansions (e.g. "123 Doe N Main" → "123 Doe North Main"). Quadrants only
  (NE/NW/SE/SW) are unambiguous and safe to expand.

## Dependencies
- None (standalone skill)

## Complements
- **Before**: SpellChecker (spelling must be fixed first)
- **After**: FuzzyMatcher (standardized form detects variants better)
- **With**: GeographicValidator (standardized addresses validate better)
