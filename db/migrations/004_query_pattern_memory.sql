CREATE TABLE IF NOT EXISTS query_pattern_memory (
    id SERIAL PRIMARY KEY,
    domain TEXT NOT NULL,
    gap_type TEXT NOT NULL,
    query_template TEXT NOT NULL,
    success_count INT NOT NULL DEFAULT 0,
    failure_count INT NOT NULL DEFAULT 0,
    last_used_at TIMESTAMPTZ,
    sample_resolution JSONB,
    UNIQUE (domain, gap_type, query_template)
);

CREATE INDEX IF NOT EXISTS idx_qpm_domain_gap ON query_pattern_memory(domain, gap_type);

CREATE TABLE IF NOT EXISTS source_registry (
    domain_key TEXT NOT NULL,
    url_host TEXT NOT NULL,
    trust_score REAL NOT NULL DEFAULT 0.5,
    success_count INT NOT NULL DEFAULT 0,
    failure_count INT NOT NULL DEFAULT 0,
    license_notes TEXT,
    PRIMARY KEY (domain_key, url_host)
);
