"""Langkah siklus TI-SE-02: konektor sumber sekunder, kendali mutu, penyelarasan, dan write_features.

Setiap langkah adalah fungsi ``(time_window, config, context)`` untuk
``acquisition.schedule_acquisition`` (Gambar 3.7, B2-B3 dan B11-B14):

- ``fetch_satellite_archive``: GEOS-CF ``ana`` dan ``fcst``, Landsat 8/9, VIIRS VNP46A2.
- ``fetch_tropospheric_column``: Sentinel-5P NO2.
- ``fetch_meteo``: Open-Meteo.
- ``run_quality_control``: ketersediaan dan umur setiap sumber pada waktu inferensi
  τ (akhir time window); sumber yang hilang atau terlalu basi ditandai
  ``available=false`` (Tabel 3.12), siklus tetap berjalan.
- ``align_spatiotemporal``: FeatureMatrix seluruh sel aktif grid 100 m untuk τ
  dengan aturan as-of data latih (``StoreSourceReader``).
- ``write_features``: ``feature_version`` (SHA-256 isi) dan manifest ke tabel
  ``feature_window``; Parquet + ``manifest.json`` ke ``FEATURE_STORE_DIR`` bila diatur.

Setiap sumber dicatat sebagai satu baris ``features.fetch_log``. Kegagalan satu
sumber tidak menghentikan sumber lain (UT-DW-01b, PF-14): galatnya dicatat,
langkahnya ``failed``, dan langkah berikutnya tetap berjalan.
"""

from __future__ import annotations

import dataclasses
import datetime as dt
import hashlib
import json
import logging
import os
import pathlib
from typing import Callable

import numpy as np
import pandas as pd

from connectors import geoscf, landsat, open_meteo, sentinel5p, stac, viirs
from connectors.http import FormatError, HttpClient, Policy, SourceError
from contracts import JAKARTA_BBOX, TimeWindow

from .reader import StoreSourceReader, landsat_grid_positions
from .store import FeatureStore, now_utc, schema_name

log = logging.getLogger("data_worker.features")

SOURCES = ("open_meteo", "geoscf_ana", "geoscf_fcst", "sentinel5p", "landsat", "viirs")
#: Jeda minimum antarpemeriksaan katalog per sumber (Tabel 3.18: Landsat bulanan, VIIRS mingguan).
CHECK_INTERVAL = {"landsat": dt.timedelta(hours=24), "viirs": dt.timedelta(hours=24)}
#: Urutan keparahan status sumber; status langkah adalah yang terparah.
SEVERITY = {"failed": 4, "degraded": 3, "unavailable": 2, "ok": 1, "not_due": 0, "disabled": 0}
#: Batas umur pada τ sebelum sumber dianggap tidak tersedia (di atas umur maksimum data latih).
STALE_AFTER_HOURS = {"open_meteo": 12, "geoscf_ana": 72, "sentinel5p": 14 * 24, "viirs": 30 * 24,
                     "landsat": 240 * 24}


def uses_context(step: Callable) -> Callable:
    """Penanda: ``schedule_acquisition`` meneruskan kamus konteks siklus sebagai argumen ketiga."""
    step.uses_context = True
    return step


def enabled_sources(environ=None) -> tuple[str, ...]:
    env = os.environ if environ is None else environ
    raw = (env.get("FEATURES_SOURCES") or "").strip()
    if not raw or raw == "all":
        return SOURCES
    if raw == "none":
        return ()
    chosen = tuple(s.strip() for s in raw.split(",") if s.strip())
    unknown = sorted(set(chosen) - set(SOURCES))
    if unknown:
        raise ValueError(f"FEATURES_SOURCES memuat sumber tak dikenal: {unknown}")
    return chosen


def _open_store(config, context: dict) -> FeatureStore:
    store = context.get("feature_store")
    if store is None or store.conn.closed:
        store = FeatureStore.connect(config.database, schema_name())
        store.ensure_schema()
        context["feature_store"] = store
    return store


@dataclasses.dataclass
class SourceResult:
    status: str
    products_new: int = 0
    rows_written: int = 0
    note: str | None = None
    detail: dict = dataclasses.field(default_factory=dict)


def run_source(store: FeatureStore, source: str, window: TimeWindow, policy: Policy,
               fn: Callable[[HttpClient, str], SourceResult], *, headers: dict | None = None,
               now: dt.datetime | None = None, environ=None) -> dict:
    """Jalankan satu konektor dengan pencatatan ``fetch_log``; galat apa pun menjadi status ``failed``."""
    now = now or now_utc()
    if source not in enabled_sources(environ):
        return {"status": "disabled", "note": "dinonaktifkan lewat FEATURES_SOURCES"}
    interval = CHECK_INTERVAL.get(source)
    if interval is not None:
        last = store.conn.execute(
            f"""SELECT started_utc, detail FROM {store.s}.fetch_log WHERE source = %s AND status IN ('ok', 'degraded')
                ORDER BY started_utc DESC LIMIT 1""", (source,)).fetchone()
        if last and now - last["started_utc"] < interval and not (last["detail"] or {}).get("pending"):
            return {"status": "not_due", "note": f"katalog terakhir diperiksa {last['started_utc']:%Y-%m-%dT%H:%MZ}"}
    client = HttpClient(source, policy, headers=headers)
    fetch_id = store.start_fetch(source, window.start)
    try:
        result = fn(client, fetch_id)
    except SourceError as exc:
        result = SourceResult("failed", note=str(exc))
    except FormatError as exc:
        result = SourceResult("failed", note=f"format respons berubah: {exc}")
    except Exception as exc:  # noqa: BLE001 - satu sumber tidak boleh menghentikan yang lain
        log.exception("konektor %s gagal", source)
        result = SourceResult("failed", note=f"{type(exc).__name__}: {exc}")
    counts = client.counts()
    store.finish_fetch(fetch_id, result.status, products_new=result.products_new, rows_written=result.rows_written,
                       counts=counts, note=result.note, detail=result.detail)
    level = logging.ERROR if result.status == "failed" else (
        logging.WARNING if result.status in ("degraded", "unavailable") else logging.INFO)
    log.log(level, "sumber %s: %s (baru %d, baris %d, permintaan %d, diulang %d, gagal %d)%s", source, result.status,
            result.products_new, result.rows_written, counts["requests"], counts["retried_requests"],
            counts["failed_requests"], f": {result.note}" if result.note else "")
    return {"status": result.status, "fetch_id": fetch_id, "products_new": result.products_new,
            "rows_written": result.rows_written, **counts, **({"note": result.note} if result.note else {}),
            **{k: v for k, v in result.detail.items() if k in ("pending", "hours", "published_hours")}}


def combine(sources: dict) -> dict:
    """Status langkah dari status sumber-sumbernya (yang terparah)."""
    active = {k: v for k, v in sources.items() if v["status"] not in ("disabled", "not_due")}
    if not active:
        status = "disabled" if all(v["status"] == "disabled" for v in sources.values()) else "ok"
    else:
        status = max((v["status"] for v in active.values()), key=lambda s: SEVERITY.get(s, 3))
    failed = [k for k, v in sources.items() if v["status"] == "failed"]
    out = {"status": status, "sources": sources}
    if failed:
        notes = [str(sources[k].get("note") or "gagal") for k in failed]
        out["note"] = "; ".join(n if n.startswith(f"{k}:") else f"{k}: {n}" for k, n in zip(failed, notes))
    return out


# Konektor ---------------------------------------------------------------------
def _meteo(store: FeatureStore, now: dt.datetime):
    def run(client: HttpClient, fetch_id: str) -> SourceResult:
        frame, quality = open_meteo.parse(open_meteo.fetch(client))
        if frame.empty:
            return SourceResult("unavailable", note="respons tanpa jam", detail=quality)
        rows = store.write_weather(frame, fetch_id, now)
        return SourceResult("ok", rows_written=rows, detail=quality)
    return run


def _geoscf_ana(store: FeatureStore, now: dt.datetime):
    def run(client: HttpClient, fetch_id: str) -> SourceResult:
        stored = store.stored_ana_hours(pd.Timestamp(now) - pd.Timedelta(hours=geoscf.ANA_LOOKBACK_HOURS + 8))
        frame, info = geoscf.fetch_ana(client, now, stored)
        rows = store.write_geoscf_ana(frame, fetch_id, now)
        return SourceResult("ok", products_new=int(info.get("published_hours", 0)), rows_written=rows, detail=info)
    return run


def _geoscf_fcst(store: FeatureStore, now: dt.datetime):
    def run(client: HttpClient, fetch_id: str) -> SourceResult:
        inits = geoscf.fcst_inits(now)
        stored = store.stored_fcst_inits(min(inits))
        new, rows, waiting = 0, 0, []
        for init in sorted(i for i in inits if i not in stored):
            if not geoscf.fcst_complete(client, init):
                waiting.append(f"{init:%Y-%m-%dT%Hz}")
                continue
            frame, _ = geoscf.fetch_fcst(client, init)
            rows += store.write_geoscf_fcst(frame, fetch_id, now)
            new += 1
        note = f"run belum lengkap terbit: {', '.join(waiting)}" if waiting else None
        return SourceResult("ok", products_new=new, rows_written=rows, note=note,
                            detail={"runs_checked": [f"{i:%Y-%m-%dT%Hz}" for i in inits]})
    return run


def _products(store: FeatureStore, source: str, items: list[dict], limit: int, fetch_one) -> SourceResult:
    """Produk katalog yang belum tersimpan, terlama dulu, paling banyak ``limit`` per siklus."""
    since = min((pd.Timestamp(i["observed_at"]) for i in items), default=None)
    stored = store.product_ids(source, since=since - pd.Timedelta(days=1) if since is not None else None)
    todo = [i for i in items if i["id"] not in stored]
    new, rows, errors = 0, 0, []
    for item in todo[:limit]:
        try:
            rows += fetch_one(item)
            new += 1
        except FormatError as exc:
            log.warning("%s: produk %s dilewati: %s", source, item["id"], exc)
            errors.append(f"{item['id']}: {exc}")
    pending = max(0, len(todo) - limit)
    detail = {"catalog_items": len(items), "already_stored": len(items) - len(todo), "pending": pending,
              "products": [i["id"] for i in todo[:limit]]}
    if errors:
        status = "failed" if new == 0 else "degraded"
        return SourceResult(status, new, rows, note="; ".join(errors), detail=detail)
    note = f"{pending} produk menunggu siklus berikutnya" if pending else None
    return SourceResult("ok", new, rows, note=note, detail=detail)


def _sentinel5p(store: FeatureStore, now: dt.datetime):
    def run(client: HttpClient, fetch_id: str) -> SourceResult:
        items = [dict(i, observed_at=i["properties"]["datetime"]) for i in sentinel5p.search(client, now)]
        token = stac.sas_token(client, sentinel5p.COLLECTION) if items else None

        def one(item):
            obs, rec = sentinel5p.fetch_product(client, item, token)
            record = dict(rec, observed_start_at=rec["observed_start"], observed_at=rec["observed_end"])
            store.write_product("sentinel5p", record, fetch_id=fetch_id, fetched_utc=now,
                                processing_version=sentinel5p.PROCESSING_VERSION, n_cells=len(obs), observations=obs,
                                quality={"source_pixels": rec["source_pixels"], "valid_pixels": rec["valid_pixels"],
                                         "bytes_read": rec["bytes_read"]})
            return len(obs)
        return _products(store, "sentinel5p", items, sentinel5p.MAX_PRODUCTS, one)
    return run


def _landsat(store: FeatureStore, now: dt.datetime):
    def run(client: HttpClient, fetch_id: str) -> SourceResult:
        items = [dict(i, observed_at=i["properties"]["datetime"]) for i in landsat.search(client, now)]
        token = stac.sas_token(client, landsat.COLLECTION) if items else None
        rows, cols = landsat_grid_positions()

        def one(item):
            layers, rec = landsat.fetch_product(client, item, token, rows, cols)
            store.write_product("landsat", rec, fetch_id=fetch_id, fetched_utc=now,
                                processing_version=landsat.PROCESSING_VERSION, n_cells=len(rows), layers=layers,
                                quality={"valid_cells": rec["valid_cells"], "cloud_cover": rec["cloud_cover"]})
            return 1
        return _products(store, "landsat", items, landsat.MAX_PRODUCTS, one)
    return run


def _viirs(store: FeatureStore, now: dt.datetime):
    def run(client: HttpClient, fetch_id: str) -> SourceResult:
        granules = [dict(g, observed_at=g["observation_date"]) for g in viirs.search(client, now)]

        def one(granule):
            layers, rec = viirs.fetch_product(client, granule)
            store.write_product("viirs", rec, fetch_id=fetch_id, fetched_utc=now,
                                processing_version=viirs.PROCESSING_VERSION, n_cells=rec["cells"], layers=layers,
                                quality={k: rec[k] for k in ("valid_cells", "gap_filled_cells", "bytes_read")})
            return 1
        return _products(store, "viirs", granules, viirs.MAX_PRODUCTS, one)
    return run


@uses_context
def fetch_satellite_archive(window: TimeWindow, config, context: dict) -> dict:
    store, now = _open_store(config, context), now_utc()
    sources = {
        "geoscf_ana": run_source(store, "geoscf_ana", window, geoscf.POLICY, _geoscf_ana(store, now), now=now),
        "geoscf_fcst": run_source(store, "geoscf_fcst", window, geoscf.POLICY, _geoscf_fcst(store, now), now=now),
        "landsat": run_source(store, "landsat", window, landsat.POLICY, _landsat(store, now), now=now),
    }
    token = (os.environ.get("EARTHDATA_TOKEN") or "").strip()
    if token:
        sources["viirs"] = run_source(store, "viirs", window, viirs.POLICY, _viirs(store, now), now=now,
                                      headers={"Authorization": f"Bearer {token}"})
    else:
        sources["viirs"] = {"status": "disabled", "note": "EARTHDATA_TOKEN belum diatur"}
    return combine(sources)


@uses_context
def fetch_tropospheric_column(window: TimeWindow, config, context: dict) -> dict:
    store, now = _open_store(config, context), now_utc()
    return combine({"sentinel5p": run_source(store, "sentinel5p", window, sentinel5p.POLICY,
                                             _sentinel5p(store, now), now=now)})


@uses_context
def fetch_meteo(window: TimeWindow, config, context: dict) -> dict:
    store, now = _open_store(config, context), now_utc()
    return combine({"open_meteo": run_source(store, "open_meteo", window, open_meteo.POLICY,
                                             _meteo(store, now), now=now)})


# Kendali mutu ----------------------------------------------------------------
def source_availability(store: FeatureStore, tau: pd.Timestamp, as_of: dt.datetime | None = None) -> dict:
    """Data terbaru yang boleh dipakai pada τ per sumber, umurnya (jam), dan penanda ``available``."""
    s = store.s
    q = lambda sql, *p: store.conn.execute(sql.format(s=s), p).fetchone()  # noqa: E731
    out = {}
    weather = store.read_weather(as_of=as_of, since=tau - pd.Timedelta(days=3), until=tau + pd.Timedelta(hours=1))
    if not weather.empty:
        weather = weather[weather[[c for c in weather if c.endswith("_available")]].any(axis=1)]
        usable = weather[weather.time_utc.dt.floor("6h") + pd.Timedelta(hours=8) <= tau]
    latest = usable.time_utc.max() if not weather.empty and len(usable) else None
    out["open_meteo"] = {"latest": latest, "age_hours": (tau - latest) / pd.Timedelta(hours=1) if latest else None}
    row = q("""SELECT max(time_window_start) AS t FROM {s}.geoscf_ana_hourly
               WHERE available_at_utc <= %s AND (%s::timestamptz IS NULL OR fetched_utc <= %s)""", tau, as_of, as_of)
    latest = row["t"]
    out["geoscf_ana"] = {"latest": latest, "age_hours": (tau - pd.Timestamp(latest) - pd.Timedelta(hours=1))
                         / pd.Timedelta(hours=1) if latest else None}
    row = q("""SELECT max(init_utc) AS init, min(lead_hours) AS lead FROM {s}.geoscf_fcst_hourly
               WHERE time_window_start = %s AND available_at_utc <= %s
                 AND (%s::timestamptz IS NULL OR fetched_utc <= %s)""",
            tau - pd.Timedelta(hours=1), tau, as_of, as_of)
    out["geoscf_fcst"] = {"latest": row["init"], "available": row["init"] is not None,
                          "age_hours": None if row["lead"] is None else float(row["lead"])}
    for source in ("sentinel5p", "viirs", "landsat"):
        row = q("""SELECT max(observed_at) AS t FROM {s}.satellite_product
                   WHERE source = %s AND produced_at <= %s AND n_cells > 0
                     AND (%s::timestamptz IS NULL OR fetched_utc <= %s)""", source, tau, as_of, as_of)
        latest = row["t"]
        out[source] = {"latest": latest, "age_hours": (tau - pd.Timestamp(latest)) / pd.Timedelta(hours=1)
                       if latest else None}
    for source, info in out.items():
        if "available" not in info:
            limit = STALE_AFTER_HOURS[source]
            info["available"] = info["age_hours"] is not None and info["age_hours"] <= limit
            if info["age_hours"] is not None and info["age_hours"] > limit:
                info["stale"] = True
        if info["latest"] is not None:
            info["latest"] = pd.Timestamp(info["latest"]).isoformat()
        if info["age_hours"] is not None:
            info["age_hours"] = round(float(info["age_hours"]), 2)
    return out


@uses_context
def run_quality_control(window: TimeWindow, config, context: dict) -> dict:
    store = _open_store(config, context)
    availability = source_availability(store, pd.Timestamp(window.end))
    context["availability"] = availability
    missing = sorted(k for k, v in availability.items() if not v["available"])
    status = "ok" if not missing else ("unavailable" if len(missing) == len(availability) else "degraded")
    result = {"status": status, "time_utc": pd.Timestamp(window.end).isoformat(), "sources": availability}
    if missing:
        result["note"] = f"sumber ditandai available=false pada τ: {', '.join(missing)}"
    return result


# Penyelarasan dan penulisan ---------------------------------------------------
def sensor_stations(config, window: TimeWindow) -> pd.DataFrame:
    """Stasiun SPKU dengan pengukuran PM2.5/NO2 bersih pada time window, untuk ``dist_nearest_sensor_m``."""
    from spku.store import Store

    with Store(config.database, config.db_schema) as gt:
        rows = gt.read_measurements(window.start, window.end, ["pm25", "no2"], JAKARTA_BBOX.as_tuple())
    frame = pd.DataFrame(rows)
    if frame.empty:
        return pd.DataFrame(columns=["station_longitude", "station_latitude"])
    frame = frame[(frame.qc == "") & frame.value.gt(0) & frame.value.lt(999.99)]
    return frame.rename(columns={"lng": "station_longitude", "lat": "station_latitude"})[
        ["station_longitude", "station_latitude"]].drop_duplicates()


def build_feature_matrix(store: FeatureStore, window: TimeWindow, sensors: pd.DataFrame,
                         as_of: dt.datetime | None = None) -> pd.DataFrame:
    """FeatureMatrix seluruh sel aktif pada τ = akhir time window (kolom ``features.parquet``)."""
    from feature_matrix.grid import active_cells
    from feature_matrix.prepare_stgnn import check_grid_feature_batch, finalize_feature_batch, sensor_distances

    cells = active_cells()
    nodes = cells.assign(node_index=np.arange(len(cells)))
    tau = pd.Timestamp(window.end)
    reader = StoreSourceReader(store, as_of=as_of)
    batch = reader(nodes.grid_id.tolist(), tau, tau + pd.Timedelta(hours=1))
    check_grid_feature_batch(batch, nodes, tau, tau + pd.Timedelta(hours=1))
    static = nodes[["node_index", "grid_id"]].assign(dist_nearest_sensor_m=sensor_distances(nodes, sensors))
    return finalize_feature_batch(batch, static)


def feature_version(frame: pd.DataFrame) -> str:
    """SHA-256 isi FeatureMatrix: nama, tipe, dan nilai setiap kolom dalam urutan tetap."""
    digest = hashlib.sha256()
    for column in frame.columns:
        series = frame[column]
        digest.update(f"{column}:{series.dtype}\n".encode())
        if pd.api.types.is_datetime64_any_dtype(series):
            values = series.astype("int64").to_numpy() if series.notna().all() else \
                series.map(lambda t: -1 if pd.isna(t) else t.value).to_numpy("int64")
            digest.update(values.tobytes())
        elif pd.api.types.is_numeric_dtype(series) or pd.api.types.is_bool_dtype(series):
            digest.update(np.ascontiguousarray(series.to_numpy()).tobytes())
        else:
            digest.update("\x1f".join("\x00" if pd.isna(v) else str(v) for v in series).encode())
    return digest.hexdigest()


@uses_context
def align_spatiotemporal(window: TimeWindow, config, context: dict) -> dict:
    store = _open_store(config, context)
    built = now_utc()
    sensors = sensor_stations(config, window)
    matrix = build_feature_matrix(store, window, sensors, as_of=built)
    context.update(feature_matrix=matrix, built_utc=built, sensors=sensors)
    flags = [c for c in matrix.columns if c.endswith("_available")]
    coverage = {c[:-len("_available")]: round(float(matrix[c].mean()), 4) for c in flags}
    return {"status": "ok", "cells": len(matrix), "columns": matrix.shape[1], "sensor_stations": len(sensors),
            "availability_fraction": coverage}


def _sources_used(matrix: pd.DataFrame) -> dict:
    used = {}
    for prefix in ("ndvi", "ndbi", "no2_mol_m2", "ntl"):
        column = f"{prefix}_scene_id"
        if column in matrix:
            used[prefix] = sorted(matrix[column].dropna().unique().tolist())
    for column in ("weather_time_utc", "geoscf_pm25_time_window_start", "geoscf_fcst_pm25_init_utc"):
        if column in matrix:
            values = matrix[column].dropna().unique()
            used[column] = sorted(pd.Timestamp(v).isoformat() for v in values)
    return used


@uses_context
def write_features(window: TimeWindow, config, context: dict) -> dict:
    matrix = context.get("feature_matrix")
    if matrix is None:
        return {"status": "unavailable", "note": "FeatureMatrix tidak terbentuk pada align_spatiotemporal"}
    store = _open_store(config, context)
    version = feature_version(matrix)
    manifest = {
        "time_window_start": window.start, "time_utc": window.end, "feature_version": version,
        "built_utc": context["built_utc"], "run_id": context.get("run_id"), "cells": len(matrix),
        "columns": list(matrix.columns), "availability": context.get("availability", {}),
        "sources": {**_sources_used(matrix), "sensor_stations": context["sensors"][
            ["station_longitude", "station_latitude"]].to_numpy().tolist()}}
    directory = os.environ.get("FEATURE_STORE_DIR")
    if directory:
        folder = pathlib.Path(directory) / f"{window.start:%Y%m%dT%H%MZ}"
        folder.mkdir(parents=True, exist_ok=True)
        path = folder / "features.parquet"
        matrix.to_parquet(path, index=False, compression="zstd")
        manifest["parquet_path"] = str(path)
        (folder / "manifest.json").write_text(json.dumps(manifest, indent=2, default=str) + "\n", encoding="utf-8")
    created = store.write_feature_window(manifest)
    pruned = store.prune(now_utc())
    return {"status": "ok", "feature_version": version, "new_version": created, "cells": len(matrix),
            **({"parquet_path": manifest["parquet_path"]} if directory else {}),
            **({"pruned": pruned} if pruned else {})}


def read_features(store: FeatureStore, window: TimeWindow) -> tuple[pd.DataFrame, dict]:
    """Bangun ulang FeatureMatrix versi terakhir time window ``window`` dan periksa ``feature_version``-nya.

    Sumber dibaca as-of ``built_utc`` dengan himpunan stasiun yang tercatat pada manifest.
    """
    rows = store.feature_windows(window.start)
    if not rows:
        raise LookupError(f"tidak ada FeatureMatrix untuk {window}")
    manifest = rows[0]
    sensors = pd.DataFrame(manifest["sources"].get("sensor_stations", []),
                           columns=["station_longitude", "station_latitude"])
    matrix = build_feature_matrix(store, window, sensors, as_of=manifest["built_utc"])
    if feature_version(matrix) != manifest["feature_version"]:
        raise ValueError(f"feature_version {window} tidak cocok; data sumber berubah atau sudah dipangkas retensi")
    return matrix, manifest
