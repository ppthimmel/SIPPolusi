"""Mandatory, transactional grid-to-road lineage stored with the pollution cache."""
from dataclasses import dataclass
import hashlib
import json
import re

import numpy as np
import pandas as pd
from psycopg import sql

from spatial_model.data.grid import GridSpec
from .aggregate import aggregate_grid_to_edges
from .confidence import IDW_CAP, DEFAULT_LENGTH_SCALE_M

GRID_COLUMNS = ("cell_id", "x", "y", "pm25_ugm3", "no2_ugm3", "confidence_score", "pm25_source", "no2_source")
TRACE_VERSION = 1
CONFIDENCE_POLICY = dict(status="provisional", cap=IDW_CAP, length_scale_m=DEFAULT_LENGTH_SCALE_M)


def _json(value):
    return json.dumps(value, sort_keys=True, allow_nan=False, default=str)


def _table(schema, name):
    return sql.Identifier(schema, name)


@dataclass
class InferenceTrace:
    run_id: str
    grid: pd.DataFrame
    pieces: pd.DataFrame
    grid_spec: dict
    input_fingerprint: str
    model_metadata: dict
    settings: dict

    def prepare(self, weights):
        """Reject incomplete or inconsistent lineage before touching the database."""
        if not self.run_id or not re.fullmatch(r"[0-9a-f]{64}", self.input_fingerprint):
            raise ValueError("Trace requires a run ID and SHA-256 input fingerprint")
        spec = GridSpec(**self.grid_spec)
        grid = self.grid.copy()
        for pollutant in ("pm25", "no2"):
            column = f"{pollutant}_source"
            if column not in grid:
                raise ValueError(f"Trace missing {column}")
        grid = grid[list(GRID_COLUMNS)].sort_values("cell_id").reset_index(drop=True)
        if grid.cell_id.duplicated().any() or not np.array_equal(grid.cell_id, np.arange(spec.n_cells)):
            raise ValueError("Trace must contain the complete canonical grid")
        x, y = spec.cell_centers()
        if not (np.allclose(grid.x, x) and np.allclose(grid.y, y)):
            raise ValueError("Trace grid coordinates disagree with GridSpec")
        numeric = grid[["pm25_ugm3", "no2_ugm3", "confidence_score"]].to_numpy(float)
        if np.isinf(numeric).any() or (numeric[np.isfinite(numeric)] < 0).any():
            raise ValueError("Invalid traced concentration/confidence")
        pieces = self.pieces.loc[self.pieces.edge_id.isin(weights.edge_id), ["edge_id", "cell_id", "length_m"]].copy()
        pieces = pieces.sort_values(["edge_id", "cell_id"]).reset_index(drop=True)
        if (pieces.duplicated(["edge_id", "cell_id"]).any()
                or not pieces.cell_id.isin(grid.cell_id).all()
                or not np.isfinite(pieces.length_m).all() or (pieces.length_m <= 0).any()):
            raise ValueError("Invalid traced road-cell intersections")
        reconstructed, _ = aggregate_grid_to_edges(grid, pieces)
        expected = weights.sort_values("edge_id").reset_index(drop=True)
        actual = reconstructed.sort_values("edge_id").reset_index(drop=True)
        if not np.array_equal(expected.edge_id, actual.edge_id):
            raise ValueError("Every cached edge requires supported source cells")
        for column in ("pm25_ugm3", "no2_ugm3", "exposure_index", "confidence_score"):
            if not np.allclose(expected[column].to_numpy(float), actual[column].to_numpy(float),
                               rtol=1e-7, atol=1e-9, equal_nan=True):
                raise ValueError(f"Cached {column} cannot be reconstructed from trace")
        manifest = dict(format_version=TRACE_VERSION, grid_spec=self.grid_spec, input_fingerprint=self.input_fingerprint,
                        model_metadata=self.model_metadata, settings=self.settings, unit="ug/m3",
                        confidence=CONFIDENCE_POLICY,
                        aggregation="sum(length_m * concentration) / sum(length_m) over finite values",
                        grid_sha256=hashlib.sha256(pd.util.hash_pandas_object(grid, index=False).values.tobytes()).hexdigest(),
                        pieces_sha256=hashlib.sha256(pd.util.hash_pandas_object(pieces, index=False).values.tobytes()).hexdigest())
        return grid, pieces, manifest


def write_trace(conn, trace, prepared, window, graph_version, model_version, schema):
    """Called inside the cache transaction, after serializing writers for this window."""
    grid, pieces, manifest = prepared
    manifest = dict(manifest, time_window_start=window.start.isoformat(), time_window_end=window.end.isoformat(),
                    graph_version=graph_version, model_version=model_version)
    digest = hashlib.sha256(_json(manifest).encode()).hexdigest()
    table = _table(schema, "inference_run")
    inserted = conn.execute(sql.SQL("""INSERT INTO {} (run_id, time_window_start, manifest, trace_sha256)
        VALUES (%s, %s, %s::jsonb, %s) ON CONFLICT (run_id) DO NOTHING RETURNING run_id""").format(table),
        (trace.run_id, window.start, _json(manifest), digest)).fetchone()
    if inserted is None:
        old = conn.execute(sql.SQL("SELECT trace_sha256 FROM {} WHERE run_id = %s").format(table),
                           (trace.run_id,)).fetchone()
        old_hash = old["trace_sha256"] if isinstance(old, dict) else old[0]
        if old_hash != digest:
            raise ValueError("Immutable inference run ID already has different lineage")
        return
    def nullable(value):
        if pd.isna(value):
            return None
        return value.item() if isinstance(value, np.generic) else value
    with conn.cursor().copy(sql.SQL("COPY {} (run_id, {}) FROM STDIN").format(
            _table(schema, "grid_prediction"), sql.SQL(", ").join(map(sql.Identifier, GRID_COLUMNS)))) as copy:
        for row in grid.itertuples(index=False, name=None):
            copy.write_row([trace.run_id, *(nullable(v) for v in row)])
    with conn.cursor().copy(sql.SQL("COPY {} (run_id, edge_id, cell_id, length_m) FROM STDIN").format(
            _table(schema, "edge_cell"))) as copy:
        for row in pieces.itertuples(index=False, name=None):
            copy.write_row([trace.run_id, *(nullable(v) for v in row)])


def read_edge_trace(conn, edge_id, window_start, schema="pollution"):
    """Return the persisted cells and lengths for a completed cached edge; None for legacy rows."""
    row = conn.execute(sql.SQL("""SELECT r.run_id, r.manifest, r.trace_sha256 FROM {edge} e
        JOIN {window} w USING (time_window_start)
        JOIN {run} r ON r.run_id = e.trace_run_id
        WHERE e.edge_id = %s AND e.time_window_start = %s AND w.status = 'complete'""").format(
            edge=_table(schema, "edge_pollution"), window=_table(schema, "pollution_window"),
            run=_table(schema, "inference_run")), (edge_id, window_start)).fetchone()
    if row is None:
        return None
    run_id, manifest, digest = (row[k] for k in ("run_id", "manifest", "trace_sha256")) if isinstance(row, dict) else row
    cur = conn.execute(sql.SQL("""SELECT c.edge_id, c.length_m, g.* FROM {pieces} c
        JOIN {grid} g USING (run_id, cell_id) WHERE c.run_id = %s AND c.edge_id = %s ORDER BY c.cell_id""").format(
            pieces=_table(schema, "edge_cell"), grid=_table(schema, "grid_prediction")), (run_id, edge_id))
    names = [column.name for column in cur.description]
    cells = [r if isinstance(r, dict) else dict(zip(names, r)) for r in cur.fetchall()]
    return dict(run_id=run_id, manifest=manifest, trace_sha256=digest, cells=cells)
