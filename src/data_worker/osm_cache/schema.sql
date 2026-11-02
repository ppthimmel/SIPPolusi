-- DDL lapisan cache: schema osm (OSM Cache DB) dan schema pollution (Spatial Pollution Cache DB).
--
-- Salinan struktur basis data Railway per 2026-10-13: osm dari handoff_database.md bagian 12
-- ditambah index trigram, pollution dari information_schema/pg_indexes basis data asli.
-- __OSM__ dan __POLLUTION__ diganti nama schema oleh osm_cache.schema_sql(), sehingga uji
-- dapat membuat salinan terisolasi. Dipakai untuk uji dan basis data lokal baru, bukan migrasi
-- basis data produksi.

CREATE EXTENSION IF NOT EXISTS postgis;
CREATE EXTENSION IF NOT EXISTS pg_trgm;

CREATE SCHEMA IF NOT EXISTS __OSM__;

CREATE TABLE __OSM__.graph_version (
    version text NOT NULL,
    status text NOT NULL,
    osm_timestamp timestamp with time zone,
    built_at timestamp with time zone,
    node_count integer,
    edge_count integer,
    CONSTRAINT graph_version_pkey PRIMARY KEY (version),
    CONSTRAINT ck_graph_version_status CHECK (status = ANY (ARRAY['building'::text, 'active'::text, 'retired'::text]))
);
CREATE UNIQUE INDEX graph_version_single_active_idx ON __OSM__.graph_version USING btree (status)
    WHERE (status = 'active'::text);

CREATE TABLE __OSM__.road_node (
    node_id bigint NOT NULL,
    graph_version text NOT NULL,
    geom public.geometry(Point, 4326),
    tags jsonb DEFAULT '{}'::jsonb NOT NULL,
    CONSTRAINT road_node_pkey PRIMARY KEY (node_id, graph_version)
);
CREATE INDEX idx_road_node_geom ON __OSM__.road_node USING gist (geom);

CREATE TABLE __OSM__.road_edge (
    edge_id bigint NOT NULL,
    graph_version text NOT NULL,
    u bigint NOT NULL,
    v bigint NOT NULL,
    length_m real,
    highway text,
    walk_allowed boolean DEFAULT true NOT NULL,
    bike_allowed boolean DEFAULT true NOT NULL,
    geom public.geometry(LineString, 4326),
    osm_way_id bigint,
    name text,
    oneway boolean DEFAULT false NOT NULL,
    tags jsonb DEFAULT '{}'::jsonb NOT NULL,
    CONSTRAINT road_edge_pkey PRIMARY KEY (edge_id, graph_version)
);
CREATE INDEX idx_road_edge_geom ON __OSM__.road_edge USING gist (geom);
CREATE INDEX idx_road_edge_osm_way_id ON __OSM__.road_edge USING btree (osm_way_id);
CREATE INDEX idx_road_edge_u ON __OSM__.road_edge USING btree (graph_version, u);
CREATE INDEX idx_road_edge_v ON __OSM__.road_edge USING btree (graph_version, v);
CREATE INDEX idx_road_edge_name_trgm ON __OSM__.road_edge USING gin (name gin_trgm_ops);
-- edge_id unik secara global (pollution.edge_pollution hanya merujuk edge_id). Dibuat di
-- Railway pada 2026-10-13.
CREATE UNIQUE INDEX road_edge_edge_id_key ON __OSM__.road_edge USING btree (edge_id);

CREATE SCHEMA IF NOT EXISTS __POLLUTION__;

CREATE TABLE __POLLUTION__.edge_pollution (
    edge_id bigint NOT NULL,
    time_window_start timestamp with time zone NOT NULL,
    pm25_ugm3 real,
    no2_ugm3 real,
    exposure_index real,
    confidence_score real,
    background_source text,
    estimation_source text,
    model_metadata jsonb NOT NULL DEFAULT '{}'::jsonb,
    graph_version text,
    model_version text,
    geom public.geometry(LineString, 4326),
    written_at timestamp with time zone DEFAULT now() NOT NULL,
    CONSTRAINT edge_pollution_pkey PRIMARY KEY (edge_id, time_window_start),
    CONSTRAINT ck_edge_pollution_background_source CHECK (background_source = ANY (ARRAY['aod'::text, 'reanalysis'::text])),
    CONSTRAINT ck_edge_pollution_estimation_source CHECK (estimation_source = ANY (ARRAY['stgnn'::text, 'idw'::text, 'hybrid'::text]))
);
CREATE INDEX idx_edge_pollution_geom ON __POLLUTION__.edge_pollution USING gist (geom);

CREATE TABLE __POLLUTION__.pollution_window (
    time_window_start timestamp with time zone NOT NULL,
    pollutant text,
    status text NOT NULL,
    coverage_ratio real,
    model_version text,
    completed_at timestamp with time zone,
    CONSTRAINT pollution_window_pkey PRIMARY KEY (time_window_start),
    CONSTRAINT ck_pollution_window_status CHECK (status = ANY (ARRAY['writing'::text, 'complete'::text]))
);

-- Durable lineage, retained for the same lifetime as the cached time window.
CREATE TABLE IF NOT EXISTS __POLLUTION__.inference_run (
  run_id TEXT PRIMARY KEY,
  time_window_start TIMESTAMPTZ NOT NULL REFERENCES __POLLUTION__.pollution_window(time_window_start) ON DELETE CASCADE,
  manifest JSONB NOT NULL,
  trace_sha256 TEXT NOT NULL,
  created_at TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE INDEX IF NOT EXISTS inference_run_window_idx ON __POLLUTION__.inference_run(time_window_start);
CREATE TABLE IF NOT EXISTS __POLLUTION__.grid_prediction (
  run_id TEXT NOT NULL REFERENCES __POLLUTION__.inference_run(run_id) ON DELETE CASCADE,
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
CREATE TABLE IF NOT EXISTS __POLLUTION__.edge_cell (
  run_id TEXT NOT NULL,
  edge_id BIGINT NOT NULL,
  cell_id BIGINT NOT NULL,
  length_m DOUBLE PRECISION NOT NULL CHECK (length_m > 0),
  PRIMARY KEY (run_id, edge_id, cell_id),
  FOREIGN KEY (run_id, cell_id) REFERENCES __POLLUTION__.grid_prediction(run_id, cell_id) ON DELETE CASCADE
);
ALTER TABLE __POLLUTION__.edge_pollution
  ADD COLUMN IF NOT EXISTS trace_run_id TEXT REFERENCES __POLLUTION__.inference_run(run_id);
-- Every foreign key needs an index led by its own columns. ON DELETE CASCADE from
-- grid_prediction finds edge_cell rows by (run_id, cell_id), and deleting an
-- inference_run checks edge_pollution.trace_run_id. Without these, pruning one
-- window scanned edge_cell once per grid cell and ran for hours (111k cells,
-- 1.7M edge_cell rows), so the hourly cycle fell behind.
CREATE INDEX IF NOT EXISTS edge_cell_run_cell_idx ON __POLLUTION__.edge_cell (run_id, cell_id);
CREATE INDEX IF NOT EXISTS edge_pollution_trace_run_idx ON __POLLUTION__.edge_pollution (trace_run_id);
