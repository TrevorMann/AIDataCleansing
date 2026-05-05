CREATE TABLE IF NOT EXISTS spell_corrections (
    wrong TEXT NOT NULL,
    domain TEXT NOT NULL,
    right TEXT NOT NULL,
    source TEXT NOT NULL DEFAULT 'manual_seed',
    confidence REAL NOT NULL DEFAULT 1.0,
    added_at TIMESTAMPTZ DEFAULT NOW(),
    PRIMARY KEY (wrong, domain)
);

CREATE INDEX IF NOT EXISTS idx_spell_corr_domain ON spell_corrections(domain);
