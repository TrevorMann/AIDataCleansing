# Data Cleaning Refactor — A Migration Design (C → A)

**Date:** 2026-04-27
**Status:** Approved (forward-looking; depends on the C-Hybrid refactor shipping first)
**Scope:** Migration from C-Hybrid (one shared per-country agent reused per group) to A (genuinely independent per-country agents, instantiated in parallel, with optional per-country tool registries).
**Companion doc:** `2026-04-27-data-cleaning-c-hybrid-refactor-design.md` is the prerequisite. This document only makes sense once C-Hybrid is shipped.

---

## 1. Why this document exists

The C-Hybrid refactor was designed with clean migration boundaries so that moving to A is **mechanical, not a rewrite**. This spec captures *what changes and what does not* during that migration, so the future change can be planned in hours rather than days, and so the discipline required to keep the migration cheap is documented while the design rationale is fresh.

If the C-Hybrid refactor drifts from the contracts in this document — most importantly the three migration boundary invariants — the cost of A goes from ~1 day to ~1 week. Treat the invariants as non-negotiable.

## 2. When to migrate to A

Migrate when at least one of these becomes true:

- **Per-country tool divergence**: a country needs a tool the others do not (e.g. a Japan-Post API client, a Canada Post FSA verifier, a Mexico SEPOMEX lookup). Today every agent shares the same `[web_search]` tool list — A allows different tools per country without contaminating siblings.
- **Volume requires parallelism**: batches grow large enough that streaming through one shared agent per group becomes the wall-clock bottleneck. Tavily latency is the practical ceiling; once you regularly process batches that take >2 minutes on the standard tier, parallelism pays.
- **Failure isolation matters**: today, if the per-country agent loops or errors on a Mexican record, the Canadian batch in the same workflow is delayed (because the orchestrator iterates groups serially). Under A, sibling failures are independent.
- **Per-country prompt evolution speed**: when changes to the Canada prompt repeatedly cause regressions in the USA agent's behavior (or vice versa), per-country isolation pays. This is rare with the C-Hybrid design but possible if shared prompt scaffolding grows.

If none of these conditions are met, **stay on C-Hybrid**. A's per-call cost is higher and its complexity is real; do not migrate prematurely.

## 3. What changes

Only the orchestrator's dispatch loop and the `Clients` allocation strategy. Everything else — `CleaningAgent`, `EscalationAgent`, `WebSearchCache`, `LLMClient`, `flags.py`, `prompts/`, persistence, REPL, `AdHocConversation` — is unchanged.

### 3.1 Orchestrator dispatch — before (C) and after (A)

**Before (C-Hybrid)**:

```python
# cleaning/orchestrator.py — current, serial, one agent per country group
for country_code, batch in groups.items():
    agent = CleaningAgent(
        country_code=country_code,
        system_prompt=prompts.build_system_prompt(country_code, schema),
        ...
        llm_client=clients.standard,
        web_cache=web_cache,
        escalator=escalator,
    )
    outputs.extend(agent.process(batch))
```

**After (A)**:

```python
# cleaning/orchestrator.py — A migration, parallel, persistent per-country agents
agents = {
    code: CleaningAgent(
        country_code=code,
        system_prompt=prompts.build_system_prompt(code, schema),
        ...
        llm_client=clients.standard,                 # could differ per country if needed
        tools=COUNTRY_TOOLS.get(code, DEFAULT_TOOLS), # per-country tool registry (optional)
        web_cache=web_cache,                          # still shared — cache benefits all agents
        escalator=escalator,                          # still shared
    )
    for code in COUNTRIES
}

with ThreadPoolExecutor(max_workers=len(COUNTRIES)) as ex:
    futures = {code: ex.submit(agents[code].process, batch)
               for code, batch in groups.items()}
    outputs = [out for fut in futures.values() for out in fut.result()]
```

That is the entire structural change. Lines of code: ~15 changed in `orchestrator.py`. Other files: 0.

### 3.2 New optional surface: per-country tool registries

A allows different tools per country. C-Hybrid does not (every agent gets `[web_search]`). Adding this is additive — agents that don't have a custom registry get the default:

```python
# cleaning/tools.py (new under A; optional)

DEFAULT_TOOLS = [WEB_SEARCH_TOOL]

COUNTRY_TOOLS = {
    'CA':  [WEB_SEARCH_TOOL, CANADA_POST_FSA_TOOL],
    'JP':  [WEB_SEARCH_TOOL, JAPAN_POST_TOOL],
    'MX':  [WEB_SEARCH_TOOL, SEPOMEX_TOOL],
}
```

Each tool has its own `execute_tool` handler registered with the agent. The agent's tool dispatch is already a switch on tool name; adding more entries is additive.

**This is not added during the migration unless a country actually needs a custom tool.** Empty `COUNTRY_TOOLS = {}` is valid and means "all agents use `DEFAULT_TOOLS`," which preserves C-Hybrid behavior exactly.

### 3.3 Optional: per-country LLM clients

A allows different model tiers per country. The most common case is keeping all agents on `clients.standard`, but you could route a country with stricter rules (e.g. Japan, where address parsing is harder) to `clients.deep`:

```python
COUNTRY_CLIENTS = {
    'CA':  clients.standard,
    'USA': clients.standard,
    'NL':  clients.standard,
    'MX':  clients.standard,
    'JP':  clients.deep,        # harder address parsing
}

agents = {code: CleaningAgent(..., llm_client=COUNTRY_CLIENTS[code], ...) for code in COUNTRIES}
```

Like the tool registry, this is optional and additive. Default is "all standard," matching C-Hybrid.

## 4. What does NOT change

To stay disciplined about scope, here is the explicit list of what the migration **does not touch**:

- `CleaningAgent` class itself. Same constructor, same `process()` signature, same internal logic.
- `EscalationAgent`. Still shared across all per-country agents. Still uses `clients.deep`.
- `WebSearchCache`. Still shared, still per-run. Already thread-safe — designed for this.
- `LLMClient` and `build_clients()`. Same env-driven config.
- `flags.py`, `flags` table, `FlagType` enum.
- `prompts/` package.
- `pre_cleaner.py`. Pre-cleaning is still serial (it's pure Python and fast).
- `cleaning/conversation.py` / `AdHocConversation`. The REPL chat path is unchanged.
- `multi_turn_conversation.py`. The REPL wrapper. Unchanged.
- Database schema. Unchanged.
- Public API: `run_cleaning_workflow(query, *, country_override=None) -> CleaningRunReport`. Identical signature, identical return type.

If the migration touches any of the above, the C-Hybrid design failed at one of its boundaries and the migration is more expensive than necessary. Investigate what invariant was violated.

## 5. Migration boundary invariants (recap from C-Hybrid spec)

These three invariants are **load-bearing for cheap migration**. They must hold in the C-Hybrid implementation, and the implementation plan must verify them:

1. **`CleaningAgent` is country-fixed at construction time.** The agent never inspects records to decide "what country am I serving?" — it already knows. No `if country == 'CA'` branches inside the loop.
2. **No shared mutable state across agents.** Each agent owns its own `messages`, its own counters, its own search log. No reaching into `self.parent` or module-level state. The cache and escalator are *injected* and treated as external services.
3. **Agents return data; the orchestrator persists.** Agents never call `insert_cleaned_data`, never write `flags`, never touch the audit log. The orchestrator is the sole writer.

Each invariant directly enables a property of A:

- (1) lets you swap "one agent reused across groups" for "five agents instantiated once" without changing agent code.
- (2) lets parallel execution be safe without locking — agents don't share mutation surface.
- (3) lets the orchestrator collect results from N futures and write them in a deterministic order, regardless of which agent finished first.

## 6. Concurrency considerations

A introduces real parallelism via `ThreadPoolExecutor`. Three things to verify before the migration:

### 6.1 `WebSearchCache` thread safety

The C-Hybrid cache must be implemented thread-safe (using `threading.Lock` around `get`/`put`, or `dict` operations that are safe by default in CPython but should not be relied on for compound check-then-act patterns). The cache is shared across all parallel agents and is the primary contention point. Verify with a stress test: 5 agents, each making 50 cached calls with overlapping queries.

### 6.2 SQLite concurrency

Persistence remains serial (the orchestrator writes after collecting all futures). SQLite's per-process write lock is fine for this pattern. **Do not change persistence to write per-agent in parallel** — that is a different design and not the goal here.

### 6.3 Tavily rate limits

Five agents firing in parallel may hit Tavily rate limits faster than serial dispatch. The cache mitigates this dramatically (fewer unique queries) but the bursty pattern is different. Consider either:
- A semaphore in `WebSearchCache._tavily_call` capping concurrent outbound HTTP calls (e.g. 3).
- Or relying on the cache + retry-with-backoff in the Tavily client.

The simpler approach (semaphore) is recommended. ~10 lines of code.

## 7. Cost model

A pays per-agent overhead (system prompt + tool definitions) on every call from every agent. With prompt caching enabled (Haiku 4.5 or native Anthropic stages):

- C-Hybrid: one cached system+tools per country group, reused within the group's calls.
- A: same — each per-country agent has its own cached system+tools, reused within that agent's calls.

**The cost difference is negligible if caching is enabled.** Without caching (gpt-oss stage):

- C-Hybrid: pays full system+tools tokens once per group iteration.
- A: pays full system+tools tokens once per agent.process() call (which equals once per group). Same number of full payments.

So **A is not materially more expensive than C-Hybrid in token terms**. The cost story is dominated by the escalation tier (deep model for hard cases), which is unchanged.

## 8. Test changes for A

Most C-Hybrid tests are unchanged. New tests required for A:

- **Concurrency stress test** for `WebSearchCache`: N threads, overlapping queries, assert no double-fetch and no lost writes.
- **Parallel orchestrator test**: 5 country groups, mocked agents that sleep different amounts, verify wall-clock time is bounded by the slowest agent (not the sum).
- **Failure isolation test**: one agent raises mid-flight, verify the others complete and the orchestrator records the failure in `CleaningRunReport.errors`.

Pre-existing unit tests for `CleaningAgent`, `EscalationAgent`, `WebSearchCache`, `LLMClient`, persistence, flags, and the public API are **unchanged** because none of those classes change.

## 9. Migration plan (estimated effort)

Total: **~1 day of work** if invariants held during C-Hybrid implementation.

| Step | Effort | Notes |
|---|---|---|
| 1. Verify C-Hybrid invariants hold | 1 hour | Code review with the three invariants as checklist. |
| 2. Add `ThreadPoolExecutor` dispatch in `orchestrator.py` | 1 hour | ~15 lines changed. |
| 3. Add `WebSearchCache` thread safety (if not already) | 1 hour | `threading.Lock` around `get`/`put`. |
| 4. Add Tavily semaphore | 30 min | ~10 lines in cache. |
| 5. Add concurrency stress test for cache | 1 hour | New test file. |
| 6. Add parallel orchestrator test | 1 hour | Mocked agents with sleeps. |
| 7. Add failure isolation test | 1 hour | One agent raises, verify others complete. |
| 8. Smoke test live against gpt-oss with mixed batch | 1 hour | End-to-end on the existing dataset. |
| 9. (Optional) Per-country tool registry | 2 hours | Only if a country actually needs a custom tool at this point. |
| 10. (Optional) Per-country client tier | 30 min | Only if a country needs a different model. |

## 10. What to add to A later (not in scope for the initial migration)

These are *future* enhancements once A has been live for a while and you have data on what is actually needed:

- **Per-country prompt versioning + A/B testing**: route a fraction of records through an experimental prompt for one country.
- **Per-country observability**: separate latency / cost / error metrics dashboards.
- **Async I/O instead of threads**: if Tavily and the LLM clients gain async SDKs and parallelism becomes the bottleneck.
- **Cross-process parallelism**: if a single Python process becomes CPU-bound (unlikely for this workload — it's I/O dominated).

None of these are needed at A's launch. They become candidates only when concrete performance or operational pain motivates them.

## 11. Decision log

| Question | Decision | Rationale |
|---|---|---|
| Use threads or async for parallelism? | Threads (`ThreadPoolExecutor`). | I/O-bound workload, ≤5 country groups, threads are simpler than async; rewriting agents to async is gratuitous churn. |
| Process-level or thread-level parallelism? | Threads. | The workload is I/O-bound; processes add IPC complexity for no throughput gain. |
| Share `WebSearchCache` across agents? | Yes. | Cache hit rates are dominated by cross-country overlap (e.g. Toronto-related queries from many records). Per-agent caches would defeat the savings. |
| Share `EscalationAgent` across agents? | Yes. | Escalation is ~5% of records; one shared escalator with `clients.deep` is sufficient and avoids spinning up five expensive instances. |
| Per-country tool registries on day one? | No. | Add only when a real per-country tool need appears. Premature optimization otherwise. |
| Per-country LLM tiers on day one? | No. | All `clients.standard` to start. Add per-country routing only when a specific country shows it needs more. |
| Persistence parallelism? | No. | Stays serial in the orchestrator. SQLite + the audit-trail ordering requirements make parallel writes a separate, harder design problem. |
