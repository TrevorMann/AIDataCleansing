# Record Linker Skill

## Purpose
Find records that refer to the same real-world entity using config-driven match rules.
Never modifies source field values — outputs linkage metadata only.
Supports transitive grouping: A→B on email, B→C on name → A,B,C same group.

## When to Use
- **DO**: Use for entity resolution / record grouping across a batch
- **DO**: Use `link_batch()` for transitive group assignment
- **DO**: Use `run()` for per-record candidate matching (requires candidates in tools)
- **DON'T**: Use for deduplication on primary key — use DB constraints for that
- **DON'T**: Use to overwrite field values — linker only annotates, never mutates

## Configuration
```yaml
record_linker:
  config:
    blocking_fields: [postal_code]
    match_rules:
      - name: email_exact
        fields: [email]
        match_type: exact
        weight: 1.0
      - name: phone_exact
        fields: [phone_number]
        match_type: exact
        weight: 1.0
      - name: address_fuzzy
        fields: [street_address, city, state]
        match_type: fuzzy
        threshold: 0.80
        weight: 0.60
```

## Output
Per-record (`run()`): `_linked_records: [{id, matched_rule, confidence}]`
Batch (`link_batch()`): `_group_id` on each record (shared across group members)

## Match Types
- `exact`: all fields must match exactly (case-insensitive)
- `fuzzy`: combined field values scored via token + Levenshtein similarity

## Dependencies
- None (pure Python, no external services)
