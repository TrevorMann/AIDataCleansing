CREATE TABLE IF NOT EXISTS plan_cache (
    signature TEXT PRIMARY KEY,
    domain TEXT NOT NULL,
    plan JSONB NOT NULL,
    reasoning TEXT,
    created_at TIMESTAMPTZ DEFAULT NOW(),
    expires_at TIMESTAMPTZ NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_plan_cache_expires ON plan_cache(expires_at);
CREATE INDEX IF NOT EXISTS idx_plan_cache_domain ON plan_cache(domain);
