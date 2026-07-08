# Gap Type Vocabulary

**Date:** 2026-06-15
**Author:** Trevor Mann
**Status:** Design Review
**Scope:** Controlled, multi-domain vocabulary for naming record defects ("gaps")
**Relates to:** `2026-06-15-agentic-memory-embedding-design.md` (this is the prerequisite that spec depends on)

---

## 1. Why This Exists

The agentic memory spec keys everything — pattern lookup, the planner cache
signature, query-template lookup in `query_pattern_memory` — on a string called
`gap_type`. Today that string has no single definition: it is produced ad-hoc as
hardcoded strings inside `web_search_enricher._identify_gaps()`
(`postal_unresolved`, `municipality_ambiguous`, …), threaded around as free-form
`_gap_hints: list[str]`, and partially overlaps the hardcoded `FlagType` enum in
`cleaning/flags.py`.

If two records with the *same* real problem get *different* gap strings, pattern
memory never accumulates hits. If two records with *different* problems collide on
one string, the system applies the wrong fix. A stable, multi-domain `gap_type`
vocabulary is therefore a hard prerequisite for the memory system.

This spec defines that vocabulary, where it is declared, how it is detected, and
how it reconciles with the existing flag machinery.

## 2. Design Principles

- **Multi-domain by construction.** The framework initializes arbitrary domains
  via LLM. The vocabulary must work for a new domain with little or no authoring.
- **No fragmentation at the part that matters.** The defect verb set is closed.
  Fields and qualifiers vary freely; verbs do not.
- **Automate authoring, allow manual override.** The init agent proposes
  detection rules and discriminators from real data; everything it writes is
  hand-editable.
- **Simplicity first / YAGNI.** Build the smallest slice that makes pattern
  learning work (`missing` detection + column-value qualifiers). Design the rest
  so it slots in without rework.

## 3. Anatomy of a `gap_type`

```
<verb>:<field>[|<qualifier_value>]
```

- **verb** — closed set, extends only via a code change:
  `missing`, `malformed`, `ambiguous`, `mismatch`, `out_of_range`
- **field** — canonical column name from `column_metadata`.
  `mismatch` joins the participating fields, sorted, with `+`.
- **qualifier** — optional. The *value* of a declared discriminator column for
  this record, filled in at runtime. Lowercased.

Delimiters are distinct so they never collide:

| Delimiter | Separates |
|-----------|-----------|
| `:` | verb from field |
| `\|` | field from qualifier value |
| `+` | fields in a multi-field `mismatch` |

Examples:

```
missing:postal_code
missing:postal_code|ca
malformed:phone|us
ambiguous:municipality|ca
mismatch:city+province
```

### Verb definitions

| Verb | Meaning | Maps from existing FlagType |
|------|---------|-----------------------------|
| `missing` | field is null / empty | postal_unresolved, unknown_country, municipality_unresolved |
| `malformed` | present but wrong shape/value | (bad_province_abbr, etc.) |
| `ambiguous` | present but cannot be disambiguated | postal_ambiguous |
| `mismatch` | two or more fields disagree | cross_region_mismatch |
| `out_of_range` | numeric/date outside valid bounds | (none today) |

Process/outcome flags (`low_confidence_research`, `guardrail_blocked`,
`resolved_after_escalation`) are **not** gap types — they describe pipeline
outcomes, not data defects, and stay in `FlagType` only.

## 4. Declaration — in `column_metadata`

A gap is a property of a field, so its declaration lives on the field's
`column_metadata` row (one source of truth per column; rides the existing
init/annotation/seed flow). A new `gap_detection` block:

```yaml
# column_metadata for postal_code (real_estate)
gap_detection:
  discriminator: country        # ONE column drives both the qualifier and rule selection
  missing: true                 # default-on for every field; zero config
  malformed:                    # DESIGNED, not built in v1
    by: country
    rules:
      ca: '^[A-Z]\d[A-Z] ?\d[A-Z]\d$'
      us: '^\d{5}(-\d{4})?$'
    # no rule for a given discriminator value -> do not flag (never invent a defect)
  # out_of_range, mismatch: same `by: <discriminator> / rules:` shape; designed, not built
```

- `missing: true` is the default for every field — no per-field work required.
- `discriminator` names another column whose value (a) qualifies the gap string
  and (b) selects which detection rule applies. The **same** declaration serves
  both jobs.
- `malformed` / `out_of_range` / `mismatch` use a uniform `by: <discriminator> /
  rules:` structure. **Designed now, not built in v1** (see §7).

Why `column_metadata` and not a new table or `memory.yaml`:

- A new registry table would duplicate `column_metadata`'s purpose — two places
  describing one column.
- `memory.yaml` holds the memory system's tuning/thresholds; field-level
  detection rules belong with the field definition, not the tuning knobs.

## 5. Detection — one shared classifier

A single function replaces the scattered logic:

```python
# Implemented as a PURE function: the gap_detection config is loaded separately
# (db.schema_discovery.get_gap_detection / pg_query_memory.gap_detection_for) and
# passed in, so the classifier is DB-free and trivially testable.
def classify_gaps(record: dict, gap_config: dict) -> list[str]:
    """Emit gap_type strings for a record from a pre-loaded gap_detection config."""
```

- **v1 builds the `missing` path only**: for each field with `missing: true`, if
  the value is null/empty, emit `missing:<field>` (plus `|<discriminator_value>`
  when a discriminator is declared and present on the record).
- The other verbs are wired with the structure in place but return nothing in v1.
- This **replaces** `web_search_enricher._identify_gaps()`, which becomes a thin
  caller of `classify_gaps`. Exactly one place computes gaps.

### Discriminator-keyed detection

Detection itself can depend on the discriminator, not just the resolution
strategy. A Canadian postal regex would falsely flag a valid US ZIP. So the
`malformed`/`out_of_range`/`mismatch` rules are keyed by the discriminator value
(`by: country`, `rules: {ca: ..., us: ...}`). A record whose discriminator value
has no rule is **not flagged** — we never invent a defect we cannot validate.

This remains within "column-value qualifiers": rules are keyed on *another
column's value*, never on inspecting the field's own content for a sub-type.
Content-sniffing sub-conditions are the deferred path (§7).

## 6. Relationship to `FlagType`

- `gap_type` is the **input** vocabulary: triage → planner → pattern memory.
- `FlagType` remains the **output** vocabulary for the flags/analytics table.
- The data-defect flags (`postal_unresolved`, etc.) are **derived from**
  `gap_type` output. The process flags are untouched.

No double bookkeeping: gaps are classified once, and the corresponding flags fall
out of that classification.

## 7. v1 Build Scope vs. Designed-For

| Capability | v1 | Designed (later) |
|-----------|----|------------------|
| `<verb>:<field>` base strings, 5-verb set | ✅ | |
| Column-value qualifiers (`\|<value>`) | ✅ | |
| `missing` detection (mechanical, all fields) | ✅ | |
| `column_metadata.gap_detection` block | ✅ (missing + discriminator) | full rule sets |
| Shared `classify_gaps`, replaces `_identify_gaps` | ✅ | |
| `FlagType` derived from gaps | ✅ | |
| `malformed` / `out_of_range` / `mismatch` detection | structure only (no-op) | per-discriminator rules |
| Sub-condition detectors (content-sniffing, e.g. `stacked_unit`) | ❌ | resolver that writes a key into record context; same matching code, possibly its own skill |

Learning works at the base level from day one regardless of how much is declared.

## 8. Runtime Matching (consumed by the memory spec)

Pattern lookup tries **most-specific-first**, then falls back:

```
missing:postal_code|ca   →   missing:postal_code
```

Because the base always exists, a domain benefits from accumulated learning even
before anyone declares a discriminator. The qualifier only sharpens matches where
the data shows it pays off.

## 9. Authoring Flow (automated, overridable)

During `initialize_domain.py` Phase 3 seed research:

1. Sample real records from registered tables.
2. Derive base gaps mechanically (which fields are null/anomalous).
3. **Propose** discriminators/rules from observed distributions, e.g.
   "`postal_code` spans 3 distinct `country` values — qualify by `country`?"
4. Proposals are **batched** — accept-all / none / pick — never one prompt per
   record.
5. Default is base-only if the user skips everything.
6. Everything written to `column_metadata.gap_detection` is hand-editable after.

## 10. Tooling

No new tools or external dependencies. All reuse:

- `column_metadata` migration + seeder field (mirrors
  `006_column_metadata_annotation_fields.sql`)
- Existing Phase 3 init LLM + data-sampling path for proposals
- Existing `query_pattern_memory`, planner cache signature, LLM/embedding factory

New code is ordinary: `classify_gaps`, one migration, and the `FlagType`
derivation wiring. The only candidate for a dedicated skill is the *deferred*
sub-condition detectors.

## 11. Open Questions / Follow-on

1. **Sub-condition detectors** — deferred. When real data needs content-derived
   sub-types (e.g. `stacked_unit`), add a resolver that writes a qualifier key
   into record context; matching code is unchanged.
2. **`out_of_range` rule shape** — designed as `by: <discriminator> / rules:`
   with min/max bounds; exact schema deferred until first real use.
3. **Migration of existing strings** — `postal_unresolved` etc. map to the new
   scheme per §3; the cutover plan belongs in the implementation plan.
