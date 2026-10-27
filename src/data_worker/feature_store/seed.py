"""Muat arsip ``dataset_processed`` (data latih TI-AI-01) ke skema ``features``.

Image Data Worker tidak memuat ``dataset_processed``, sedangkan aturan as-of
memakai pengamatan valid terakhir: tanpa riwayat, NDVI/NDBI Landsat (umur
sampai ±200 hari) baru terisi setelah scene cerah berikutnya. ``seed`` mengisi
riwayat tersebut sekali; konektor melanjutkannya. Penulisan idempoten: produk
dan jam yang sudah ada dilewati.

Cap ``fetched_utc`` hasil seed adalah waktu seed dijalankan (kapan data masuk
penyimpanan), sehingga FeatureMatrix yang dibangun sebelum seed tetap dapat
dibangun ulang persis (``read_features``).
"""

from __future__ import annotations

import datetime as dt
import json
import logging
from pathlib import Path

import numpy as np
import pandas as pd

from connectors import landsat, sentinel5p, viirs
from feature_matrix.grid import active_cells
from feature_matrix.sources import DATASET_PROCESSED, WEATHER

from .store import RETENTION_DAYS, FeatureStore, now_utc

log = logging.getLogger("data_worker.features.seed")

SEED_SOURCES = ("open_meteo", "geoscf_ana", "geoscf_fcst", "sentinel5p", "viirs", "landsat")
RETENTION_KEY = {"open_meteo": "weather"}


def _utc(value) -> pd.Timestamp:
    value = pd.Timestamp(value)
    return value.tz_localize("UTC") if value.tzinfo is None else value.tz_convert("UTC")


def seed(store: FeatureStore, root: Path | str = DATASET_PROCESSED, *, sources=SEED_SOURCES,
         since: dict | None = None, now: dt.datetime | None = None) -> dict:
    """Muat setiap sumber sejak ``since[source]`` (bawaan: batas retensi dari ``now``)."""
    root, now = Path(root), now or now_utc()
    cut = {s: pd.Timestamp(now) - pd.Timedelta(days=RETENTION_DAYS[RETENTION_KEY.get(s, s)]) for s in SEED_SOURCES}
    cut.update({k: _utc(v) for k, v in (since or {}).items()})
    summary = {}
    for source in sources:
        fetch_id = store.start_fetch(f"seed:{source}", None)
        try:
            result = SEEDERS[source](store, root, cut[source], fetch_id, now)
        except Exception as exc:
            store.finish_fetch(fetch_id, "failed", note=f"{type(exc).__name__}: {exc}")
            raise
        store.finish_fetch(fetch_id, "ok", products_new=result.get("products", 0), rows_written=result.get("rows", 0),
                           detail={"since": cut[source].isoformat(), **result})
        log.info("seed %s: %s", source, result)
        summary[source] = result
    return summary


def _weather(store, root, since, fetch_id, fetched):
    frame = pd.read_parquet(root / "open_meteo/hourly.parquet")
    frame = frame[frame.time_utc >= since].copy()
    for v in WEATHER:
        frame.loc[~frame[f"{v}_available"], v] = np.nan
        frame[f"{v}_qc"] = False
    return {"rows": store.write_weather(frame, fetch_id, fetched)}


def _geoscf_ana(store, root, since, fetch_id, fetched):
    frame = pd.read_parquet(root / "geos-cf/geoscf_cell_hourly.parquet")
    frame = frame[frame.time_window_start >= since].copy()
    frame["cell_id"] = frame.cell_id.astype("int16")
    return {"rows": store.write_geoscf_ana(frame, fetch_id, fetched)}


def _geoscf_fcst(store, root, since, fetch_id, fetched):
    frame = pd.read_parquet(root / "geos-cf/geoscf_fcst_cell_hourly.parquet")
    frame = frame[frame.init_utc >= since].copy()
    frame["cell_id"] = frame.cell_id.astype("int16")
    return {"rows": store.write_geoscf_fcst(frame, fetch_id, fetched)}


def _sentinel5p(store, root, since, fetch_id, fetched):
    obs = pd.read_parquet(root / "sentinel5p/observations.parquet")
    obs = obs[obs.observed_at >= since]
    have = store.product_ids("sentinel5p")
    products = rows = 0
    for scene, group in obs.groupby("scene_id", sort=False):
        if scene in have:
            continue
        record = {"scene_id": scene, "observed_start_at": group.observed_start_at.min(),
                  "observed_at": group.observed_at.max(), "produced_at": group.produced_at.iloc[0]}
        store.write_product("sentinel5p", record, fetch_id=fetch_id, fetched_utc=fetched,
                            processing_version=sentinel5p.PROCESSING_VERSION, n_cells=len(group),
                            observations=group, quality={"seeded_from": "dataset_processed"})
        products, rows = products + 1, rows + len(group)
    return {"products": products, "rows": rows}


def _viirs(store, root, since, fetch_id, fetched):
    since_day = pd.Timestamp(since).floor("D")
    daily = pd.read_parquet(root / "viirs/daily.parquet", filters=[("observation_date_utc", ">=", since_day)])
    manifest = json.loads((root / "viirs/manifest.json").read_text(encoding="utf-8"))
    names = {pd.Timestamp(x["observation_date"], tz="UTC"): x["scene_id"] for x in manifest["inputs"]}
    order = pd.Index(viirs.cell_ids())
    have = store.product_ids("viirs")
    products = 0
    for day, group in daily.groupby("observation_date_utc"):
        scene = names[day]
        if scene in have:
            continue
        group = group.set_index("viirs_cell_id").reindex(order)
        if group.ntl_available.isna().any():
            raise ValueError(f"VIIRS {scene}: sel berbeda dengan ekstraksi konektor")
        layers = {name: group[name].to_numpy(dtype) for name, dtype in viirs.LAYERS.items()}
        record = {"scene_id": scene, "observed_start_at": group.observed_start_at.iloc[0],
                  "observed_at": group.observed_end_at.iloc[0], "produced_at": group.produced_at.iloc[0]}
        store.write_product("viirs", record, fetch_id=fetch_id, fetched_utc=fetched,
                            processing_version=viirs.PROCESSING_VERSION, n_cells=len(group), layers=layers,
                            quality={"valid_cells": int(group.ntl_available.sum()), "seeded_from": "dataset_processed"})
        products += 1
    return {"products": products}


def _landsat(store, root, since, fetch_id, fetched):
    order = pd.Index(active_cells().grid_id)
    have = store.product_ids("landsat")
    products = 0
    for path in sorted((root / "landsat/scenes").glob("*.parquet")):
        scene = path.stem
        if scene in have:
            continue
        frame = pd.read_parquet(path)
        if frame.observed_at.iloc[0] < since:
            continue
        frame = frame.set_index("grid_id").reindex(order)
        layers = {}
        for f in landsat.FEATURES:
            layers[f] = np.where(frame[f + "_available"].fillna(False).to_numpy(bool),
                                 frame[f].to_numpy("float32"), np.nan).astype("float32")
            layers[f + "_valid_fraction"] = frame[f + "_valid_fraction"].to_numpy("float32")
        record = {"scene_id": scene, "observed_at": frame.observed_at.iloc[0], "produced_at": frame.produced_at.iloc[0],
                  "catalog_created_at": frame.catalog_created_at.iloc[0]}
        store.write_product("landsat", record, fetch_id=fetch_id, fetched_utc=fetched,
                            processing_version=landsat.PROCESSING_VERSION, n_cells=len(order), layers=layers,
                            quality={"valid_cells": {f: int(np.isfinite(layers[f]).sum()) for f in landsat.FEATURES},
                                     "seeded_from": "dataset_processed"})
        products += 1
    return {"products": products}


SEEDERS = {"open_meteo": _weather, "geoscf_ana": _geoscf_ana, "geoscf_fcst": _geoscf_fcst,
           "sentinel5p": _sentinel5p, "viirs": _viirs, "landsat": _landsat}
