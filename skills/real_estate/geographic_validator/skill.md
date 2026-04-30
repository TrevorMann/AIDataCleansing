# Geographic Validator Skill

## Purpose
Validate geographic coherence across address, postal code, municipality, province, and country.
Detects format errors, invalid codes, and hierarchy inconsistencies.
Flags records with geographic data conflicts for review.

## When to Use
- **DO**: After municipality resolution (validate resolved geography is coherent)
- **DO**: To catch format errors (postal code, province codes)
- **DO**: To validate country-specific rules (Canadian postal ≠ USA zip)
- **DON'T**: Before municipality resolution (needs resolved municipality first)
- **DON'T**: As standalone validation (needs other skills to fix issues)

## Input
```python
{
  "country": str,                # Country code (CA, USA, NL, MX, JP)
  "state_province": str,         # Province/state code (ON, AB, CA, etc.)
  "postal_code": str,            # Postal code (format varies by country)
  "municipality": str,           # Resolved municipality
}
```

## Output
```python
{
  "_geographic_validated": bool,  # True if validation completed
  "_decisions": [                 # Validation decisions
    {
      "skill": "GeographicValidator",
      "decision": "Valid postal format: M9L 1H7",
      "reason": "Matches Canada postal code pattern",
      "confidence": 1.0,
    }
  ]
}
```

## Examples

### Example 1: Valid Canadian Data
```
Input:  {country: "CA", state: "ON", postal: "M9L 1H7", municipality: "North York"}
Output: All validations pass, _geographic_validated: true
```

### Example 2: Invalid Postal Format
```
Input:  {country: "CA", postal: "INVALID"}
Output: Decision: "Invalid postal format: INVALID for country CA"
```

### Example 3: Invalid Province
```
Input:  {country: "CA", state: "XX"}
Output: Decision: "Invalid province: XX for country CA"
```

### Example 4: Hierarchy Mismatch
```
Input:  {country: "CA", state: "ON", municipality: "Los Angeles"}
Output: Decision: "Verify municipality: Los Angeles in ON"
Confidence: 0.70 (warning, not error)
```

## Validations

### Postal Code Format
- CA: M#L #A# (M9L 1H7) — 6 characters, specific pattern
- USA: #####(-####)? (12345 or 12345-6789) — 5 or 9 digits
- NL: #### ## (1234 AB) — 4 digits + 2 letters
- MX: ##### (12345) — 5 digits
- JP: ###-#### (123-4567) — 3 digits, dash, 4 digits

### Province/State Codes
- CA: AB, BC, MB, NB, NL, NS, NT, NU, ON, PE, QC, SK, YT (13 provinces)
- USA: AL, AK, AZ, ... WY (50 states + territories, 54 total)

### Hierarchy Rules
- Ontario (ON) + municipality "Toronto" = valid
- Ontario (ON) + municipality "Los Angeles" = suspicious
- Toronto municipalities: Toronto, Scarborough, North York, Etobicoke, York, East York

## Configuration
```yaml
geographic_validator:
  strict_mode: false   # Warning vs error on mismatches
```

## Constraints
- Only validates format, not existence (doesn't check if address actually exists)
- Ontario-specific rules hard-coded
- Cannot infer missing data (needs explicit inputs)
- Case-insensitive but validates uppercase codes

## Dependencies
- MunicipalityAuthority (should run after municipality is resolved)

## Complements
- **After**: MunicipalityAuthority (validate resolved municipality is coherent)
- **Before**: DataQualityTriage (validation results inform triage decisions)
