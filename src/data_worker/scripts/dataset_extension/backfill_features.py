"""Backfill dataset_processed-format feature files for a date range, using the Data Worker connectors.

One source group per Cloud Run task (CLOUD_RUN_TASK_INDEX), or all groups with --all:
  0 geoscf_ana   -> geos-cf/geoscf_cell_hourly.parquet
  1 geoscf_fcst  -> geos-cf/geoscf_fcst_cell_hourly.parquet
  2 open_meteo   -> open_meteo/hourly.parquet ; sentinel5p -> sentinel5p/observations.parquet
  3 landsat      -> landsat/scenes/<scene>.parquet, landsat/scene_times.parquet
VIIRS is not backfilled (needs EARTHDATA_TOKEN).

Run from src/data_worker (or with it on PYTHONPATH):
  python backfill_features.py --start 2026-09-27T00:00Z --end 2026-11-19T12:00Z --out DIR [--all]
"""
from __future__ import annotations

import argparse
import datetime as dt
import json
import os
import sys
import time
import traceback
from pathlib import Path

import numpy as np
import pandas as pd

from connectors import geoscf, landsat, open_meteo, sentinel5p, stac
from connectors.http import HttpClient, Policy
from feature_matrix.grid import active_cells
from feature_matrix.sources import WEATHER
from feature_store.reader import landsat_grid_positions
from scripts import fetch_geoscf_fcst as fcst_script

HIST_WEATHER_URL = "https://historical-forecast-api.open-meteo.com/v1/forecast"
ANA_CHUNK_HOURS = 48


def log(msg: str) -> None:
    print(f"{dt.datetime.now(dt.timezone.utc):%H:%M:%S} {msg}", flush=True)


def save(frame: pd.DataFrame, path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(".tmp")
    frame.to_parquet(tmp, index=False)
    os.replace(tmp, path)


# GEOS-CF analysis --------------------------------------------------------------------
def do_ana(start, end, out: Path) -> dict:
    client = HttpClient(geoscf.SOURCE_ANA, geoscf.POLICY)
    hours = pd.date_range(start, end, freq="h", inclusive="left")
    published, missing = {}, []
    for i, h in enumerate(hours):
        moment = geoscf.ana_published(client, h)
        if moment is None:
            missing.append(h)
        else:
            published[h] = moment
        if i % 100 == 0:
            log(f"ana HEAD {i}/{len(hours)} published={len(published)} missing={len(missing)}")
    first, length = geoscf.ana_axis(client)
    inside = sorted(h for h in published if geoscf.ana_index(first, h) < length)
    frames = []
    for k in range(0, len(inside), ANA_CHUNK_HOURS):
        chunk = inside[k:k + ANA_CHUNK_HOURS]
        url, i0, _ = geoscf.ana_query(first, chunk)
        text = client.get(url, timeout=geoscf.OPENDAP_TIMEOUT_S).text
        frame, _ = geoscf.parse_ana(text, {h: published[h] for h in chunk}, geoscf.ana_start(first, i0))
        frames.append(frame)
        log(f"ana OPeNDAP {k + len(chunk)}/{len(inside)}")
    frame = pd.concat(frames, ignore_index=True)
    frame = to_ana_schema(frame)
    save(frame, out / "geos-cf/geoscf_cell_hourly.parquet")
    return {"hours_requested": len(hours), "published": len(published), "not_published": len(missing),
            "beyond_opendap_axis": len(published) - len(inside), "rows": len(frame),
            "latency_hours": frame.latency_hours.describe().round(2).to_dict()}


def to_ana_schema(frame: pd.DataFrame) -> pd.DataFrame:
    f = frame.copy()
    f["time_window_start"] = f.time_window_start.astype("datetime64[us, UTC]")
    f["cell_id"] = f.cell_id.astype("int8")
    for c in ("pm25_ugm3", "no2_ugm3", "no2_ppb", "latency_hours"):
        f[c] = f[c].astype("float32")
    f["available_pm25"] = f.pm25_ugm3.notna()
    f["available_no2"] = f.no2_ugm3.notna()
    f["available_at_utc"] = pd.to_datetime(f.available_at_utc, utc=True).astype("datetime64[us, UTC]")
    f["source"] = pd.Categorical([geoscf.SOURCE_LABEL] * len(f))
    cols = ["time_window_start", "cell_id", "pm25_ugm3", "no2_ugm3", "no2_ppb", "available_pm25", "available_no2",
            "available_at_utc", "latency_hours", "source"]
    return f[cols].sort_values(["time_window_start", "cell_id"]).reset_index(drop=True)


# GEOS-CF forecast ----------------------------------------------------------------------
def do_fcst(start, end, out: Path) -> dict:
    client = HttpClient(geoscf.SOURCE_FCST, geoscf.POLICY)
    first = (start - pd.Timedelta(hours=fcst_script.INIT_HOUR)).ceil("D") + pd.Timedelta(hours=fcst_script.INIT_HOUR)
    inits = pd.date_range(first, end, freq="D")
    frames, skipped = [], {}
    for init in inits:
        try:
            if not geoscf.fcst_complete(client, init):
                skipped[str(init)] = "incomplete"
                continue
            frame, quality = geoscf.fetch_fcst(client, init)
            frames.append(frame)
            log(f"fcst {init:%Y-%m-%d %Hz} rows={len(frame)}")
        except Exception as exc:  # keep going; a missing run falls back to the previous run in the reader
            skipped[str(init)] = f"{type(exc).__name__}: {exc}"
            log(f"fcst {init} failed: {exc}")
    frame = pd.concat(frames, ignore_index=True)
    ref = ["init_utc", "time_window_start", "lead_hours", "cell_id", "pm25_ugm3", "no2_ppb", "first_published_utc",
           "available_at_utc", "no2_ugm3", "available_pm25", "available_no2"]
    for c in ("init_utc", "time_window_start", "first_published_utc", "available_at_utc"):
        frame[c] = pd.to_datetime(frame[c], utc=True).astype("datetime64[us, UTC]")
    frame["cell_id"] = frame.cell_id.astype("int8")
    for c in ("lead_hours", "pm25_ugm3", "no2_ppb", "no2_ugm3"):
        frame[c] = frame[c].astype("float32")
    frame = frame[ref].sort_values(["init_utc", "time_window_start", "cell_id"]).reset_index(drop=True)
    save(frame, out / "geos-cf/geoscf_fcst_cell_hourly.parquet")
    return {"runs_requested": len(inits), "runs_saved": len(frames), "skipped": skipped, "rows": len(frame)}


# Open-Meteo ------------------------------------------------------------------------------
def do_weather(start, end, out: Path) -> dict:
    client = HttpClient(open_meteo.SOURCE, open_meteo.POLICY)
    params = {"latitude": open_meteo.LATITUDE, "longitude": open_meteo.LONGITUDE, "hourly": ",".join(WEATHER),
              "start_date": f"{start:%Y-%m-%d}", "end_date": f"{(end - pd.Timedelta(seconds=1)):%Y-%m-%d}",
              "timezone": "UTC", "models": "best_match"}
    frame, quality = open_meteo.parse(client.get_json(HIST_WEATHER_URL, params=params))
    out_frame = pd.DataFrame({"time_utc": frame.time_utc.astype("datetime64[ns, UTC]")})
    for v in WEATHER:
        out_frame[v] = frame[v].astype("float64")
        out_frame[f"{v}_available"] = frame[v].notna()
    out_frame["feature_available_at"] = pd.Series(pd.NaT, index=out_frame.index, dtype="datetime64[ns, UTC]")
    save(out_frame, out / "open_meteo/hourly.parquet")
    return {"rows": len(out_frame), "qc_dropped": quality if isinstance(quality, dict) else str(quality),
            "null_counts": {v: int(out_frame[v].isna().sum()) for v in WEATHER}}


# Sentinel-5P -----------------------------------------------------------------------------
def do_s5p(start, end, out: Path) -> dict:
    client = HttpClient(sentinel5p.SOURCE, sentinel5p.POLICY)
    items = stac.search(client, sentinel5p.COLLECTION, start.to_pydatetime(), end.to_pydatetime(), sentinel5p.QUERY)
    token = stac.sas_token(client, sentinel5p.COLLECTION)
    frames, failed = [], {}
    for i, item in enumerate(items):
        if i and i % 20 == 0:
            token = stac.sas_token(client, sentinel5p.COLLECTION)
        try:
            obs, _ = sentinel5p.fetch_product(client, item, token)
            frames.append(obs)
        except Exception as exc:
            failed[item["id"]] = f"{type(exc).__name__}: {exc}"
        log(f"s5p {i + 1}/{len(items)} {item['id']}")
    frame = pd.concat([f for f in frames if len(f)], ignore_index=True)
    for c in ("observed_start_at", "observed_at", "produced_at"):
        frame[c] = pd.to_datetime(frame[c], utc=True).astype("datetime64[ns, UTC]")
    frame["n_valid_pixels"] = frame.n_valid_pixels.astype("int64")
    frame["available"] = frame.available.astype(bool)
    save(frame, out / "sentinel5p/observations.parquet")
    return {"items": len(items), "scenes_with_rows": int(frame.scene_id.nunique()), "rows": len(frame),
            "available_rows": int(frame.available.sum()), "failed": failed}


# Landsat ---------------------------------------------------------------------------------
def do_landsat(start, end, out: Path) -> dict:
    client = HttpClient(landsat.SOURCE, landsat.POLICY)
    items = stac.search(client, landsat.COLLECTION, start.to_pydatetime(), end.to_pydatetime(), landsat.QUERY)
    items = [x for x in items if x["id"].startswith(("LC08_L2SP_", "LC09_L2SP_"))]
    token = stac.sas_token(client, landsat.COLLECTION)
    rows, cols = landsat_grid_positions()
    cells = active_cells()
    times, saved, failed = [], [], {}
    for item in items:
        try:
            layers, rec = landsat.fetch_product(client, item, token, rows, cols)
        except Exception as exc:
            failed[item["id"]] = f"{type(exc).__name__}: {exc}"
            log(f"landsat {item['id']} failed: {exc}")
            continue
        scene = cells.copy()
        scene["scene_id"] = rec["scene_id"]
        for c in ("observed_at", "produced_at", "catalog_created_at"):
            scene[c] = pd.Series(pd.Timestamp(rec[c]), index=scene.index).dt.tz_convert("UTC").astype(
                "datetime64[ns, UTC]")
        for f in landsat.FEATURES:
            scene[f] = layers[f].astype("float32")
            scene[f + "_available"] = np.isfinite(layers[f])
            scene[f + "_valid_fraction"] = layers[f + "_valid_fraction"].astype("float32")
        save(scene, out / "landsat/scenes" / f"{rec['scene_id']}.parquet")
        times.append({k: rec[k] for k in ("scene_id", "observed_at", "produced_at", "catalog_created_at")})
        saved.append({"scene_id": rec["scene_id"], "cloud_cover": rec.get("cloud_cover"), "valid_cells": rec["valid_cells"]})
        log(f"landsat {rec['scene_id']} valid={rec['valid_cells']}")
    if times:
        t = pd.DataFrame(times)
        for c in ("observed_at", "produced_at", "catalog_created_at"):
            t[c] = pd.to_datetime(t[c], utc=True).astype("datetime64[ns, UTC]")
        save(t, out / "landsat/scene_times.parquet")
    return {"items": len(items), "saved": saved, "failed": failed}


GROUPS = {0: [("geoscf_ana", do_ana)], 1: [("geoscf_fcst", do_fcst)],
          2: [("open_meteo", do_weather), ("sentinel5p", do_s5p)], 3: [("landsat", do_landsat)]}


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--start", required=True)
    ap.add_argument("--end", required=True)
    ap.add_argument("--out", required=True)
    ap.add_argument("--all", action="store_true")
    ap.add_argument("--group", type=int, default=None)
    a = ap.parse_args()
    start, end, out = pd.Timestamp(a.start), pd.Timestamp(a.end), Path(a.out)
    groups = list(GROUPS) if a.all else [a.group if a.group is not None else int(os.environ.get("CLOUD_RUN_TASK_INDEX", 0))]
    status = 0
    for g in groups:
        for name, fn in GROUPS[g]:
            t0 = time.time()
            log(f"start {name} {start} -> {end}")
            try:
                report = {"status": "ok", **fn(start, end, out)}
            except Exception as exc:
                report = {"status": "failed", "error": f"{type(exc).__name__}: {exc}", "trace": traceback.format_exc()}
                status = 1
            report.update(source=name, start=str(start), end=str(end), seconds=round(time.time() - t0, 1))
            (out / "reports").mkdir(parents=True, exist_ok=True)
            (out / "reports" / f"{name}.json").write_text(json.dumps(report, indent=2, default=str) + "\n")
            log(f"done {name}: {json.dumps({k: v for k, v in report.items() if k != 'trace'}, default=str)[:600]}")
    return status


if __name__ == "__main__":
    sys.exit(main())
