"""Inferensi Spatial Downscaling Model sampai EdgeWeight (Tabel 3.8, Gambar 3.7 B16–B27).

``run_downscale_inference`` menjalankan satu time window:

1. ST-GNN (``predict_concentration``) bila artefaknya tersedia. Artefak ST-GNN
   dan FeatureMatrix belum ada (TI-AI-02, TI-AI-09), sehingga langkah ini
   selalu gagal dengan ``ModelUnavailableError`` dan alur berlanjut ke
   fallback, persis seperti fragmen alt B23/B24.
2. Fallback IDW (``run_idw_fallback``) dari ground truth time window berjalan,
   dengan parameter dan kendali mutu yang sama dengan baseline TI-AI-04.
3. Confidence score per sel (``estimate_confidence``).
4. Agregasi grid ke ruas (``aggregate_grid_to_edges``).
5. Opsional: penulisan ke Spatial Pollution Cache DB (``write_pollution_weight``)
   dan artefak penelusuran (grid, potongan ruas-sel, EdgeWeight, manifest).

Eksekusi idempoten terhadap (time_window_start, model_version): run_id
ditentukan dari keduanya, dan time window yang sudah ``complete`` dengan
model_version yang sama tidak ditulis ulang kecuali ``force`` (UT-SDM-01b).
"""

from __future__ import annotations

import datetime as dt
import hashlib
import json
import logging
import pathlib
import time
import tomllib

import numpy as np
import pandas as pd

from contracts import JAKARTA_BBOX, EdgeWeight, RunSummary, TimeWindow
from spatial_model.aggregate import aggregate_grid_to_edges, edge_cell_pieces, grid_spec_id
from spatial_model.confidence import confidence_distribution, estimate_confidence
from spatial_model.grid import GridSpec
from spatial_model.idw import run_idw_fallback

log = logging.getLogger("spatial_model.inference")

#: Konfigurasi baseline TI-AI-04: sumber tunggal parameter IDW dan aturan kendali mutu.
BASELINE_CONFIG = pathlib.Path(__file__).resolve().parent / "baseline" / "configs" / "idw_baseline.toml"


class ModelUnavailableError(RuntimeError):
    """Artefak ST-GNN tidak dapat dimuat; memicu fallback IDW (Tabel 3.12)."""


def load_idw_settings(path: pathlib.Path = BASELINE_CONFIG) -> dict:
    config = tomllib.loads(pathlib.Path(path).read_text(encoding="utf-8"))
    model = config["model"]
    return {
        "power": float(model["power"]),
        "neighbors": int(model["neighbors"]),
        "qc": config["dataset"].get("qc", {}),
        "cell_m": float(config["evaluation"].get("grid_map", {}).get("cell_m", 100.0)),
        "config_sha256": hashlib.sha256(pathlib.Path(path).read_bytes()).hexdigest(),
    }


def idw_model_version(power: float, neighbors: int) -> str:
    return f"idw-p{power:g}-k{neighbors}"


def predict_concentration(feature_matrix, background_term, model):
    """Mesin inferensi ST-GNN (Tabel 3.8). Belum ada artefak terlatih."""
    raise ModelUnavailableError("artefak ST-GNN belum tersedia (TI-AI-09/TI-AI-11)")


def resolve_stgnn_model(model_version: str | None):
    """Resolusi alias "production" di Model Registry. Belum ada model yang diterbitkan."""
    raise ModelUnavailableError(
        f"model spatial-downscaling {model_version or 'production'} belum terdaftar di Model Registry"
    )


# ------------------------------------------------------------------ ground truth


def load_ground_truth_window(conn, time_window: TimeWindow, qc: dict, schema: str = "ground_truth") -> pd.DataFrame:
    """Ground truth PM2.5 dan NO2 time window berjalan, dengan kendali mutu baseline.

    Memakai ``apply_qc`` dan ``aggregate_hourly`` dari dataset baseline,
    sehingga aturan pembersihan sama persis dengan evaluasi TI-AI-04.
    """
    from psycopg import sql

    from spatial_model.baseline.dataset import METRIC_TO_POLLUTANT, aggregate_hourly, apply_qc

    cur = conn.cursor()
    cur.execute(
        sql.SQL("""SELECT uuid::text AS uuid, kode, lat, lng,
                          ST_Intersects(geom, ST_MakeEnvelope(%s, %s, %s, %s, 4326)) AS in_jakarta_bbox
                   FROM {}.station""").format(sql.Identifier(schema)),
        JAKARTA_BBOX.as_tuple(),
    )
    stations = pd.DataFrame(cur.fetchall(), columns=[c.name for c in cur.description])
    cur.execute(
        sql.SQL("""SELECT station_uuid::text AS station_uuid, metric, ts_utc, value, qc FROM {}.observation
                   WHERE metric = ANY(%s) AND ts_utc >= %s AND ts_utc < %s""").format(sql.Identifier(schema)),
        (list(METRIC_TO_POLLUTANT), time_window.start, time_window.end),
    )
    obs = pd.DataFrame(cur.fetchall(), columns=[c.name for c in cur.description])
    if obs.empty:
        return pd.DataFrame(columns=["station_id", "kode", "lon", "lat", "pollutant", "value", "n_readings"])
    obs["ts_utc"] = pd.to_datetime(obs["ts_utc"], utc=True)
    obs["value"] = obs["value"].astype(float)
    clean, _ = apply_qc(obs, stations.rename(columns={"lng": "lon"}), qc, restrict_to_study_area=True)
    hourly = aggregate_hourly(clean, 1)
    meta = stations.rename(columns={"uuid": "station_uuid", "lng": "lon"})[["station_uuid", "kode", "lat", "lon"]]
    hourly = hourly.merge(meta, on="station_uuid", how="left")
    return hourly.rename(columns={"station_uuid": "station_id", "value_ugm3": "value"})[
        ["station_id", "kode", "lon", "lat", "pollutant", "value", "n_readings"]
    ].sort_values(["pollutant", "kode"]).reset_index(drop=True)


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
                   estimation_source=r.estimation_source)
        for r in weights.itertuples(index=False)
    ]


def run_downscale_inference(
    time_window: TimeWindow,
    model_version: str | None = None,
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
) -> RunSummary:
    """Satu eksekusi inferensi untuk ``time_window`` (penangan IDownscaleInference).

    ``edges`` adalah (edge_ids, coords_lonlat, coord_edge_index) dari
    ``cache.read_road_edges``. ``conn`` (psycopg) bila diberikan dipakai untuk
    memeriksa idempotensi dan menulis cache. ``artifact_dir`` bila diberikan
    menerima artefak penelusuran per run.
    """
    from spatial_model import cache

    started = time.perf_counter()
    settings = settings or load_idw_settings()
    spec = grid_spec or GridSpec.from_bbox(JAKARTA_BBOX.as_tuple(), settings["cell_m"])
    timings: dict[str, float] = {}

    # B23: ST-GNN; gagal → B24 fallback IDW (Tabel 3.12).
    fallback_reason = None
    try:
        model = resolve_stgnn_model(model_version)
        grid = predict_concentration(None, None, model)
        estimation_source, background_source = "stgnn", None
        resolved_version = model_version
    except ModelUnavailableError as exc:
        fallback_reason = f"{type(exc).__name__}: {exc}"
        estimation_source, background_source = "idw", None
        resolved_version = idw_model_version(settings["power"], settings["neighbors"])
        grid = None
    run_id = make_run_id(time_window, resolved_version)

    if conn is not None and not force:
        record = cache.window_record(conn, time_window.start, pollution_schema)
        if record and record["status"] == "complete" and record["model_version"] == resolved_version:
            log.info("time window %s sudah complete dengan %s; dilewati", time_window, resolved_version)
            return RunSummary(run_id=run_id, status="skipped", time_window_start=time_window.start,
                              model_version=resolved_version, estimation_source=estimation_source,
                              coverage_ratio=record.get("coverage_ratio"), graph_version=graph_version,
                              duration_s=round(time.perf_counter() - started, 3),
                              fallback_reason=fallback_reason)

    t = time.perf_counter()
    if grid is None:
        grid = run_idw_fallback(ground_truth, spec, power=settings["power"], neighbors=settings["neighbors"])
    grid["confidence_score"] = estimate_confidence(grid["nearest_station_m"].to_numpy(),
                                                   estimation_source=estimation_source)
    timings["grid_s"] = time.perf_counter() - t

    t = time.perf_counter()
    edge_ids, coords, index = edges
    pieces, edge_table = _pieces(edge_ids, coords, index, spec, graph_version, pieces_cache_dir)
    weights, missing = aggregate_grid_to_edges(grid, pieces, edge_table)
    weights["background_source"] = background_source
    weights["estimation_source"] = estimation_source
    timings["aggregate_s"] = time.perf_counter() - t
    coverage = len(weights) / len(edge_table) if len(edge_table) else 0.0

    rows_written = 0
    if conn is not None:
        t = time.perf_counter()
        rows_written = cache.write_pollution_weight(conn, weights, time_window, graph_version, resolved_version,
                                                    coverage_ratio=coverage, pollution_schema=pollution_schema,
                                                    osm_schema=osm_schema)
        timings["write_cache_s"] = time.perf_counter() - t

    summary = RunSummary(
        run_id=run_id, status="complete", time_window_start=time_window.start, model_version=resolved_version,
        estimation_source=estimation_source, coverage_ratio=round(coverage, 6),
        confidence_distribution=confidence_distribution(weights["confidence_score"]),
        graph_version=graph_version, rows_written=rows_written, fallback_reason=fallback_reason,
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
    from spatial_model.baseline.dataset import sha256_file
    from spatial_model.baseline.run import code_version

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
        "model": {"model_name": "spatial-downscaling", "model_version": summary.model_version,
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
    (run_dir / "run_manifest.json").write_text(json.dumps(manifest, indent=2, ensure_ascii=False, default=str))
    return {"dir": str(run_dir), "files": sorted(hashes)}
