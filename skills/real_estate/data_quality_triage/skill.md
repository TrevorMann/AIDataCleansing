# Data Quality Triage Skill

## Purpose
Evaluate data quality after all prior processing and route records to final destinations.
Decides: is this record complete enough? Is confidence high enough? Should it be accepted or flagged?
Makes go/no-go decision: DONE (auto-accept) vs NEEDS_REVIEW (escalate) vs UNSALVAGEABLE (discard).

## When to Use
- **DO**: As final gate before persistence (last decision point)
- **DO**: After all other skills complete (needs full context)
- **DO**: To decide: accept (done), escalate (review), or reject (unsalvageable)
- **DON'T**: Early in pipeline (needs results from all prior skills)
- **DON'T**: To fix data (only to evaluate and route)

## Input
```python
{
  "address": str,
  "city": str,
  "postal_code": str,
  "municipality": str,
  "country": str,
  "state_province": str,
  "_municipality_confidence": float,      # From MunicipalityAuthority
  "_geographic_validated": bool,         # From GeographicValidator
  "_agent_decisions": [dict],            # All prior decisions
}
```

## Output
```python
{
  "_triage_route": str,                   # "done" | "needs_review" | "unsalvageable"
  "_triage_confidence": float,            # Overall confidence 0.0-1.0
  "_triage_completeness": float,          # Completeness score 0.0-1.0
  "_triage_data_confidence": float,       # Data quality confidence 0.0-1.0
  "_decisions": [
    {
      "skill": "DataQualityTriageAgent",
      "decision": "Triage: done (completeness: 1.00, confidence: 0.95)",
      "reason": "High confidence (0.95) and complete (1.00)",
      "confidence": 0.95,
    }
  ]
}
```

## Routing Decision Rules

### Route: "DONE" (Auto-Accept)
Conditions:
- Confidence ≥ 0.85 AND
- Completeness ≥ 0.80

Meaning: Record is clean, validated, and ready to accept.

### Route: "NEEDS_REVIEW" (Escalate)
Conditions:
- Confidence ≥ 0.60 AND
- Confidence < 0.85

Meaning: Record is partially clean, human review needed.

### Route: "UNSALVAGEABLE" (Discard)
Conditions:
- Completeness < 0.70 OR
- Confidence < 0.60

Meaning: Missing critical data or low quality, not worth processing.

## Examples

### Example 1: Complete, High Quality
```
Input:  {address: "25 Muir Avenue", city: "Toronto", postal: "M9L 1H7", 
         municipality: "North York", country: "CA", state: "ON",
         _municipality_confidence: 0.95, _geographic_validated: true}
Output: {_triage_route: "done", _triage_confidence: 0.95}
Reason:  High confidence + complete → accept
```

### Example 2: Incomplete Data
```
Input:  {address: "123 Main", city: "Toronto", postal: "", 
         municipality: "", country: "CA", state: ""}
Output: {_triage_route: "unsalvageable", _triage_completeness: 0.33}
Reason:  Missing postal_code, municipality, state → too incomplete
```

### Example 3: Medium Quality, Needs Review
```
Input:  {address: "456 Queen", city: "Toronto", postal: "M4J 1A1",
         municipality: "Toronto", country: "CA", state: "ON",
         _municipality_confidence: 0.70}
Output: {_triage_route: "needs_review", _triage_confidence: 0.70}
Reason:  Confidence 0.70 is between thresholds → escalate for human review
```

## Scoring

### Completeness (0.0-1.0)
Required fields: address, city, postal_code, municipality, country, state_province
Score = (fields_present) / 6

### Confidence (0.0-1.0)
Aggregate of:
1. Municipality confidence (from MunicipalityAuthority)
2. Standardization confidence (fewer corrections = higher)
3. Geographic validation (if validated = +0.85)
Average of all signals

## Configuration
```yaml
data_quality_triage:
  min_confidence_auto_complete: 0.85    # Route to DONE if >= this
  min_confidence_agent_review: 0.60     # Route to NEEDS_REVIEW if >= this
```

## Constraints
- Only evaluates, doesn't fix (use other skills to fix)
- Thresholds are tunable but 0.85/0.60 are defaults
- Cannot infer missing critical fields
- Confidence = aggregate of prior skills (only as good as inputs)

## Dependencies
- MunicipalityAuthorityAgent (confidence from this skill)
- GeographicValidator (validation flag from this skill)

## Complements
- **After**: All prior skills (needs full context)
- **Output**: Final routing decision (DONE → persist, NEEDS_REVIEW → escalate, UNSALVAGEABLE → discard)
