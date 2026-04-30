# Municipality Authority Skill

## Purpose
Resolve canonical municipality using postal code (FSA) as source of truth.
Detects conflicts between upstream municipality name and postal-code-implied municipality.
Enforces consistency: postal code determines municipality for Toronto real estate.

## When to Use
- **DO**: After address standardization (to validate geographic coherence)
- **DO**: When postal code is present (FSA lookup is authoritative)
- **DO**: To resolve conflicts (postal trusted over user-entered name)
- **DON'T**: Without postal code (cannot resolve)
- **DON'T**: Before address standardization (standardization may affect confidence)

## Input
```python
{
  "postal_code": str,      # Full postal code (M9L 1H7)
  "municipality": str,     # User-entered municipality
}
```

## Output
```python
{
  "municipality": str,     # Resolved canonical municipality (from FSA)
  "_municipality_confidence": float,  # 1.0 (match), 0.95 (resolved), 0.85 (conflict), 0.60 (uncertain)
  "_decisions": [
    {
      "skill": "MunicipalityAuthorityAgent",
      "decision": "Resolved municipality: North York (from FSA M9L)",
      "reason": "FSA M9L → North York, upstream was 'Humber Summit', trusting postal",
      "confidence": 0.85,
    }
  ]
}
```

## FSA Coverage
Toronto postal codes mapped to canonical municipalities:
- M1A-M1X → Scarborough (20 FSAs)
- M2H-M3N → North York (14 FSAs)  
- M4A-M7A → Toronto (38 FSAs)
- M7R-M9W → Etobicoke (13 FSAs)
- M9L-M9P → North York (4 FSAs, special cases)

Total: ~95 Toronto M-series FSAs mapped

## Examples

### Example 1: Conflict, FSA Trusted
```
Input:  {postal: "M9L 1H7", municipality: "Humber Summit"}
Output: {municipality: "North York", confidence: 0.85}
Reason: FSA M9L → North York, trusting postal over upstream name
```

### Example 2: Perfect Match
```
Input:  {postal: "M2N 1A1", municipality: "North York"}
Output: {municipality: "North York", confidence: 1.0}
Reason: Upstream matches FSA lookup (perfect agreement)
```

### Example 3: No Upstream
```
Input:  {postal: "M1A 1B1", municipality: ""}
Output: {municipality: "Scarborough", confidence: 0.95}
Reason: No upstream provided, resolved via FSA lookup
```

### Example 4: Unknown FSA
```
Input:  {postal: "M99 9Z9", municipality: "Unknown"}
Output: {municipality: "Unknown"}
Confidence: 0.0
Reason: FSA not in mapping, cannot resolve
```

## Configuration
```yaml
municipality_authority:
  trust_postal_over_name: true          # FSA is source of truth
  escalate_confidence_threshold: 0.60   # Route to review if < 0.60
```

## Constraints
- Toronto-only (M-series postal codes)
- Only covers ~95 FSAs
- Cannot resolve non-Toronto postal codes
- Case-insensitive matching for municipality names
- Requires postal code (cannot infer from address)

## Dependencies
- None (standalone skill)

## Complements
- **Before**: AddressStandardizer (standardize address, get postal clarity)
- **After**: GeographicValidator (validate resolved municipality is coherent)
- **Input**: Postal code must be valid format (M#A #A# or M#L #A#)
