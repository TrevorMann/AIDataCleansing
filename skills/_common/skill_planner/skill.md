# SkillPlanner Skill

## Purpose
Read available skill documentation + record state, then output an ordered skill
execution plan. Domain-agnostic — works for any domain registered in the skill
registry. Replaces hardcoded orchestration with LLM-driven dynamic planning.

## When to Use
- **DO**: When `_triage_route == "needs_review"` (record is ambiguous, needs more work)
- **DO**: When deterministic tier left low-confidence fields
- **DO**: When record has identifiable gap_types
- **DON'T**: When `_triage_route == "done"` — waste of LLM call
- **DON'T**: When `_triage_route == "unsalvageable"` — nothing to plan
- **DON'T**: On every record — check plan cache first (same record shape = same plan)

## Input
```python
{
  "_triage_route": str,              # From data_quality_triage
  "_triage_data_confidence": float,  # From data_quality_triage
  "_gap_hints": list[str],           # From triage / earlier skills
  "<all record fields>": ...,
}
# Plus tools:
{
  "registry": SkillRegistry,         # For skill menu + metadata
}
```

## Output
```python
{
  "_planned_skills": list[str],   # Ordered skill names (validated, no hallucinations)
  "_plan_reasoning": str,         # LLM reasoning for the plan
  "_plan_source": str,            # "cache" | "llm"
}
```

## Caching
Signature = hash(fields_present + conf_bucket + route + gaps + domain + version).
Same shape = same plan → cache hit → no LLM call. TTL: 24h (configurable).

## Cost
HIGH — LLM call on cache miss. Expected cache hit rate: 80%+ after warm-up
(most records cluster into a handful of shapes).

## Configuration
```yaml
skill_planner:
  tier: "fast"              # LLM tier: fast | standard | deep
  plan_cache_ttl_hours: 24  # Cache TTL
  pg_conn: "${runtime.pg_conn}"
  llm_client: "${runtime.llm_client}"  # Optional: inject pre-built client
```

## Constraints
- Hallucinated skill names rejected (only registry skills accepted)
- Dependency order enforced via registry.topological_sort()
- LLM must output valid JSON: {"plan": [...], "reasoning": "..."}
- Falls back to empty plan on parse failure (safe degradation)
