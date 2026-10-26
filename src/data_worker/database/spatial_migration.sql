-- Additive, idempotent serving provenance migration. Run before the updated cache API.
ALTER TABLE pollution.edge_pollution
  ADD COLUMN IF NOT EXISTS model_metadata JSONB NOT NULL DEFAULT '{}'::jsonb;
ALTER TABLE pollution.edge_pollution
  DROP CONSTRAINT IF EXISTS ck_edge_pollution_estimation_source;
ALTER TABLE pollution.edge_pollution
  ADD CONSTRAINT ck_edge_pollution_estimation_source
  CHECK (estimation_source IN ('stgnn', 'idw', 'hybrid'));
