# Address Standardizer Skill

## Purpose
Normalize address format for consistency and validation.
Expands abbreviations (St→Street, Ave→Avenue, E→East) and strips unit numbers.
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

### Example 3: Expand Directionals
```
Input:  {"address": "456 E Queen"}
Output: {"address": "456 East Queen"}  # (if expand_directionals=true)
```

## Transformations
- Street type abbreviations: St→Street, Ave→Avenue, Blvd→Boulevard, Rd→Road, Dr→Drive, Ln→Lane, Ct→Court, etc.
- Directional abbreviations: E→East, W→West, N→North, S→South, NE→Northeast, etc.
- Unit/apt removal: ", Apt 123" / ", Unit 456" / ", #789" → removed
- Whitespace normalization: Multiple spaces → single space

## Configuration
```yaml
address_standardizer:
  strip_unit_numbers: false         # Remove apt/unit/# numbers
  expand_directionals: true         # E→East, W→West, etc.
```

## Constraints
- Only standardizes known abbreviations (limited to ~30 patterns)
- Cannot infer missing information
- Cannot validate address exists
- Case-insensitive but preserves original case pattern on output

## Dependencies
- None (standalone skill)

## Complements
- **Before**: SpellChecker (spelling must be fixed first)
- **After**: FuzzyMatcher (standardized form detects variants better)
- **With**: GeographicValidator (standardized addresses validate better)
