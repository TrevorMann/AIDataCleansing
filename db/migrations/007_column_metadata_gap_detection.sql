-- db/migrations/007_column_metadata_gap_detection.sql
-- Per-field gap detection config (see 2026-06-15-gap-type-vocabulary-design.md).
ALTER TABLE column_metadata
  ADD COLUMN IF NOT EXISTS gap_detection JSONB DEFAULT NULL;
