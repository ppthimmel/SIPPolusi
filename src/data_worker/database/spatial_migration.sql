-- Additive, idempotent serving provenance migration. Run before the updated cache API.
ALTER TABLE pollution.edge_pollution
  ADD COLUMN IF NOT EXISTS model_metadata JSONB NOT NULL DEFAULT '{}'::jsonb;
ALTER TABLE pollution.edge_pollution
  DROP CONSTRAINT IF EXISTS ck_edge_pollution_estimation_source;
ALTER TABLE pollution.edge_pollution
  ADD CONSTRAINT ck_edge_pollution_estimation_source
  CHECK (estimation_source IN ('stgnn', 'idw', 'hybrid'));

-- Durable lineage, retained for the same lifetime as the cached time window.
CREATE TABLE IF NOT EXISTS pollution.inference_run (
  run_id TEXT PRIMARY KEY,
  time_window_start TIMESTAMPTZ NOT NULL REFERENCES pollution.pollution_window(time_window_start) ON DELETE CASCADE,
  manifest JSONB NOT NULL,
  trace_sha256 TEXT NOT NULL,
  created_at TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE INDEX IF NOT EXISTS inference_run_window_idx ON pollution.inference_run(time_window_start);
CREATE TABLE IF NOT EXISTS pollution.grid_prediction (
  run_id TEXT NOT NULL REFERENCES pollution.inference_run(run_id) ON DELETE CASCADE,
  cell_id BIGINT NOT NULL,
  x DOUBLE PRECISION NOT NULL,
  y DOUBLE PRECISION NOT NULL,
  pm25_ugm3 DOUBLE PRECISION,
  no2_ugm3 DOUBLE PRECISION,
  confidence_score DOUBLE PRECISION,
  pm25_source TEXT NOT NULL,
  no2_source TEXT NOT NULL,
  PRIMARY KEY (run_id, cell_id)
);
CREATE TABLE IF NOT EXISTS pollution.edge_cell (
  run_id TEXT NOT NULL,
  edge_id BIGINT NOT NULL,
  cell_id BIGINT NOT NULL,
  length_m DOUBLE PRECISION NOT NULL CHECK (length_m > 0),
  PRIMARY KEY (run_id, edge_id, cell_id),
  FOREIGN KEY (run_id, cell_id) REFERENCES pollution.grid_prediction(run_id, cell_id) ON DELETE CASCADE
);
ALTER TABLE pollution.edge_pollution
  ADD COLUMN IF NOT EXISTS trace_run_id TEXT REFERENCES pollution.inference_run(run_id);
-- Every foreign key needs an index led by its own columns. ON DELETE CASCADE from
-- grid_prediction finds edge_cell rows by (run_id, cell_id), and deleting an
-- inference_run checks edge_pollution.trace_run_id. Without these, pruning one
-- window scanned edge_cell once per grid cell and ran for hours (111k cells,
-- 1.7M edge_cell rows), so the hourly cycle fell behind.
CREATE INDEX IF NOT EXISTS edge_cell_run_cell_idx ON pollution.edge_cell (run_id, cell_id);
CREATE INDEX IF NOT EXISTS edge_pollution_trace_run_idx ON pollution.edge_pollution (trace_run_id);
