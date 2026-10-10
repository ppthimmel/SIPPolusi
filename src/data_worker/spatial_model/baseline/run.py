"""Satu run eksperimen baseline IDW dari manifest dataset yang dibekukan.

Keluaran pada ``<out>/<run_id>/``:

    config.toml            salinan konfigurasi yang dipakai
    dataset_manifest.json  salinan manifest dataset (versi, split, aturan QC)
    run_manifest.json      seed, parameter, versi dataset/kode/pustaka, perangkat keras, durasi
    metrics.json           metrik agregat, varian, sensitivitas, ringkasan grid
    tables/*.csv           metrik per stasiun dan per kelompok analisis galat
    figures/*.png          visualisasi galat
    predictions.parquet    estimasi LOSO per stasiun-jam
    report.md              laporan ringkas
"""

from __future__ import annotations

import datetime as dt
import hashlib
import importlib.metadata
import json
import logging
import os
import pathlib
import platform
import random
import shutil
import subprocess
import time
import tomllib

import numpy as np
import pandas as pd

from contracts import JAKARTA_BBOX
from spatial_model.baseline import analysis, report
from spatial_model.baseline.dataset import is_snapshot, load_dataset, load_snapshot, sha256_file
from spatial_model.baseline.evaluate import (
    METRIC_DEFINITIONS,
    compute_metrics,
    fixed_split_predict,
    loso_predict,
    select_from_sweep,
    sweep,
)
from spatial_model.grid import GridSpec
from spatial_model.idw import run_idw_fallback

log = logging.getLogger("spatial_model.baseline.run")

LIBRARIES = ("numpy", "pandas", "pyarrow", "pyproj", "matplotlib")


def code_version() -> dict:
    root = pathlib.Path(__file__).resolve().parent
    try:
        commit = subprocess.run(["git", "rev-parse", "HEAD"], cwd=root, capture_output=True,
                                text=True, check=True).stdout.strip()
        dirty = subprocess.run(["git", "status", "--porcelain", "--", "."], cwd=root.parent.parent,
                               capture_output=True, text=True, check=True).stdout.strip()
        branch = subprocess.run(["git", "rev-parse", "--abbrev-ref", "HEAD"], cwd=root, capture_output=True,
                                text=True, check=True).stdout.strip()
        return {"git_commit": commit, "git_branch": branch, "git_dirty": bool(dirty)}
    except (OSError, subprocess.CalledProcessError):
        return {"git_commit": None, "git_branch": None, "git_dirty": None}


def environment() -> dict:
    libs = {}
    for name in LIBRARIES:
        try:
            libs[name] = importlib.metadata.version(name)
        except importlib.metadata.PackageNotFoundError:
            libs[name] = None
    try:
        memory_gb = round(os.sysconf("SC_PAGE_SIZE") * os.sysconf("SC_PHYS_PAGES") / 2**30, 1)
    except (ValueError, OSError, AttributeError):
        memory_gb = None
    return {
        "python": platform.python_version(),
        "platform": platform.platform(),
        "machine": platform.machine(),
        "processor": platform.processor() or None,
        "cpu_count": os.cpu_count(),
        "memory_gb": memory_gb,
        "gpu": None,
        "libraries": libs,
    }


def _frame_sha256(df: pd.DataFrame) -> str:
    return hashlib.sha256(pd.util.hash_pandas_object(df, index=False).to_numpy().tobytes()).hexdigest()


def _filter_variant(df: pd.DataFrame, variant: dict) -> pd.DataFrame:
    sub = df
    if variant.get("pollutant"):
        sub = sub[sub["pollutant"] == variant["pollutant"]]
    if variant.get("station_types"):
        sub = sub[sub["type"].isin(variant["station_types"])]
    if variant.get("exclude_kode"):
        sub = sub[~sub["kode"].isin(variant["exclude_kode"])]
    return sub


def _grid_example(df: pd.DataFrame, model: dict, cfg: dict, fig_dir: pathlib.Path) -> dict:
    """Peta grid 100 m dari run_idw_fallback untuk time window uji dengan stasiun PM2.5 terbanyak."""
    counts = df[df["pollutant"] == "pm25"].groupby("window_start_utc")["station_uuid"].nunique()
    window = counts.sort_values(ascending=False, kind="stable").index[0]
    sources = df[df["window_start_utc"] == window]
    spec = GridSpec.from_bbox(JAKARTA_BBOX.as_tuple(), float(cfg.get("cell_m", 100.0)))
    started = time.perf_counter()
    grid = run_idw_fallback(
        sources.rename(columns={"station_uuid": "station_id", "value_ugm3": "value"}),
        spec, power=model["power"], neighbors=model["neighbors"],
    )
    elapsed = time.perf_counter() - started
    figures = {f"grid_{p}": report.plot_grid_map(grid, spec, sources, window, p, fig_dir)
               for p in ("pm25", "no2") if grid[f"{p}_ugm3"].notna().any()}
    summary = {
        "window_start_utc": window.isoformat(),
        "grid_spec": spec.as_dict(),
        "n_cells": spec.n_cells,
        "duration_s": round(elapsed, 2),
        "stations": {p: int((sources["pollutant"] == p).sum()) for p in ("pm25", "no2")},
        "values": {p: {"min": float(grid[f"{p}_ugm3"].min()), "mean": float(grid[f"{p}_ugm3"].mean()),
                       "max": float(grid[f"{p}_ugm3"].max())}
                   for p in ("pm25", "no2") if grid[f"{p}_ugm3"].notna().any()},
        "nearest_station_m": {"median": float(np.median(grid["nearest_station_m"])),
                              "max": float(grid["nearest_station_m"].max())},
    }
    return {"summary": summary, "figures": figures}


def run_baseline(config_path: pathlib.Path, manifest_path: pathlib.Path, out_root: pathlib.Path) -> pathlib.Path:
    started_utc = dt.datetime.now(dt.timezone.utc)
    t0 = time.perf_counter()
    timings: dict[str, float] = {}

    config_path = pathlib.Path(config_path)
    config_bytes = config_path.read_bytes()
    config = tomllib.loads(config_bytes.decode("utf-8"))
    config_sha = hashlib.sha256(config_bytes).hexdigest()
    seed = int(config.get("seed", 42))
    random.seed(seed)
    np.random.seed(seed)

    model = config["model"]
    eval_cfg = config["evaluation"]
    power, neighbors = float(model["power"]), int(model["neighbors"])
    min_sources = int(model.get("min_sources", 1))
    report_split = eval_cfg.get("report_split", "test")

    df, manifest = (load_snapshot if is_snapshot(manifest_path) else load_dataset)(manifest_path)
    if "group_id" in df:
        eval_cfg = {**eval_cfg, "group_loso": True}
    timings["load_dataset_s"] = time.perf_counter() - t0
    run_id = f"{started_utc:%Y%m%dT%H%M%SZ}-idw-{manifest['dataset_version']}-{config_sha[:8]}"
    run_dir = pathlib.Path(out_root) / run_id
    fig_dir, tab_dir = run_dir / "figures", run_dir / "tables"
    fig_dir.mkdir(parents=True)
    tab_dir.mkdir()
    log.info("run %s: split %s, power=%g, neighbors=%d", run_id, report_split, power, neighbors)

    # Hasil utama: konfigurasi Tabel 3.8 pada split yang dilaporkan.
    t = time.perf_counter()
    eval_df = df[df["split"] == report_split].reset_index(drop=True)
    pred = loso_predict(eval_df, power, neighbors, min_sources)
    pred = analysis.add_analysis_columns(pred, eval_cfg)
    timings["loso_report_split_s"] = time.perf_counter() - t

    overall = analysis.overall_metrics(pred)
    stations = analysis.per_station(pred, overall, eval_cfg)
    tables = analysis.breakdowns(pred, overall, eval_cfg)
    found = analysis.findings(overall, stations, tables, eval_cfg)

    # Varian (mis. NO2 Reference saja).
    variants = {}
    for variant in eval_cfg.get("variants", []):
        sub = _filter_variant(eval_df, variant).reset_index(drop=True)
        vpred = loso_predict(sub, power, neighbors, min_sources)
        vm = analysis.overall_metrics(vpred)
        variants[variant["name"]] = {"definition": variant, "metrics": vm}
        vpred = analysis.add_analysis_columns(vpred, eval_cfg)
        analysis.per_station(vpred, vm, eval_cfg).to_csv(tab_dir / f"variant_{variant['name']}_per_station.csv",
                                                         index=False)
        for pollutant, m in vm.items():
            found.append(
                f"Varian {variant['name']} ({pollutant}): RMSE {m['rmse']:.2f} µg/m³, MAE {m['mae']:.2f} µg/m³, "
                f"R² {m['r2']:.3f}, bias {m['bias']:+.2f} µg/m³ pada n = {m['n']} dari {m['n_stations']} stasiun."
            )

    # Snapshot TI-AI-03: split tetap blind ganda (stasiun uji × blok uji, sumber hanya stasiun train).
    fixed_split = {}
    if "station_split" in df:
        for pollutant, protocols in manifest.get("evaluation", {}).items():
            if "fixed_split" not in protocols:
                continue
            sub = eval_df[eval_df["pollutant"] == pollutant].reset_index(drop=True)
            fp = fixed_split_predict(sub, power, neighbors, "train", report_split, min_sources)
            m = compute_metrics(fp["observed"], fp["predicted"])
            m["n_stations"] = int(fp["station_uuid"].nunique())
            fixed_split[pollutant] = {"sources": "station_split == train", "targets": f"station_split == {report_split}",
                                      "metrics": m}
            found.append(
                f"Split tetap blind ganda ({pollutant}): RMSE {m['rmse']:.2f} µg/m³, MAE {m['mae']:.2f} µg/m³, "
                f"R² {m['r2']:.3f}, bias {m['bias']:+.2f} µg/m³ pada n = {m['n']} dari {m['n_stations']} stasiun uji.")
            fp.to_parquet(run_dir / f"predictions_fixed_split_{pollutant}.parquet", index=False)

    # Sensitivitas pada split validasi; konfigurasi terpilih dievaluasi pada split uji.
    tuned = {}
    sweep_table = None
    figures: dict[str, str] = {}
    sweep_cfg = model.get("sweep", {})
    if sweep_cfg.get("enabled"):
        t = time.perf_counter()
        val_df = df[df["split"] == "val"].reset_index(drop=True)
        sweep_table = sweep(val_df, sweep_cfg["power"], sweep_cfg["neighbors"], min_sources)
        sweep_table.to_csv(tab_dir / "sweep_validation.csv", index=False)
        metric = sweep_cfg.get("selection_metric", "rmse")
        for pollutant, best in select_from_sweep(sweep_table, metric).items():
            sub = eval_df[eval_df["pollutant"] == pollutant].reset_index(drop=True)
            tp = loso_predict(sub, best["power"], best["neighbors"], min_sources)
            tuned[pollutant] = {"power": best["power"], "neighbors": best["neighbors"],
                                f"val_{metric}": best[metric], "test": compute_metrics(tp["observed"], tp["predicted"])}
        figures["sweep"] = report.plot_sweep(sweep_table, fig_dir, metric)
        timings["sweep_s"] = time.perf_counter() - t

    grid_info = None
    if eval_cfg.get("grid_map", {}).get("enabled"):
        grid_info = _grid_example(eval_df, {"power": power, "neighbors": neighbors}, eval_cfg["grid_map"], fig_dir)
        timings["grid_example_s"] = grid_info["summary"]["duration_s"]

    figures["scatter"] = report.plot_scatter(pred, fig_dir)
    figures["map_station_rmse"] = report.plot_station_map(stations, fig_dir)
    figures["by_hour"] = report.plot_by(tables, "by_hour_local", "hour_local", "galat menurut jam (WIB)",
                                         "jam WIB", fig_dir, "error_by_hour_wib.png")
    figures["by_nearest_source"] = report.plot_by(
        tables, "by_nearest_source_km", "nearest_source_km_bin", "galat menurut jarak sumber terdekat",
        "jarak ke stasiun sumber terdekat (km)", fig_dir, "error_by_nearest_source_km.png")
    figures["by_kota"] = report.plot_by(tables, "by_kota", "kota", "galat menurut kota", "kota administrasi",
                                         fig_dir, "error_by_kota.png")
    figures["by_date"] = report.plot_by(tables, "by_date_local", "date_local", "galat menurut tanggal (WIB)",
                                         "tanggal", fig_dir, "error_by_date.png")
    if grid_info:
        figures.update(grid_info["figures"])

    # ---------------------------------------------------------------- tulis
    pred_out = pred.sort_values(["pollutant", "window_start_utc", "kode"]).reset_index(drop=True)
    pred_out.to_parquet(run_dir / "predictions.parquet", index=False)
    stations.to_csv(tab_dir / "per_station.csv", index=False)
    for name, table in tables.items():
        table.to_csv(tab_dir / f"{name}.csv", index=False)
    shutil.copyfile(config_path, run_dir / "config.toml")
    shutil.copyfile(manifest_path, run_dir / "dataset_manifest.json")

    metrics = {
        "split": report_split,
        "unit": "µg/m³",
        "definitions": METRIC_DEFINITIONS,
        "overall": overall,
        "variants": variants,
        "fixed_split": fixed_split,
        "tuned_on_validation": tuned,
        "grid_example": grid_info["summary"] if grid_info else None,
        "findings": found,
    }
    (run_dir / "metrics.json").write_text(json.dumps(metrics, indent=2, ensure_ascii=False, default=float))

    timings["total_s"] = time.perf_counter() - t0
    boundaries = manifest["split"]["boundaries"]
    run_manifest = {
        "run_id": run_id,
        "experiment": config.get("experiment"),
        "model_name": "spatial-downscaling",
        "model": {"method": "idw", "power": power, "neighbors": neighbors, "min_sources": min_sources,
                  "crs": model.get("crs", "EPSG:32748")},
        "seed": seed,
        "evaluation": {"cv_scheme": ("leave-one-group-out" if "group_id" in df else eval_cfg.get("cv_scheme")),
                       "report_split": report_split, "fixed_split": bool(fixed_split),
                       "sweep": sweep_cfg if sweep_cfg.get("enabled") else None,
                       "variants": [v["name"] for v in eval_cfg.get("variants", [])]},
        "dataset": {
            "dataset_version": manifest["dataset_version"],
            "manifest_path": str(pathlib.Path(manifest_path)),
            "manifest_sha256": sha256_file(pathlib.Path(manifest_path)),
            "content_sha256": manifest["files"]["station_hour.parquet"]["content_sha256"],
            "snapshot_manifest_sha256": manifest.get("snapshot_manifest_sha256"),
            "source_type": manifest["source"]["type"],
            "source_snapshot_utc": (manifest["source"].get("snapshot_utc")
                                    or manifest["source"].get("export_snapshot_utc")),
            "split_boundaries": boundaries,
        },
        "config": {"path": str(config_path), "sha256": config_sha},
        "outputs": {"predictions_sha256": _frame_sha256(pred_out[["station_uuid", "pollutant", "window_start_utc",
                                                                  "observed", "predicted"]]),
                    "n_predictions": int(len(pred_out))},
        "code": code_version(),
        "environment": environment(),
        "started_utc": started_utc.isoformat(timespec="seconds"),
        "durations_s": {k: round(v, 2) for k, v in timings.items()},
    }
    (run_dir / "run_manifest.json").write_text(json.dumps(run_manifest, indent=2, ensure_ascii=False, default=str))

    report.write_report(run_dir / "report.md", {
        "run_manifest": run_manifest, "overall": overall, "stations": stations, "tables": tables,
        "findings": found, "tuned": tuned, "variants": variants, "figures": figures, "fixed_split": fixed_split,
        "grid": grid_info["summary"] if grid_info else None,
    })
    log.info("run %s selesai dalam %.1f s: %s", run_id, timings["total_s"], run_dir)
    return run_dir
