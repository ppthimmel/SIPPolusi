"""Registered PM2.5 IDW correction and NO2 LightGBM → grid → EdgeWeight."""

from __future__ import annotations

import datetime as dt
import hashlib
import json
import logging
import pathlib
import time

import numpy as np
import pandas as pd

from contracts import JAKARTA_BBOX, EdgeWeight, RunSummary, TimeWindow
from spatial_model.inference.aggregate import aggregate_grid_to_edges, edge_cell_pieces, grid_spec_id
from spatial_model.inference.confidence import confidence_distribution, estimate_confidence
from spatial_model.data.grid import GridSpec
from spatial_model.models.idw import idw_model_version
from spatial_model.configs import load_idw_settings
from spatial_model.data.observations import load_ground_truth_window

log = logging.getLogger("spatial_model.inference")

# ------------------------------------------------------------------ inferensi


def _sha256_frame(df: pd.DataFrame) -> str:
    return hashlib.sha256(pd.util.hash_pandas_object(df.reset_index(drop=True), index=False)
                          .to_numpy().tobytes()).hexdigest()


def make_run_id(time_window: TimeWindow, model_version: str) -> str:
    """run_id deterministik dari (time_window_start, model_version) (IDownscaleInference)."""
    digest = hashlib.sha256(f"{time_window.start.isoformat()}|{model_version}".encode()).hexdigest()[:8]
    return f"{time_window.start:%Y%m%dT%H%MZ}-{model_version}-{digest}"


def to_edge_weights(weights: pd.DataFrame, time_window: TimeWindow) -> list[EdgeWeight]:
    """Baris DataFrame menjadi list[EdgeWeight] (Tabel 3.3)."""
    def val(v):
        return None if v is None or (isinstance(v, float) and not np.isfinite(v)) else float(v)

    return [
        EdgeWeight(edge_id=int(r.edge_id), time_window_start=time_window.start, pm25_ugm3=val(r.pm25_ugm3),
                   no2_ugm3=val(r.no2_ugm3), exposure_index=val(r.exposure_index),
                   confidence_score=val(r.confidence_score), background_source=r.background_source,
                   estimation_source=r.estimation_source, model_metadata=getattr(r, "model_metadata", {}),
                   trace_run_id=getattr(r, "trace_run_id", None))
        for r in weights.itertuples(index=False)
    ]


def run_downscale_inference(
    time_window: TimeWindow,
    *,
    ground_truth: pd.DataFrame,
    edges,
    graph_version: str,
    grid_spec: GridSpec | None = None,
    conn=None,
    artifact_dir: pathlib.Path | None = None,
    settings: dict | None = None,
    force: bool = False,
    pieces_cache_dir: pathlib.Path | None = None,
    pollution_schema: str = "pollution",
    osm_schema: str = "osm",
    inputs=None,
    resolver=None,
    model_versions: dict[str, str] | None = None,
) -> RunSummary:
    """Satu eksekusi inferensi untuk ``time_window`` (penangan IDownscaleInference).

    ``edges`` adalah (edge_ids, coords_lonlat, coord_edge_index) dari
    ``cache.read_road_edges``. ``conn`` (psycopg) bila diberikan dipakai untuk
    memeriksa idempotensi dan menulis cache. ``artifact_dir`` bila diberikan
    menerima artefak penelusuran per run.
    """
    from spatial_model.inference import cache

    started = time.perf_counter()
    settings = settings or load_idw_settings()
    spec = grid_spec or GridSpec.from_bbox(JAKARTA_BBOX.as_tuple(), settings["cell_m"])
    timings: dict[str, float] = {}

    from spatial_model.inference.runtime import infer_registered
    grid, model_metadata, resolved_version, input_fingerprint = infer_registered(
        time_window, ground_truth, spec, settings, inputs=inputs, resolver=resolver, versions=model_versions)
    from .provenance import TRACE_VERSION, CONFIDENCE_POLICY
    policy_identity = json.dumps(dict(graph=graph_version, trace_version=TRACE_VERSION,
                                      confidence=CONFIDENCE_POLICY), sort_keys=True)
    resolved_version += "-" + hashlib.sha256(policy_identity.encode()).hexdigest()[:12]
    estimation_source = "idw" if all(m["source"] == "idw" for m in model_metadata.values()) else "hybrid"
    background_source = None
    reasons = {p: m["fallback_reason"] for p, m in model_metadata.items() if m.get("fallback_reason")}
    fallback_reason = json.dumps(reasons) if reasons else None
    run_id = make_run_id(time_window, resolved_version)

    if conn is not None and not force:
        record = cache.window_record(conn, time_window.start, pollution_schema)
        if (record and record["status"] == "complete" and record["model_version"] == resolved_version
                and not record["has_legacy_rows"]):
            log.info("time window %s sudah complete dengan %s; dilewati", time_window, resolved_version)
            return RunSummary(run_id=run_id, status="skipped", time_window_start=time_window.start,
                              model_version=resolved_version, estimation_source=estimation_source,
                              coverage_ratio=record.get("coverage_ratio"), graph_version=graph_version,
                              duration_s=round(time.perf_counter() - started, 3),
                              fallback_reason=fallback_reason, model_metadata=model_metadata,
                              input_fingerprint=input_fingerprint)

    t = time.perf_counter()
    # Conservative provisional confidence for both models and fallback.
    grid["confidence_score"] = estimate_confidence(grid["nearest_station_m"].to_numpy(), estimation_source="idw")
    timings["grid_s"] = time.perf_counter() - t

    t = time.perf_counter()
    edge_ids, coords, index = edges
    pieces, edge_table = _pieces(edge_ids, coords, index, spec, graph_version, pieces_cache_dir)
    weights, missing = aggregate_grid_to_edges(grid, pieces, edge_table)
    weights["background_source"] = background_source
    weights["estimation_source"] = estimation_source
    # Provenance follows the cells each road actually traverses, including partial fallback.
    road_metadata = {int(edge): {p: dict(value) for p,value in model_metadata.items()} for edge in weights.edge_id}
    for pollutant in ("pm25", "no2"):
        joined = pieces.merge(grid[["cell_id", f"{pollutant}_source"]], on="cell_id", how="left")
        for edge, source_rows in joined.groupby("edge_id"):
            if int(edge) not in road_metadata:
                continue
            sources = sorted(source_rows[f"{pollutant}_source"].dropna().unique())
            item = road_metadata[int(edge)][pollutant]
            item["source"] = sources[0] if len(sources) == 1 else "mixed"
            item["cell_sources"] = sources
    weights["model_metadata"] = [road_metadata[int(edge)] for edge in weights.edge_id]
    weights["trace_run_id"] = run_id
    timings["aggregate_s"] = time.perf_counter() - t
    coverage = len(weights) / len(edge_table) if len(edge_table) else 0.0

    rows_written = 0
    if conn is not None:
        t = time.perf_counter()
        from .provenance import InferenceTrace
        trace = InferenceTrace(run_id, grid, pieces, spec.as_dict(), input_fingerprint, model_metadata, settings)
        rows_written = cache.write_pollution_weight(conn, weights, time_window, graph_version, resolved_version,
                                                    coverage_ratio=coverage, pollution_schema=pollution_schema,
                                                    osm_schema=osm_schema, trace=trace)
        timings["write_cache_s"] = time.perf_counter() - t

    summary = RunSummary(
        run_id=run_id, status="complete", time_window_start=time_window.start, model_version=resolved_version,
        estimation_source=estimation_source, coverage_ratio=round(coverage, 6),
        confidence_distribution=confidence_distribution(weights["confidence_score"]),
        graph_version=graph_version, rows_written=rows_written, fallback_reason=fallback_reason,
        model_metadata=model_metadata, input_fingerprint=input_fingerprint,
    )
    if artifact_dir is not None:
        summary.artifacts = _write_artifacts(pathlib.Path(artifact_dir), summary, time_window, spec, ground_truth,
                                             grid, pieces, weights, missing, settings, timings)
    summary.duration_s = round(time.perf_counter() - started, 3)
    log.info("run %s: %d EdgeWeight, cakupan %.4f, sumber %s", run_id, len(weights), coverage, estimation_source)
    return summary


def _pieces(edge_ids, coords, index, spec: GridSpec, graph_version: str, cache_dir: pathlib.Path | None):
    """Potongan ruas-sel; disimpan per (versi graf, GridSpec) karena statis."""
    if cache_dir is not None:
        base = pathlib.Path(cache_dir) / f"pieces-{graph_version}-{grid_spec_id(spec)}"
        if (base / "pieces.parquet").exists() and (base / "edges.parquet").exists():
            return pd.read_parquet(base / "pieces.parquet"), pd.read_parquet(base / "edges.parquet")
    pieces, edge_table = edge_cell_pieces(edge_ids, coords, index, spec)
    if cache_dir is not None:
        base.mkdir(parents=True, exist_ok=True)
        pieces.to_parquet(base / "pieces.parquet", index=False)
        edge_table.to_parquet(base / "edges.parquet", index=False)
    return pieces, edge_table


def _write_artifacts(out: pathlib.Path, summary: RunSummary, time_window: TimeWindow, spec: GridSpec,
                     ground_truth: pd.DataFrame, grid: pd.DataFrame, pieces: pd.DataFrame,
                     weights: pd.DataFrame, missing: pd.DataFrame, settings: dict, timings: dict) -> dict:
    """Artefak penelusuran: EdgeWeight → model, input waktu, dan sel grid sumber."""
    from spatial_model.data.observations import sha256_file
    from spatial_model.evaluation.idw.run import code_version

    run_dir = out / summary.run_id
    run_dir.mkdir(parents=True, exist_ok=True)
    files = {
        "ground_truth.parquet": ground_truth,
        "grid_prediction.parquet": grid,
        "edge_cell_pieces.parquet": pieces,
        "edge_weights.parquet": weights,
        "edges_without_estimate.parquet": missing,
    }
    hashes = {}
    for name, frame in files.items():
        frame.to_parquet(run_dir / name, index=False)
        hashes[name] = {"rows": int(len(frame)), "sha256": sha256_file(run_dir / name)}
    manifest = {
        "run_id": summary.run_id,
        "status": summary.status,
        "time_window": {"start": time_window.start.isoformat(), "end": time_window.end.isoformat()},
        "model": {"model_name": "spatial-downscaling-per-pollutant", "models": summary.model_metadata, "model_version": summary.model_version,
                  "estimation_source": summary.estimation_source, "fallback_reason": summary.fallback_reason,
                  "idw": {"power": settings["power"], "neighbors": settings["neighbors"],
                          "config_sha256": settings["config_sha256"]},
                  "confidence": {"formula": "c_max * exp(-d / L)", "c_max_idw": 0.39, "length_scale_m": 3000.0}},
        "graph_version": summary.graph_version,
        "grid_spec": spec.as_dict(),
        "grid_spec_id": grid_spec_id(spec),
        "aggregation": "rata-rata nilai sel berbobot panjang potongan ruas di dalam sel (edge_cell_pieces)",
        "input": {"ground_truth_rows": int(len(ground_truth)),
                  "stations": {p: int((ground_truth["pollutant"] == p).sum()) for p in ("pm25", "no2")},
                  "ground_truth_sha256": _sha256_frame(ground_truth)},
        "output": {"edge_weights": int(len(weights)), "edges_without_estimate": int(len(missing)),
                   "coverage_ratio": summary.coverage_ratio, "rows_written": summary.rows_written,
                   "confidence_distribution": summary.confidence_distribution},
        "files": hashes,
        "code": code_version(),
        "durations_s": {k: round(v, 3) for k, v in timings.items()},
        "created_utc": dt.datetime.now(dt.timezone.utc).isoformat(timespec="seconds"),
    }
    (run_dir / "run_manifest.json").write_text(json.dumps(manifest, indent=2, ensure_ascii=False, default=str),
                                              encoding="utf-8")
    return {"dir": str(run_dir), "files": sorted(hashes)}
