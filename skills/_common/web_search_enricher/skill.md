# WebSearchEnricher Skill

## Purpose
Resolve missing or ambiguous fields via public web search (Tavily API). Domain-agnostic
core with per-domain parser plugins. Triggered only when confidence is low and there is
an identifiable gap in the record.

## When to Use
- **DO**: When `_triage_route == "needs_review"` AND `_identify_gaps()` returns gaps
- **DO**: When `_municipality_confidence < 0.70` (postal not resolved via DB)
- **DO**: When `_unknown_fsa` flag is set (FSA not in DB)
- **DON'T**: When `_triage_route == "done"` (already high confidence)
- **DON'T**: When `_triage_route == "unsalvageable"` (not worth the cost)
- **DON'T**: When per-batch budget exhausted
- **DON'T**: As first step — run deterministic skills first, web search is last resort

## Input
```python
{
  "postal_code": str,
  "city": str,
  "address": str,
  "state_province": str,
  "_triage_route": str,             # Gating signal
  "_triage_data_confidence": float, # Gating signal
  "_unknown_fsa": str | None,       # Gap hint: postal not resolved
  "_municipality_confidence": float,# Gap hint: municipality uncertain
  "_gap_hints": list[str],          # Explicit gap hints from triage
}
```

## Output
```python
{
  # Enriched fields (depends on gap resolved)
  "municipality": str,          # If postal_unresolved resolved
  "_web_search_evidence": [     # Audit trail of search actions
    {
      "query": str,
      "gap": str,
      "url": str | None,
      "snippet": str | None,
    }
  ],
  "_decisions": [...]
}
```

## Gating Logic
Only runs if ALL conditions met:
1. `_triage_route` is "needs_review" (not "done" or "unsalvageable")
2. `_triage_data_confidence < trigger_below` (default 0.70)
3. At least one identifiable gap
4. Per-batch budget not exhausted

## Configuration
```yaml
web_search_enricher:
  max_queries: 3         # Max queries per record
  trigger_below: 0.70    # Confidence threshold to trigger
  pg_conn: "${runtime.pg_conn}"
  web_cache: "${runtime.web_cache}"  # WebSearchCache instance
```

## Constraints
- Tavily API rate limited and billed per query
- Prefer cached results (WebSearchCache TTL)
- Max N queries per record, M per batch (BatchBudget)
- Parser plugins may not find useful info — always check result
- Web search results can be wrong — output as low-confidence signal
