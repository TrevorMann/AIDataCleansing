# Spell Checker Skill

## Purpose
Fix obvious spelling mistakes in configured text fields using a general English dictionary.
Domain-specific proper nouns are handled via an override table in the DB.
Only processes fields listed in `text_fields` config — everything else is untouched.

## When to Use
- **DO**: On raw input data with entry errors or OCR mistakes in free-text fields
- **DO**: Before address standardization — typos confuse abbreviation expansion
- **DON'T**: On PII fields (names, emails, IDs) — omit them from text_fields
- **DON'T**: On already-validated canonical data
- **DON'T**: Expect it to fix domain proper nouns not in the override table

## Configuration
```yaml
spell_checker:
  config:
    text_fields: [description, notes, category]   # only these are processed
    threshold: 0.85                               # min confidence to apply correction
    domain: <your_domain>                         # which DB override table to load
    max_edit_distance: 2                          # symspellpy max edit distance
```

## Input / Output
```python
# Input
{"description": "ofice suppies", "last_name": "Smyth", "notes": "near the prk"}

# Output (last_name untouched — not in text_fields)
{"description": "office supplies", "last_name": "Smyth", "notes": "near the park"}
```

Audit entries available via `skill.get_audit()` — not in the returned record.

## Correction Logic
1. Check domain override table (DB) — exact match wins at confidence=1.0
2. Check symspellpy general English dictionary — corrects if confidence ≥ threshold
3. No match → original value returned unchanged

## Dependencies
- symspellpy (bundled English dictionary, no external calls)
- DB connection optional — without it, override table is empty
