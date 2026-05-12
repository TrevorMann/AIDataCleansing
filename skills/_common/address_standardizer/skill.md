# Address Standardizer Skill

## Purpose
Expand address abbreviations in configured address fields.
Works for any domain that has street address data — real estate, delivery, HR, etc.
Only processes fields listed in `address_fields` config.

## When to Use
- **DO**: After SpellChecker — typos should be fixed before abbreviation expansion
- **DO**: Before geographic validation — standardized form validates better
- **DON'T**: On non-address fields — only put address-like fields in address_fields

## Configuration
```yaml
address_standardizer:
  config:
    address_fields: [address, mailing_address]
    strip_unit_numbers: false          # remove apt/unit/# suffixes if true
```

## Transformations
- Street types: St→Street, Ave→Avenue, Blvd→Boulevard, Rd→Road, Dr→Drive, Ln→Lane, Ct→Court, Pkwy→Parkway, Ter→Terrace, Pl→Place, Sq→Square
- Quadrant directionals: NE→Northeast, NW→Northwest, SE→Southeast, SW→Southwest
- Single-letter directionals (N, E, S, W) intentionally NOT expanded — too many false positives
- Unit removal: ", Apt 123" / ", Unit 456" / ", #789" → removed (if strip_unit_numbers=true)
- Whitespace normalization

## Dependencies
- None (pure Python, deterministic rule-based)
