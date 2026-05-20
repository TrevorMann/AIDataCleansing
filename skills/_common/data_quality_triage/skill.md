# Data Quality Triage

Routes each record to one of three outcomes based on completeness and confidence signals:

- **done** — high confidence, complete record; no further processing needed
- **needs_review** — medium confidence or gaps; downstream enrichment or human review needed
- **unsalvageable** — critically incomplete or very low confidence; skip further processing

## Configuration

| Key | Type | Description |
|-----|------|-------------|
| `required_fields` | list | Fields that must be non-empty for the record to be considered complete |
| `confidence_signal_keys` | list | Record keys whose float values feed into min() confidence scoring |
| `validated_signal_keys` | list | Boolean flag keys; `True` contributes a 0.85 confidence signal |
| `min_confidence_auto_complete` | float | Confidence threshold for routing `done` (default 0.85) |
| `min_confidence_agent_review` | float | Confidence threshold for routing `needs_review` (default 0.60) |

## Routing rules

| Condition | Route |
|-----------|-------|
| completeness < 0.70 | unsalvageable |
| confidence ≥ min_auto AND completeness ≥ 0.80 | done |
| confidence ≥ min_review | needs_review |
| else | unsalvageable |

Confidence uses **weakest-link** (`min()`) — one poor signal tanks the record.
