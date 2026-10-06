"""Dataset stasiun-jam terversi dan pembagian temporal yang dibekukan.

Membentuk ground truth PM2.5 dan NO2 per stasiun per time window satu jam
dari skema ``ground_truth`` (PostgreSQL) atau ekspor Parquet-nya, lalu
menulis dataset beserta manifest. Manifest membekukan aturan kendali mutu,
batas split pelatihan/validasi/pengujian, dan hash isi dataset, sehingga
setiap eksperimen dapat dijalankan ulang dari manifest yang sama
(acceptance criteria TI-AI-04; pembentukan split adalah lingkup TI-AI-03).

Kolom ``station_hour.parquet``:

    station_uuid, kode, name, type, kota, lat, lon, x, y,
    pollutant, window_start_utc, value_ugm3, n_readings, split,
    suspected_low_bias
"""

from __future__ import annotations

import datetime as dt
import hashlib
import json
import logging
import pathlib
from typing import Any

import numpy as np
import pandas as pd

from spatial_model.grid import GRID_CRS, to_grid_xy

log = logging.getLogger("spatial_model.baseline.dataset")

MANIFEST_VERSION = 1
DATASET_FILE = "station_hour.parquet"
MANIFEST_FILE = "manifest.json"
#: Kode parameter portal → penamaan Tabel 3.3.
METRIC_TO_POLLUTANT = {"PM25": "pm25", "NO2": "no2"}
SPLITS = ("train", "val", "test")


# ------------------------------------------------------------------ hashing


def sha256_file(path: pathlib.Path) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as fh:
        for block in iter(lambda: fh.read(1 << 20), b""):
            h.update(block)
    return h.hexdigest()


def content_sha256(df: pd.DataFrame) -> str:
    """Hash isi DataFrame yang tidak bergantung pada versi penulis Parquet."""
    ordered = df.sort_values(["pollutant", "station_uuid", "window_start_utc"]).reset_index(drop=True)
    ordered = ordered[sorted(ordered.columns)]
    h = hashlib.sha256()
    h.update(",".join(ordered.columns).encode())
    h.update(pd.util.hash_pandas_object(ordered, index=False).to_numpy().tobytes())
    return h.hexdigest()


# ------------------------------------------------------------------ sumber


def load_raw_from_export(
    export_dir: pathlib.Path, end_utc: str | None = None
) -> tuple[pd.DataFrame, pd.DataFrame, dict]:
    """Membaca station.parquet dan observation.parquet dari ekspor skema ground_truth."""
    export_dir = pathlib.Path(export_dir)
    stations = pd.read_parquet(export_dir / "station.parquet")
    observations = pd.read_parquet(
        export_dir / "observation.parquet", filters=[("metric", "in", list(METRIC_TO_POLLUTANT))]
    )
    if end_utc is not None:
        observations = observations[observations["ts_utc"] < pd.Timestamp(end_utc)]
    source: dict[str, Any] = {
        "type": "parquet_export",
        "path": export_dir.name,
        "query": {"metrics": list(METRIC_TO_POLLUTANT), "ts_utc_lt": end_utc},
        "files": {
            name: {"sha256": sha256_file(export_dir / name)}
            for name in ("station.parquet", "observation.parquet")
        },
    }
    manifest = export_dir / "manifest.json"
    if manifest.exists():
        meta = json.loads(manifest.read_text())
        source["export_snapshot_utc"] = meta.get("snapshot_utc")
        source["export_manifest_sha256"] = sha256_file(manifest)
        # Ekspor yang tidak utuh tidak boleh menjadi dasar dataset.
        for name, info in source["files"].items():
            expected = meta.get("files", {}).get(name, {}).get("sha256")
            if expected and expected != info["sha256"]:
                raise ValueError(f"{name}: SHA-256 tidak cocok dengan manifest ekspor")
    return stations, observations, source


def load_raw_from_postgres(
    dsn: str, end_utc: str, schema: str = "ground_truth"
) -> tuple[pd.DataFrame, pd.DataFrame, dict]:
    """Membaca tabel station dan observation dari PostgreSQL dalam sesi read-only.

    Hanya pengukuran dengan ``ts_utc < end_utc`` yang dibaca. Ini tidak
    menjamin hasil yang sama bila dibaca ulang: selama ±48 jam setelah batas,
    portal masih dapat menambah bacaan, dan bendera ``qc`` dapat berubah
    (teramati 6 Oktober 2026 pada DKI_PM25_85). Dataset beku adalah berkas
    hasil fungsi ini beserta manifest-nya, bukan kuerinya. DSN tidak pernah
    dicatat; manifest hanya memuat ``schema`` dan batas kueri.
    """
    import psycopg

    from contracts import JAKARTA_BBOX

    end = pd.Timestamp(end_utc)
    if end.tzinfo is None:
        raise ValueError("end_utc wajib memiliki zona waktu")
    with psycopg.connect(dsn, options="-c default_transaction_read_only=on") as conn:
        cur = conn.cursor()
        cur.execute(
            f"""SELECT uuid::text AS uuid, kode, initial, name, type, lat, lng, kecamatan, kota, active,
                       ST_Intersects(geom, ST_MakeEnvelope(%s, %s, %s, %s, 4326)) AS in_jakarta_bbox
                FROM {schema}.station""",
            JAKARTA_BBOX.as_tuple(),
        )
        stations = pd.DataFrame(cur.fetchall(), columns=[c.name for c in cur.description])
        cur.execute(
            f"""SELECT station_uuid::text AS station_uuid, metric, ts_utc, value, qc
                FROM {schema}.observation WHERE metric = ANY(%s) AND ts_utc < %s""",
            (list(METRIC_TO_POLLUTANT), end.to_pydatetime()),
        )
        observations = pd.DataFrame(cur.fetchall(), columns=[c.name for c in cur.description])
        cur.execute(f"SELECT MAX(id), MAX(migrated_utc) FROM {schema}.migration_log")
        migration = cur.fetchone()
    observations["ts_utc"] = pd.to_datetime(observations["ts_utc"], utc=True)
    observations["value"] = observations["value"].astype(float)
    source = {
        "type": "postgres",
        "schema": schema,
        "query": {"metrics": list(METRIC_TO_POLLUTANT), "ts_utc_lt": end.isoformat()},
        "snapshot_utc": dt.datetime.now(dt.timezone.utc).isoformat(timespec="seconds"),
        "raw_rows": {"station": int(len(stations)), "observation": int(len(observations))},
        "raw_ts_utc_range": [observations["ts_utc"].min().isoformat(), observations["ts_utc"].max().isoformat()],
        "latest_migration_log": {"id": migration[0], "migrated_utc": str(migration[1])},
    }
    return stations, observations, source


# ------------------------------------------------------------------ kendali mutu


def apply_qc(
    observations: pd.DataFrame, stations: pd.DataFrame, qc: dict, restrict_to_study_area: bool
) -> tuple[pd.DataFrame, dict[str, int]]:
    """Menyaring pengukuran sesuai aturan; mengembalikan data bersih dan jumlah baris terbuang per aturan."""
    df = observations.copy()
    df["pollutant"] = df["metric"].map(METRIC_TO_POLLUTANT)
    df = df.dropna(subset=["pollutant"])
    dropped: dict[str, int] = {}

    def drop(mask: pd.Series, rule: str) -> None:
        nonlocal df
        dropped[rule] = int(mask.sum())
        df = df[~mask]

    if restrict_to_study_area:
        inside = set(stations.loc[stations["in_jakarta_bbox"].astype(bool), "uuid"])
        drop(~df["station_uuid"].isin(inside), "outside_study_area")
    if qc.get("require_clean_flag", True):
        drop(df["qc"].fillna("") != "", "qc_flagged")
    drop(df["value"].isna(), "null_value")
    if qc.get("drop_nonpositive", True):
        drop(df["value"] <= 0, "nonpositive")
    if qc.get("sentinel_min") is not None:
        drop(df["value"] >= float(qc["sentinel_min"]), "sentinel")
    if qc.get("max_value_ugm3") is not None:
        drop(df["value"] > float(qc["max_value_ugm3"]), "above_max")

    by_kode = stations.set_index("kode")["uuid"]
    for rule in qc.get("exclude", []):
        kode = rule["kode"]
        if kode not in by_kode.index:
            log.warning("stasiun %s pada daftar pengecualian tidak ditemukan", kode)
            dropped[f"exclude:{kode}"] = 0
            continue
        mask = df["station_uuid"] == by_kode[kode]
        if rule.get("pollutant"):
            mask &= df["pollutant"] == rule["pollutant"]
        if rule.get("since_utc"):
            mask &= df["ts_utc"] >= pd.Timestamp(rule["since_utc"])
        drop(mask, f"exclude:{kode}")
    return df, dropped


def aggregate_hourly(df: pd.DataFrame, min_readings: int) -> pd.DataFrame:
    """Rata-rata bacaan bersih per stasiun, polutan, dan time window satu jam."""
    df = df.assign(window_start_utc=df["ts_utc"].dt.floor("h"))
    hourly = (
        df.groupby(["station_uuid", "pollutant", "window_start_utc"], as_index=False)
        .agg(value_ugm3=("value", "mean"), n_readings=("value", "size"))
    )
    return hourly[hourly["n_readings"] >= min_readings].reset_index(drop=True)


# ------------------------------------------------------------------ split


def compute_split_boundaries(windows: pd.Series, split_cfg: dict) -> dict[str, str]:
    """Batas split temporal pada hari UTC penuh.

    Mengembalikan ``start``, ``train_end``, ``val_end``, ``end`` (ISO 8601,
    batas atas eksklusif). train = [start, train_end), val = [train_end,
    val_end), test = [val_end, end).
    """
    first = windows.min()
    last = windows.max()
    start = first.floor("D")
    end = last.floor("D") + pd.Timedelta(days=1)
    if split_cfg.get("drop_partial_days", True):
        if first != start:
            start = start + pd.Timedelta(days=1)
        if last + pd.Timedelta(hours=1) != end:
            end = end - pd.Timedelta(days=1)
    n_days = int((end - start) / pd.Timedelta(days=1))
    if n_days < 3:
        raise ValueError(f"hanya {n_days} hari penuh; split pelatihan/validasi/pengujian butuh ≥ 3")

    if split_cfg.get("train_end") and split_cfg.get("val_end"):
        train_end = pd.Timestamp(split_cfg["train_end"])
        val_end = pd.Timestamp(split_cfg["val_end"])
    else:
        n_train = max(1, round(n_days * float(split_cfg["train_fraction"])))
        n_val = max(1, round(n_days * float(split_cfg["val_fraction"])))
        if n_train + n_val >= n_days:
            n_train = n_days - n_val - 1
        train_end = start + pd.Timedelta(days=n_train)
        val_end = train_end + pd.Timedelta(days=n_val)
    if not (start < train_end < val_end < end):
        raise ValueError("batas split tidak berurutan")
    return {k: v.isoformat() for k, v in
            {"start": start, "train_end": train_end, "val_end": val_end, "end": end}.items()}


def assign_split(windows: pd.Series, boundaries: dict[str, str]) -> pd.Series:
    b = {k: pd.Timestamp(v) for k, v in boundaries.items()}
    split = pd.Series(pd.NA, index=windows.index, dtype="object")
    split[(windows >= b["start"]) & (windows < b["train_end"])] = "train"
    split[(windows >= b["train_end"]) & (windows < b["val_end"])] = "val"
    split[(windows >= b["val_end"]) & (windows < b["end"])] = "test"
    return split


# ------------------------------------------------------------------ build


def build_dataset(
    config: dict,
    stations: pd.DataFrame,
    observations: pd.DataFrame,
    source: dict,
    out_dir: pathlib.Path,
    code_version: dict | None = None,
) -> dict:
    """Membentuk dataset stasiun-jam dan menulis ``station_hour.parquet`` serta ``manifest.json``."""
    ds_cfg = config["dataset"]
    out_dir = pathlib.Path(out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)

    stations = stations.rename(columns={"lng": "lon"})
    clean, dropped = apply_qc(observations, stations, ds_cfg.get("qc", {}), ds_cfg.get("restrict_to_study_area", True))
    clean = clean[clean["pollutant"].isin(ds_cfg["pollutants"])]
    hourly = aggregate_hourly(clean, int(ds_cfg.get("min_readings_per_window", 1)))

    x, y = to_grid_xy(stations["lon"].to_numpy(), stations["lat"].to_numpy())
    meta = stations.assign(x=x, y=y)[["uuid", "kode", "name", "type", "kota", "lat", "lon", "x", "y"]]
    hourly = hourly.merge(meta.rename(columns={"uuid": "station_uuid"}), on="station_uuid", how="left")

    boundaries = compute_split_boundaries(hourly["window_start_utc"], ds_cfg["split"])
    hourly["split"] = assign_split(hourly["window_start_utc"], boundaries)
    outside = int(hourly["split"].isna().sum())
    hourly = hourly.dropna(subset=["split"])
    columns = ["station_uuid", "kode", "name", "type", "kota", "lat", "lon", "x", "y",
               "pollutant", "window_start_utc", "value_ugm3", "n_readings", "split"]
    hourly = hourly[columns]
    hourly = hourly.sort_values(["pollutant", "window_start_utc", "kode"]).reset_index(drop=True)

    hourly["suspected_low_bias"] = False
    suspects = flag_low_bias(hourly, ds_cfg.get("low_bias_suspect"))
    hourly.loc[hourly["station_uuid"].isin(suspects["station_uuid"]), "suspected_low_bias"] = True

    path = out_dir / DATASET_FILE
    hourly.to_parquet(path, index=False)
    content_hash = content_sha256(hourly)

    summary = (
        hourly.groupby(["split", "pollutant"])
        .agg(rows=("value_ugm3", "size"), stations=("station_uuid", "nunique"),
             windows=("window_start_utc", "nunique"))
        .reset_index()
    )
    manifest = {
        "manifest_version": MANIFEST_VERSION,
        "dataset_version": f"sh-{content_hash[:12]}",
        "created_utc": dt.datetime.now(dt.timezone.utc).isoformat(timespec="seconds"),
        "source": source,
        "code": code_version or {},
        "grid": {"crs": GRID_CRS, "time_window": ds_cfg.get("time_window", "1h"),
                 "window_label": "start", "timezone": "UTC"},
        "definition": {
            "pollutants": list(ds_cfg["pollutants"]),
            "unit": "µg/m³",
            "aggregation": "mean of clean readings in [window_start, window_start + 1h)",
            "min_readings_per_window": int(ds_cfg.get("min_readings_per_window", 1)),
            "restrict_to_study_area": bool(ds_cfg.get("restrict_to_study_area", True)),
            "qc": ds_cfg.get("qc", {}),
        },
        "qc_dropped_rows": dropped,
        "suspected_low_bias_stations": suspects.to_dict(orient="records"),
        "rows_outside_split_range": outside,
        "split": {"scheme": "temporal_blocks", "boundaries": boundaries, "config": ds_cfg["split"]},
        "summary": summary.to_dict(orient="records"),
        "files": {DATASET_FILE: {"sha256": sha256_file(path), "content_sha256": content_hash,
                                  "rows": int(len(hourly))}},
    }
    (out_dir / MANIFEST_FILE).write_text(json.dumps(manifest, indent=2, ensure_ascii=False, default=str))
    log.info("dataset %s: %d baris stasiun-jam ditulis ke %s", manifest["dataset_version"], len(hourly), out_dir)
    return manifest


def flag_low_bias(hourly: pd.DataFrame, rule: dict | None) -> pd.DataFrame:
    """Stasiun yang diduga bias rendah (DATA.md): tidak dibuang, hanya ditandai untuk analisis.

    Rerata dihitung dari split pelatihan saja agar penandaan tidak memakai
    informasi split validasi/uji.
    """
    empty = pd.DataFrame(columns=["station_uuid", "kode", "mean_ugm3", "n"])
    if not rule:
        return empty
    sub = hourly[(hourly["split"] == rule.get("split", "train"))
                 & (hourly["pollutant"] == rule.get("pollutant", "pm25"))]
    if rule.get("kode_prefix"):
        sub = sub[sub["kode"].astype(str).str.startswith(rule["kode_prefix"])]
    if rule.get("station_type"):
        sub = sub[sub["type"] == rule["station_type"]]
    stats = sub.groupby(["station_uuid", "kode"], as_index=False).agg(
        mean_ugm3=("value_ugm3", "mean"), n=("value_ugm3", "size"))
    stats = stats[stats["mean_ugm3"] < float(rule["max_mean_ugm3"])]
    return stats.sort_values("mean_ugm3").reset_index(drop=True) if len(stats) else empty


def load_dataset(manifest_path: pathlib.Path) -> tuple[pd.DataFrame, dict]:
    """Membaca dataset dari manifest dan memastikan isinya tidak berubah sejak dibekukan."""
    manifest_path = pathlib.Path(manifest_path)
    manifest = json.loads(manifest_path.read_text())
    info = manifest["files"][DATASET_FILE]
    df = pd.read_parquet(manifest_path.parent / DATASET_FILE)
    actual = content_sha256(df)
    if actual != info["content_sha256"]:
        raise ValueError(
            f"isi {DATASET_FILE} tidak cocok dengan manifest ({actual[:12]} ≠ {info['content_sha256'][:12]})"
        )
    # Split dihitung ulang dari batas yang dibekukan sebagai pemeriksaan konsistensi.
    recomputed = assign_split(df["window_start_utc"], manifest["split"]["boundaries"])
    if not (recomputed.astype(str) == df["split"].astype(str)).all():
        raise ValueError("kolom split tidak sesuai batas split pada manifest")
    return df, manifest
