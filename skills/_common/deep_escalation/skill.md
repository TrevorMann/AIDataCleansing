# Deep Escalation

Last resort in the escalation ladder. Sends a record that is still
`needs_review` after deterministic skills, web search, and the planner to the
**deep LLM tier** (configured via `LLM_BACKEND_DEEP`, e.g. Sonnet) for
multi-round investigation with web search.

- Reuses the record's `_web_search_evidence` as a prior-search log so queries
  are never repeated.
- Maps `_gap_hints` to escalation flag hints; defaults to
  `low_confidence_research` when no mappable hints exist.
- Emits `_escalation_flags` on the record and a full audit decision.
- Cost: high — budget-gated per batch; only fires on `needs_review` records.

Config:
- `tier` (default `deep`) — LLM tier from `cleaning.llm_client`
- `max_rounds` (default 10) — tool-use round cap per record
- `web_cache` — injected `WebSearchCache` (falls back to a fresh one)
