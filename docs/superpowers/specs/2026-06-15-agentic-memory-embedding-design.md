# Agentic Memory & Embedding Architecture for Data Cleaning

**Date:** 2026-06-15  
**Author:** Trevor Mann  
**Status:** Design Review  
**Scope:** Multi-domain pattern memory, semantic caching, and batch context management

---

## 1. Overview & Goals

### Problem
The data cleaning pipeline currently processes each record independently. When it encounters a gap (missing postal code, ambiguous municipality, etc.), it uses the AI planner to select skills — often redundantly re-solving the same types of problems across records. This is inefficient and doesn't learn from past successes.

### Solution
Implement a **three-tier agentic memory system** that:

1. **Pattern Memory (Primary):** Learn what types of gaps exist and which skill combinations solve them reliably
2. **Semantic Cache (Secondary):** Avoid redundant API calls via vector similarity lookups
3. **Working Context (Tertiary):** Enable skill-to-skill communication within a batch without tight coupling

### What Patterns Do (and Don't Do)

A pattern match **never** marks a record as clean or skips skill execution. Patterns do two things only:

- **Phase 2 (triage):** Apply a small confidence adjustment so triage routing is better informed
- **Phase 3 (planner):** Seed the skill plan directly, bypassing the LLM planning call

The skills in that plan still execute. Phase 5 re-triage decides the final route based on actual outcomes.

### Success Criteria

- Reduce LLM planner invocations (Phase 3) by **20–30%** through pattern-seeded plans
- Maintain **<0.01% false positive rate** in cross-domain pattern application
- Enable **weekly re-validation** of patterns with <15% degradation tolerance
- Keep pattern table size manageable (**<1000 patterns/domain/quarter**)
- Phase 2–5 triage routing accuracy measurable via pattern hit/miss counters

---

## 2. Architecture

### 2.1 Three-Tier Memory Model

```
┌─────────────────────────────────────────────────────┐
│         Working Memory (Session-Scoped)             │
│   TTL: batch lifetime  │  Scope: Current batch only │
│  ┌───────────────────────────────────────────────┐  │
│  │ Patterns auto-created from Phase 5 outcomes  │  │
│  │ High-velocity patterns (3+ hits in batch)    │  │
│  │ Candidate patterns awaiting end-of-batch     │  │
│  │ promotion                                    │  │
│  └───────────────────────────────────────────────┘  │
└─────────────────────────────────────────────────────┘
                        ↓ promote at batch end (≥5 hits, ≥75% success)
┌─────────────────────────────────────────────────────┐
│       Validated Template Memory                      │
│   TTL: 24 hours  │  Scope: Domain-wide             │
│  ┌───────────────────────────────────────────────┐  │
│  │ Patterns with ≥0.75 confidence               │  │
│  │ Seeded at domain init; augmented at runtime  │  │
│  │ Used for Phase 2 triage hint + Phase 3 plan  │  │
│  │ Refreshed weekly via re-validation           │  │
│  └───────────────────────────────────────────────┘  │
└─────────────────────────────────────────────────────┘
                        ↓ demote/archive on degradation
┌─────────────────────────────────────────────────────┐
│      Long-Term Pattern Archive                       │
│   TTL: ∞ (with decay)  │  Scope: Historical        │
│  ┌───────────────────────────────────────────────┐  │
│  │ Patterns with <0.75 confidence or demoted    │  │
│  │ Archived; used for analysis/debugging        │  │
│  │ Temporal decay: 0.95^days_since_success      │  │
│  └───────────────────────────────────────────────┘  │
└─────────────────────────────────────────────────────┘
```

### 2.2 Integration Points in 5-Phase Pipeline

**Phase 1 (Deterministic):** No change. Spell checker, address standardizer, record linker run as-is.

**Phase 2 (Triage) ← ROUTING HINT ONLY**
```
Input: record with initial confidence score from phase 1
├─ Lookup: Find matching patterns in validated template memory
│  └─ Mandatory filters: domain, schema_hash
│  └─ Hybrid retrieval: 50% semantic (pgvector) + 50% keyword (ts_rank)
├─ If pattern found and confidence ≥ 0.75:
│  └─ Apply small triage hint (+0.05 to initial confidence)
│  └─ Store pattern_id in record context for Phase 3
│  └─ Increment pattern hit counter
├─ If no pattern found:
│  └─ Increment pattern miss counter
└─ Route: done / needs_review / unsalvageable (same thresholds as before)

NOTE: Phase 2 does NOT mark records done based on pattern match alone.
      Records only reach "done" after Phase 4 skills have executed and
      Phase 5 re-triage confirms fields are actually filled.
```

**Phase 3 (AI Planner) ← PRIMARY INTEGRATION**
```
Input: record routed to needs_review
├─ If pattern_id present in record context:
│  └─ Inject skill plan directly from pattern.resolution_strategy
│  └─ Skip LLM planner call entirely (primary cost saving)
│  └─ Log: "plan_seeded_from_pattern", pattern_id
├─ If no pattern match:
│  └─ Run LLM planner as normal
│  └─ Optionally enrich prompt: "Gap type X seen N times; skills A,B worked"
└─ Output: ordered skill plan (seeded or LLM-generated)
```

**Phase 4 (Planned Skills):** Execute skill plan. Use semantic cache for address/search result deduplication.

**Phase 5 (Re-Triage):**
```
├─ Score final record (confidence + completeness)
├─ Log outcome to cleaned_records (with pattern_id if used)
├─ If outcome = success AND gap_type identified:
│  └─ Create or increment working-memory pattern for this session
└─ Route: done / needs_review / unsalvageable
```

---

## 3. Pattern Seeding & Creation

### 3.1 Two Sources of Patterns

Patterns enter the system through two paths:

**Path A — Domain Initialization (starts as `validated`)**

During `initialize_domain.py` Phase 3 seed research, the initialization agent:

1. Samples real records from the registered tables (if data is present)
2. Derives likely gap types from actual null/malformed field patterns
3. LLM proposes additional likely gap types based on domain knowledge
4. User confirms or rejects proposed gap types interactively
5. For confirmed types, agent proposes a resolution strategy
6. Confirmed gap type + strategy written to `cleaned_pattern_memory` as `validated`

```
Phase 3 init flow:
  ├─ Sample 100 records → find nulls/anomalies
  │  └─ Derive: missing_postal_code_ca (47 records), bad_province_abbr (12 records)
  ├─ LLM proposes additional likely gaps for this domain:
  │  └─ "ambiguous_municipality_name" — confirm? [Y/n]
  │  └─ "stacked_unit_address" — confirm? [Y/n]
  └─ For each confirmed gap, propose strategy:
     └─ missing_postal_code_ca → [nominatim_geocoder, web_search]? [Y/n/edit]
```

Seeds written with `validation_status = 'validated'`, `session_id = 'init'`.

**Path B — Runtime Learning (starts as `working`)**

When Phase 5 logs a successful outcome and a gap type was identified:

```python
def record_outcome(record, outcome, session_id, domain):
    """Auto-create or increment a working-memory pattern on Phase 5 success."""
    gap_type = record.context.get("gap_type")
    skills_run = record.context.get("skills_executed", [])

    if outcome == "success" and gap_type:
        existing = db.query_one("""
            SELECT id, times_applied, times_failed
            FROM cleaning_pattern_memory
            WHERE domain = %s AND gap_type = %s AND session_id = %s
        """, (domain, gap_type, session_id))

        if existing:
            db.execute("""
                UPDATE cleaning_pattern_memory
                SET times_applied = times_applied + 1,
                    last_applied_at = NOW()
                WHERE id = %s
            """, (existing.id,))
        else:
            db.execute("""
                INSERT INTO cleaning_pattern_memory
                    (domain, gap_type, resolution_strategy, confidence,
                     validation_status, session_id, times_applied)
                VALUES (%s, %s, %s, 0.70, 'working', %s, 1)
            """, (domain, gap_type, json.dumps(skills_run), session_id))

    elif outcome == "failure" and gap_type:
        db.execute("""
            UPDATE cleaning_pattern_memory
            SET times_failed = times_failed + 1
            WHERE domain = %s AND gap_type = %s AND session_id = %s
        """, (domain, gap_type, session_id))
```

---

## 4. Data Model

### 4.1 Schema: `cleaning_pattern_memory`

```sql
CREATE TABLE cleaning_pattern_memory (
    id                      SERIAL PRIMARY KEY,
    domain                  VARCHAR(50) NOT NULL,
    gap_type                VARCHAR(100) NOT NULL,

    -- Pattern definition
    source_context          TEXT NOT NULL,
    resolution_strategy     TEXT NOT NULL,        -- JSON array of skill names
    why_it_worked           TEXT,

    -- Confidence & validation
    confidence              FLOAT NOT NULL CHECK (confidence BETWEEN 0 AND 1),
    validation_status       VARCHAR(20) NOT NULL DEFAULT 'working'
                            CHECK (validation_status IN ('working', 'validated', 'needs_review', 'archived')),
    times_applied           INT DEFAULT 1,
    times_failed            INT DEFAULT 0,

    -- Vector & keyword retrieval
    embedding               vector(1536),
    keywords_text           TEXT,                 -- tsvector-indexed text for ts_rank

    -- Lifecycle
    schema_hash             VARCHAR(64),
    session_id              VARCHAR(50),          -- 'init' for seeded; UUID for runtime batches
    created_at              TIMESTAMP DEFAULT NOW(),
    last_applied_at         TIMESTAMP,
    last_validated_date     TIMESTAMP,
    expires_at              TIMESTAMP,            -- set on promotion; NULL for init-seeded

    -- Audit
    promoted_from_session   VARCHAR(50),
    validation_samples      INT,

    -- Deduplication key uses hash of source_context to avoid TEXT index limits
    source_context_hash     CHAR(64) GENERATED ALWAYS AS
                            (encode(sha256(source_context::bytea), 'hex')) STORED,

    CONSTRAINT domain_gap_source_unique UNIQUE (domain, gap_type, source_context_hash)
);

CREATE INDEX idx_domain_validation ON cleaning_pattern_memory(domain, validation_status);
CREATE INDEX idx_expires_at ON cleaning_pattern_memory(expires_at) WHERE expires_at IS NOT NULL;
CREATE INDEX idx_session ON cleaning_pattern_memory(session_id);
CREATE INDEX idx_embedding ON cleaning_pattern_memory
    USING ivfflat (embedding vector_cosine_ops) WITH (lists = 100);
CREATE INDEX idx_keywords_fts ON cleaning_pattern_memory
    USING GIN (to_tsvector('english', COALESCE(keywords_text, '')));
```

### 4.2 Schema: `pattern_validation_log`

```sql
CREATE TABLE pattern_validation_log (
    id              SERIAL PRIMARY KEY,
    pattern_id      INT NOT NULL REFERENCES cleaning_pattern_memory(id),
    validation_date TIMESTAMP DEFAULT NOW(),

    -- Sampling results
    sample_size         INT,
    success_count       INT,
    observed_confidence FLOAT,
    degradation_pct     FLOAT,

    -- Outcome
    decision    VARCHAR(20) CHECK (decision IN ('keep', 'demote', 'archive')),
    notes       TEXT
);

CREATE INDEX idx_pattern_validation ON pattern_validation_log(pattern_id, validation_date DESC);
```

### 4.3 Schema: `cleaning_batch_session`

```sql
CREATE TABLE cleaning_batch_session (
    id              SERIAL PRIMARY KEY,
    session_id      VARCHAR(50) NOT NULL UNIQUE,
    domain          VARCHAR(50) NOT NULL,
    batch_size      INT,
    started_at      TIMESTAMP DEFAULT NOW(),
    ended_at        TIMESTAMP,
    promoted_count  INT DEFAULT 0,

    -- Pattern match metrics (enables hit/miss rate tracking)
    pattern_hits    INT DEFAULT 0,
    pattern_misses  INT DEFAULT 0
);

CREATE INDEX idx_session_domain ON cleaning_batch_session(session_id, domain);
```

### 4.4 `cleaned_records` Pattern Linkage

The orchestrator's output table must carry `pattern_id` so weekly re-validation can sample real outcomes. Add this column to whatever table Phase 5 writes results to:

```sql
-- Add to your existing cleaned records output table:
ALTER TABLE cleaned_records ADD COLUMN pattern_id INT
    REFERENCES cleaning_pattern_memory(id);
ALTER TABLE cleaned_records ADD COLUMN pattern_used BOOLEAN DEFAULT FALSE;

CREATE INDEX idx_cleaned_records_pattern ON cleaned_records(pattern_id)
    WHERE pattern_id IS NOT NULL;
```

---

## 5. Retrieval Strategy: Hybrid (50% Semantic + 50% Keyword)

### 5.1 Retrieval in SQL

Both the semantic (pgvector `<=>`) and keyword (`ts_rank`) components run in a single SQL query. No Python-side cosine loops; no full-table fetches.

```python
def hybrid_retrieve(query_text: str, domain: str, k: int = 5, alpha: float = 0.5):
    """
    Retrieve top-k patterns using pgvector semantic + PostgreSQL ts_rank fusion.
    Both components run in SQL — no Python-side vector math.
    """
    query_embedding = embed(query_text)  # single embed call per lookup

    rows = db.query("""
        WITH scored AS (
            SELECT
                id,
                confidence,
                validation_status,
                resolution_strategy,
                -- Semantic score: 1 - cosine distance (higher = more similar)
                1 - (embedding <=> %s::vector) AS sem_score,
                -- Keyword score via ts_rank
                ts_rank(
                    to_tsvector('english', COALESCE(keywords_text, '')),
                    plainto_tsquery('english', %s)
                ) AS kw_score
            FROM cleaning_pattern_memory
            WHERE domain = %s
              AND validation_status IN ('validated', 'working')
              AND (schema_hash IS NULL OR schema_hash = %s)
        )
        SELECT
            id,
            confidence,
            resolution_strategy,
            (%s * sem_score + (1 - %s) * kw_score) * confidence AS fused_score
        FROM scored
        WHERE sem_score > 0.60   -- minimum semantic threshold
        ORDER BY fused_score DESC
        LIMIT %s
    """, (query_embedding, query_text, domain, current_schema_hash(domain),
          alpha, alpha, k))

    return rows
```

### 5.2 Mandatory Filtering (Cross-Domain Isolation)

Domain isolation is enforced in the SQL `WHERE` clause, not in post-retrieval Python checks. This prevents any possibility of cross-domain leakage at the query level.

```python
def find_pattern_for_record(record, domain: str, session_id: str):
    """
    Return best matching pattern for this record, or None.
    Domain isolation is in SQL — never relaxed at retrieval time.
    """
    gap_type = classify_gap(record)
    query_text = f"{gap_type} | {record.get('address', '')} | {record.get('details', '')}"

    patterns = hybrid_retrieve(query_text, domain=domain, k=3)

    if not patterns:
        _increment_miss(session_id)
        return None

    best = patterns[0]

    # Only use validated patterns for Phase 2 triage hint.
    # Working patterns are used in Phase 3 planner enrichment only.
    if best.validation_status == 'working' and record.context.get('phase') == 2:
        _increment_miss(session_id)
        return None

    _increment_hit(session_id)
    return best
```

---

## 6. Lifecycle & Validation

### 6.1 Confidence Tiers & Phase Gating

```
Confidence     │ Status      │ Phase 2 hint │ Phase 3 action         │ Notes
───────────────┼─────────────┼──────────────┼────────────────────────┼──────────────────
≥ 0.85         │ validated   │ +0.05        │ Inject plan directly   │ Skip LLM planner
0.75–0.84      │ validated   │ +0.03        │ Inject plan directly   │ Skip LLM planner
0.60–0.74      │ validated   │ 0            │ Enrich planner prompt  │ LLM still runs
0.60–0.84      │ working     │ 0            │ Enrich planner prompt  │ LLM still runs
< 0.60         │ any         │ 0            │ No action              │ Ignore pattern
```

**Phase 2 hint rule:** The small triage confidence adjustment (+0.03–0.05) only shifts borderline records toward a less-expensive route. It never pushes a record to `done`. The `done` threshold remains 0.85 confidence AND 0.80 completeness — both must be met after Phase 4 skills have run.

### 6.2 Promotion: Working → Validated

**Trigger:** End of batch (tied to batch lifetime, not wall-clock TTL)

**Criteria:**
- Pattern applied ≥ 5 times in batch
- Applied to ≥ 3 distinct source addresses (prevents overfitting to a single source cluster)
- Success rate ≥ 0.75: `(times_applied - times_failed) / times_applied`
- No confidence conflict with an existing validated pattern for the same gap type

**What counts as success:** Phase 5 re-triage routes the record to `done` AND all previously-null fields that the skills targeted are now non-null.

```python
def promote_working_patterns(session_id: str, domain: str):
    """Promote high-confidence working patterns at end of batch."""
    working = db.query("""
        SELECT id, times_applied, times_failed, confidence
        FROM cleaning_pattern_memory
        WHERE session_id = %s AND domain = %s AND validation_status = 'working'
    """, (session_id, domain))

    for pattern in working:
        success_rate = (
            (pattern.times_applied - pattern.times_failed) / pattern.times_applied
        )
        distinct_sources = db.query_one("""
            SELECT COUNT(DISTINCT source_address) as n
            FROM cleaned_records
            WHERE pattern_id = %s AND session_id = %s
        """, (pattern.id, session_id)).n

        if pattern.times_applied >= 5 and distinct_sources >= 3 and success_rate >= 0.75:
            db.execute("""
                UPDATE cleaning_pattern_memory
                SET validation_status = 'validated',
                    confidence = %s,
                    expires_at = NOW() + INTERVAL '24 hours',
                    promoted_from_session = %s
                WHERE id = %s
            """, (success_rate, session_id, pattern.id))
            log_event("pattern_promoted", pattern.id, success_rate)
        else:
            # Pattern didn't earn promotion; expire it
            db.execute("""
                UPDATE cleaning_pattern_memory
                SET validation_status = 'archived'
                WHERE id = %s
            """, (pattern.id,))
```

### 6.3 Weekly Re-Validation

**Trigger:** Weekly scheduled job

**Process:**
```python
def weekly_revalidation():
    """Sample 10 records per validated pattern, re-score against actual outcomes."""
    patterns = db.query("""
        SELECT id, gap_type, domain, confidence
        FROM cleaning_pattern_memory
        WHERE validation_status = 'validated'
        AND (last_validated_date IS NULL
             OR last_validated_date < NOW() - INTERVAL '7 days')
    """)

    for pattern in patterns:
        samples = db.query("""
            SELECT outcome
            FROM cleaned_records
            WHERE pattern_id = %s
            ORDER BY cleaned_at DESC
            LIMIT 10
        """, (pattern.id,))

        if not samples:
            continue  # No records used this pattern yet; skip

        observed_confidence = sum(
            1 for s in samples if s.outcome == 'success'
        ) / len(samples)
        degradation = (pattern.confidence - observed_confidence) / pattern.confidence

        if degradation > 0.15:
            decision = 'demote'
            db.execute("""
                UPDATE cleaning_pattern_memory
                SET validation_status = 'needs_review', confidence = %s,
                    last_validated_date = NOW()
                WHERE id = %s
            """, (observed_confidence, pattern.id))
        elif observed_confidence < 0.60:
            decision = 'archive'
            db.execute("""
                UPDATE cleaning_pattern_memory
                SET validation_status = 'archived', last_validated_date = NOW()
                WHERE id = %s
            """, (pattern.id,))
        else:
            decision = 'keep'
            db.execute("""
                UPDATE cleaning_pattern_memory
                SET confidence = %s, last_validated_date = NOW()
                WHERE id = %s
            """, (observed_confidence, pattern.id))

        log_validation(pattern.id, len(samples), observed_confidence, degradation, decision)
```

### 6.4 Temporal Decay (Archive Stale Patterns)

```sql
-- Run as weekly maintenance job alongside re-validation
UPDATE cleaning_pattern_memory
SET validation_status = 'archived'
WHERE validation_status = 'validated'
  AND last_applied_at < NOW() - INTERVAL '90 days'
  AND confidence * POW(
      0.95,
      EXTRACT(EPOCH FROM (NOW() - last_applied_at)) / 86400.0
  ) < 0.60;
```

---

## 7. Configuration

### 7.1 Config YAML: Domain-Specific Settings

**File:** `skills/<domain>/memory.yaml`

```yaml
domain: real_estate

memory:
  # Cross-domain pattern exceptions
  # Reference gap_type strings, not DB integer IDs
  cross_domain_patterns:
    - gap_type: "address_standardization"
      applies_to: ["real_estate", "sports_ticketing"]
      min_confidence: 0.85

    - gap_type: "postal_format_ca"
      applies_to: ["real_estate"]
      min_confidence: 0.75

  # Retrieval tuning
  retrieval:
    hybrid_alpha: 0.5         # 50% semantic, 50% keyword
    top_k: 5
    semantic_threshold: 0.60  # minimum sem_score to include in results

  # Promotion thresholds
  promotion:
    min_times_applied: 5
    min_distinct_sources: 3
    min_success_rate: 0.75

  # TTL & validation
  ttl:
    validated_template_hours: 24
    archive_after_days: 90
    revalidation_cadence: weekly
    degradation_tolerance_pct: 15

  # Embedding
  embedding:
    model: "openai/text-embedding-3-small"
    dimensions: 1536
    batch_size: 100

  # Phase 2 triage hint (never exceeds these values)
  phase2_hint:
    enabled: true
    max_boost: 0.05           # maximum adjustment to initial confidence
    min_pattern_confidence: 0.75
```

### 7.2 Environment Variables

```bash
# Embedding API
OPENROUTER_API_KEY=sk-or-...
EMBEDDING_MODEL=openai/text-embedding-3-small

# Pattern memory thresholds (override YAML if set)
PATTERN_PROMOTION_THRESHOLD=0.75
PATTERN_DEMOTION_THRESHOLD=15      # % degradation before demote
PATTERN_ARCHIVE_AGE_DAYS=90

# Batch settings
EMBEDDING_BATCH_SIZE=100
REVALIDATION_CADENCE=weekly
```

---

## 8. Error Handling & Safety

### 8.1 Embedding Failures

```python
def safe_embed(text: str, pattern_id: int, fallback: str = "keyword_only"):
    """Embed with fallback to keyword-only retrieval."""
    try:
        return embed(text, timeout=15, retries=3)
    except EmbeddingTimeoutError:
        log_error("embedding_timeout", pattern_id)
        return None  # hybrid_retrieve falls back to ts_rank only
    except EmbeddingQuotaExceededError:
        log_error("embedding_quota_exceeded", pattern_id)
        db.execute(
            "UPDATE cleaning_pattern_memory SET needs_embedding = TRUE WHERE id = %s",
            (pattern_id,)
        )
        return None
```

When `embedding` is `None`, the SQL query in Section 5.1 returns 0 for `sem_score` and falls back to keyword-only ranking via `ts_rank`.

### 8.2 Confidence Hint Validation

```python
def apply_triage_hint(initial: float, pattern_confidence: float, max_boost: float = 0.05) -> float:
    """
    Apply pattern-derived triage hint.
    - Never pushes record to 'done' threshold (capped at 0.84)
    - Never lowers initial confidence
    - Boost scales with pattern confidence
    """
    hint = pattern_confidence * max_boost
    boosted = min(initial + hint, 0.84)   # hard cap below 'done' threshold
    boosted = max(boosted, initial)
    return boosted
```

### 8.3 Pattern Safety on Schema Drift

Schema drift invalidates patterns immediately. The SQL `WHERE schema_hash = %s` filter in retrieval handles this automatically — stale patterns simply don't match. The drift detector marks them explicitly for audit visibility:

```python
def detect_schema_drift(domain: str):
    current_hash = hashlib.sha256(
        json.dumps(get_schema(domain), sort_keys=True).encode()
    ).hexdigest()

    stale_count = db.execute("""
        UPDATE cleaning_pattern_memory
        SET validation_status = 'needs_review'
        WHERE domain = %s
          AND schema_hash IS NOT NULL
          AND schema_hash != %s
          AND validation_status = 'validated'
    """, (domain, current_hash)).rowcount

    if stale_count:
        log_event("schema_drift_detected", domain, stale_count)
```

---

## 9. Testing Strategy

### 9.1 Unit Tests

- `test_hybrid_retrieval_ranking.py` — pgvector + ts_rank fusion scores correctly; keyword-only fallback when embedding is None
- `test_mandatory_filtering.py` — domain isolation prevents cross-contamination at SQL level
- `test_triage_hint.py` — hint never pushes record to done threshold; never lowers confidence
- `test_pattern_promotion.py` — requires ≥5 applies, ≥3 distinct sources, ≥75% success rate
- `test_temporal_decay.py` — decay formula correct; stale patterns archive

### 9.2 Integration Tests

- `test_phase2_triage_with_patterns.py` — validated patterns apply hint; working patterns do not affect Phase 2
- `test_phase3_planner_skip.py` — high-confidence patterns bypass LLM planner; plan is injected correctly
- `test_phase3_planner_enrich.py` — medium-confidence patterns enrich LLM prompt but don't bypass it
- `test_revalidation_pipeline.py` — weekly re-validation demotes degraded patterns
- `test_schema_drift_detection.py` — schema changes invalidate and flag stale patterns
- `test_pattern_creation_from_phase5.py` — successful Phase 5 outcomes create working patterns

### 9.3 End-to-End Tests

- `test_full_batch_with_pattern_memory.py` — full pipeline with memory; Phase 3 skip rate measurable via session hit/miss counters
- `test_cross_domain_isolation_e2e.py` — patterns never leak across domains
- `test_pattern_promotion_after_batch.py` — working patterns promote at batch end with distinct-source check
- `test_init_seeding_flow.py` — domain init seeds patterns from data + LLM proposals; user confirmation gates promotion

---

## 10. Success Metrics & Monitoring

### 10.1 Metrics to Track

| Metric | Target | How Measured |
|--------|--------|--------------|
| Phase 3 LLM planner skips | 20–30% reduction | `cleaning_batch_session.pattern_hits` |
| Pattern hit rate | Baseline → track trend | `pattern_hits / (pattern_hits + pattern_misses)` |
| Cross-domain false positives | <0.01% | SQL audit: `pattern.domain != record.domain` in cleaned_records |
| Weekly revalidation degradation | <15% | `pattern_validation_log.degradation_pct` |
| Pattern promotion success rate | >75% | Promoted vs. expired working patterns per batch |
| Phase 5 records reaching done | +5–10% | Compare done% before/after pattern memory enabled |

The 20–30% Phase 3 reduction target is now measurable: hit counter tracks how many times patterns bypassed the planner; miss counter tracks how often they didn't.

### 10.2 Monitoring Queries

```sql
-- Pattern health by domain
SELECT
    domain,
    validation_status,
    COUNT(*) as count,
    AVG(confidence) as avg_confidence
FROM cleaning_pattern_memory
GROUP BY domain, validation_status;

-- Hit/miss rate per batch session
SELECT
    session_id,
    domain,
    pattern_hits,
    pattern_misses,
    ROUND(
        pattern_hits::numeric / NULLIF(pattern_hits + pattern_misses, 0) * 100,
        1
    ) as hit_rate_pct
FROM cleaning_batch_session
ORDER BY started_at DESC;

-- Revalidation trends per pattern
SELECT
    pattern_id,
    validation_date,
    observed_confidence,
    observed_confidence - LAG(observed_confidence)
        OVER (PARTITION BY pattern_id ORDER BY validation_date) as confidence_delta,
    decision
FROM pattern_validation_log
ORDER BY pattern_id, validation_date;

-- Patterns nearing archive threshold
SELECT
    id,
    domain,
    gap_type,
    confidence,
    last_applied_at,
    EXTRACT(DAY FROM NOW() - last_applied_at) as days_idle
FROM cleaning_pattern_memory
WHERE validation_status = 'validated'
  AND last_applied_at < NOW() - INTERVAL '60 days'
ORDER BY last_applied_at;
```

---

## 11. Rollout & Migration Path

### Phase A (Week 1): Foundation
- [ ] Create `cleaning_pattern_memory`, `pattern_validation_log`, `cleaning_batch_session` tables
- [ ] Add `pattern_id` column to `cleaned_records`
- [ ] Implement hybrid retrieval (pgvector + ts_rank in SQL)
- [ ] Add mandatory domain filter to retrieval

### Phase B (Week 2): Phase 3 Integration (Primary Value)
- [ ] Wire pattern lookup into Phase 3 planner: skip LLM call when pattern confidence ≥ 0.75
- [ ] Enrich LLM planner prompt for medium-confidence patterns (0.60–0.74)
- [ ] Wire Phase 5 outcome logging → working pattern creation
- [ ] Add hit/miss counters to batch session

### Phase C (Week 3): Phase 2 Triage Hint
- [ ] Integrate small triage hint into Phase 2 (validated patterns only)
- [ ] Enforce hard cap at 0.84 (never pushes to done)
- [ ] Validate triage routing accuracy improves

### Phase D (Week 4): Lifecycle & Validation
- [ ] Implement batch-end promotion with distinct-source check
- [ ] Add weekly re-validation job
- [ ] Enable temporal decay & archival

### Phase E (Week 5): Init Seeding
- [ ] Wire pattern proposal into `initialize_domain.py` Phase 3
- [ ] Data-derived gap detection from null/anomaly sampling
- [ ] LLM-proposed gap types with interactive user confirmation
- [ ] Confirmed patterns written as `validated` with `session_id = 'init'`

### Phase F (Week 6+): Optimization & Monitoring
- [ ] Add schema drift detection
- [ ] Build monitoring dashboard queries
- [ ] Tune retrieval thresholds from real hit/miss data

---

## 12. Open Questions & Future Work

1. **Skill-to-skill communication (Working Context):** Deferred. The `session_id` tracking supports it; implementation TBD after Phase B proves value.

2. **Semantic cache integration:** Address/search result deduplication deferred. Schema exists in `db/pg_vector.py`; integration with orchestrator TBD.

3. **Cross-domain learning policy:** Config YAML exceptions exist per domain. Determining safe cross-domain gap types (e.g., address standardization applying to both real estate and sports ticketing) requires domain expert sign-off per pair.

4. **Data annotation layer:** Out of scope for this spec. A follow-on spec will cover labeling records with entity types, quality scores, and training-ready metadata — the pattern memory here produces the confidence signals that annotation will consume.

---

## Appendix: Example Flow

**Record:** `{ address: "123 King St, Toronto, ON", postal_code: NULL, country: "CA" }`

**Phase 1:** Deterministic skills run, address standardized to `"123 King Street, Toronto, ON"`, postal still NULL. Completeness: 0.78. Confidence: 0.72.

**Phase 2 (with pattern memory):**
```
Initial confidence: 0.72 (MEDIUM → needs_review)

Pattern lookup:
  Query: "missing_postal_code_ca | 123 King Street Toronto ON"
  Domain: real_estate
  Hybrid retrieval → Pattern #42 matches (fused_score: 0.91)
    gap_type: "missing_postal_code_ca"
    confidence: 0.87 (validated)
    resolution_strategy: ["nominatim_geocoder", "web_search"]

Triage hint: 0.72 + (0.87 × 0.05) = 0.764 (still needs_review — working as intended)
Route: needs_review (no change; Phase 3 will run)
Record context: { pattern_id: 42, gap_type: "missing_postal_code_ca" }
```

**Phase 3 (with pattern):**
```
Pattern found in context (confidence 0.87 ≥ 0.75)
→ Skip LLM planner
→ Inject plan: [nominatim_geocoder, web_search]
Cost: $0.00 (vs. $0.01–0.03 for LLM planner)
```

**Phase 4:**
```
nominatim_geocoder → returns postal_code: "M5H 2N2"
web_search → confirms postal, adds FSA context
Record now: { ..., postal_code: "M5H 2N2" }
```

**Phase 5:**
```
Completeness: 0.96 (all fields filled)
Confidence: 0.91 (HIGH)
Route: done ✓

Log outcome: success, pattern_id=42
→ Pattern #42 times_applied++, last_applied_at = NOW()
```

**Result:** LLM planner skipped (cost saving). Record correctly marked done only after postal code was actually filled by Phase 4 skills.
