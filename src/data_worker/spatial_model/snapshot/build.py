"""Membangun snapshot dataset terversi (TI-AI-03) dari ekspor ground truth dan ``dataset_processed``.

Isi folder snapshot:

    manifest.json, CHECKSUMS.sha256, README.md, config.toml
    labels.parquet               label stasiun-jam bersih beserta alasan eksklusi
    stations.parquet             stasiun, sel, grup, split stasiun, penanda bias rendah
    splits/{train,val,test}.parquet, splits/loso_folds.parquet, splits/loso_sensor_distance.parquet
    split_spec.json, split_stats.md, exclusion_rules.md, normalization.json
    leakage_report.{json,md}
    stgnn/                       FeatureMatrix ST-GNN (TI-AI-02): features, nodes, edges, labels, manifest
    source/                      salinan ekspor ground truth yang dibekukan

Tidak ada cap waktu pembangunan di dalam berkas, sehingga dua pembangunan dari
commit dan masukan yang sama menghasilkan checksum identik.
"""

from __future__ import annotations

import hashlib
import json
import platform
import shutil
import subprocess
import tomllib
from pathlib import Path

import numpy as np
import pandas as pd

from feature_matrix.grid import active_cells
from feature_matrix.labels import REASONS, LabelRules, annotate_readings, station_cells
from feature_matrix.prepare_stgnn import (LAND_AGES, LAND_VALUES, SENSOR_FEATURES, TEMPORAL_AGES, TEMPORAL_VALUES,
                                          build_grid_graph, export_stgnn_inputs, prepare_stgnn_labels, stgnn_hash)
from feature_matrix.quality import write_report as write_feature_report
from feature_matrix.sources import SourceReader

from . import docs, leakage
from .split import (SPLITS, block_gaps_hours, loso_folds, loso_sensor_distance, period_of, split_groups,
                    station_groups)

PACKAGE = Path(__file__).resolve().parent
DEFAULT_CONFIG = PACKAGE / "configs" / "ds-v0.1.0.toml"
METRIC_OF = {"pm25": "PM25", "no2": "NO2"}
SOURCE_FILES = ("station.parquet", "observation.parquet", "manifest.json")
STGNN_SPLIT = {"train": "train", "val": "validation", "test": "test"}


def load_config(path: Path | str = DEFAULT_CONFIG) -> tuple[dict, bytes]:
    raw = Path(path).read_bytes()
    return tomllib.loads(raw.decode("utf-8")), raw


def label_rules(cfg: dict) -> LabelRules:
    lab = cfg["labels"]
    return LabelRules(
        sentinel=float(lab["sentinel"]), excluded_stations=tuple(lab["excluded_stations"]),
        excluded_from=tuple((r["kode"], r["metric"], r["since"]) for r in lab.get("excluded_from", [])),
        stuck_run_min=lab.get("stuck_run_min"), require_complete_hour=bool(lab.get("require_complete_hour")))


def _git() -> dict:
    def run(*args):
        return subprocess.run(["git", *args], cwd=PACKAGE, capture_output=True, text=True).stdout.strip()
    data_worker = PACKAGE.parents[1]
    return dict(commit=run("rev-parse", "HEAD"),
                dirty=bool(run("status", "--porcelain", "--", str(data_worker))))


def _versions() -> dict:
    import pyarrow
    import pyproj
    import shapely
    return dict(python=platform.python_version(), pandas=pd.__version__, numpy=np.__version__,
                pyarrow=pyarrow.__version__, pyproj=pyproj.__version__, shapely=shapely.__version__)


# Label --------------------------------------------------------------------------------
def station_hour_labels(readings: pd.DataFrame, cfg: dict) -> pd.DataFrame:
    """Satu baris per (stasiun, time window): nilai, jumlah pembacaan, alasan eksklusi per target."""
    scope = cfg["scope"]
    r = readings.assign(time_window_start=readings.ts_utc.dt.floor("h").astype("datetime64[ns, UTC]"))
    r = r[(r.time_window_start >= pd.Timestamp(scope["first_window"]))
          & (r.time_window_start < pd.Timestamp(scope["end_window"]))]
    rank = {reason: i for i, reason in enumerate(REASONS)}
    out = None
    for target in scope["targets"]:
        m = r[r.metric == METRIC_OF[target]]
        valid = m[m.exclusion_reason.isna()]
        agg = m.groupby(["station_uuid", "time_window_start"]).agg(**{f"{target}_n_raw": ("value", "size")})
        agg[f"{target}_ugm3"] = valid.groupby(["station_uuid", "time_window_start"]).value.mean()
        agg[f"{target}_n_obs"] = valid.groupby(["station_uuid", "time_window_start"]).size()
        agg[f"{target}_n_obs"] = agg[f"{target}_n_obs"].fillna(0).astype("int64")
        bad = m[m.exclusion_reason.notna()].assign(_rank=lambda d: d.exclusion_reason.map(rank))
        first = bad.sort_values("_rank").groupby(["station_uuid", "time_window_start"]).exclusion_reason.first()
        agg[f"{target}_exclusion_reason"] = first.reindex(agg.index).astype("string")
        agg.loc[agg[f"{target}_ugm3"].notna(), f"{target}_exclusion_reason"] = pd.NA
        out = agg if out is None else out.join(agg, how="outer")
    out = out.reset_index()
    for target in scope["targets"]:
        for col in (f"{target}_n_raw", f"{target}_n_obs"):
            out[col] = out[col].fillna(0).astype("int64")
    out["time_utc"] = out.time_window_start + pd.Timedelta(hours=1)
    return out


def low_bias_stations(labels: pd.DataFrame, stations: pd.DataFrame, rule: dict) -> set[str]:
    """Stasiun dengan rerata PM2.5 blok train di bawah ambang (hanya data blok train)."""
    sub = labels[(labels.period == "train") & labels.pm25_ugm3.notna()]
    sub = sub[sub.kode.str.startswith(rule["kode_prefix"]).fillna(False)
              & sub.station_type.eq(rule["station_type"]).fillna(False)]
    means = sub.groupby("station_uuid").pm25_ugm3.mean()
    return set(means[means < float(rule["max_mean_ugm3"])].index)


# Node dan normalisasi -----------------------------------------------------------------
def node_splits(nodes: pd.DataFrame, stations: pd.DataFrame, hops: int) -> pd.Series:
    """Split stasiun setiap node: split grup yang sel stasiunnya berjarak ≤ ``hops`` node; ``mixed`` bila lebih dari satu."""
    rc = nodes.grid_id.str.extract(r"^r(\d+)_c(\d+)$").astype(int).to_numpy()
    seen: dict[int, set[str]] = {i: set() for i in range(len(nodes))}
    cells = stations.groupby("grid_id").station_split.agg(set)
    pos = {tuple(p): i for i, p in enumerate(rc)}
    for grid_id, splits in cells.items():
        r, c = map(int, grid_id[1:].split("_c"))
        for dr in range(-hops, hops + 1):
            for dc in range(-hops, hops + 1):
                i = pos.get((r + dr, c + dc))
                if i is not None:
                    seen[i] |= splits
    return pd.Series([next(iter(s)) if len(s) == 1 else "mixed" for s in seen.values()], index=nodes.index)


def fit_normalization(features_dir: Path, ds_labels: pd.DataFrame, train_nodes: set[int], last_train_tau,
                      first_train_tau) -> dict:
    """Statistik fitur dan target yang di-fit hanya pada data train (TI-AI-03 K4)."""
    columns = ["node_index", "time_utc", *TEMPORAL_VALUES, *TEMPORAL_AGES, *LAND_VALUES, *LAND_AGES,
               *SENSOR_FEATURES]
    f = pd.read_parquet(features_dir / "features.parquet", columns=columns)
    rows = f[f.node_index.isin(train_nodes) & (f.time_utc <= last_train_tau)]
    stats = {}
    for col in [*TEMPORAL_VALUES, *TEMPORAL_AGES, *LAND_VALUES, *LAND_AGES, *SENSOR_FEATURES]:
        v = rows[col].astype("float64")
        stats[col] = dict(n=int(v.notna().sum()), missing_fraction=round(float(v.isna().mean()), 6),
                          median=float(v.median()), mean=float(v.mean()), std=float(v.std(ddof=0)))
    geos_cols = f[["node_index", "time_utc", "geoscf_pm25_ugm3", "geoscf_no2_ugm3"]]

    def target_stats(rows: pd.DataFrame, t: str) -> dict:
        v = rows[f"{t}_ugm3"].dropna()
        j = rows.loc[v.index, ["node_index", "time_utc", f"{t}_ugm3"]].merge(geos_cols, on=["node_index", "time_utc"])
        return dict(n=int(len(v)), mean=float(v.mean()), std=float(v.std(ddof=0)),
                    geoscf_mean=float(j[f"geoscf_{t}_ugm3"].mean()),
                    ratio_geoscf_to_spku=float(j[f"geoscf_{t}_ugm3"].mean() / v.mean()))

    pm25_train = ds_labels[ds_labels.pm25_split.eq("train").fillna(False)]
    no2_train = ds_labels[ds_labels.no2_split.eq("train").fillna(False)]
    targets = dict(pm25=dict(protocol="fixed_split", **target_stats(pm25_train, "pm25")),
                   no2=dict(protocol="loso; statistik per fold tanpa grup yang ditahan, blok train",
                            per_held_out_group={g: target_stats(no2_train[no2_train.group_id != g], "no2")
                                                for g in sorted(no2_train.group_id.unique())}))
    return dict(
        description="Statistik normalisasi. Fitur: jam masukan blok train pada node grup train split tetap (fitur "
                    "non-sensor tidak memuat label, sehingga dapat dipakai pula pada fold LOSO). Target dan rasio "
                    "GEOS-CF/SPKU: PM2.5 dari split train (stasiun train × blok train); NO2 per fold LOSO dari blok "
                    "train tanpa grup yang ditahan. Rekayasa fitur (log1p umur, sin/cos arah angin) dilakukan model.",
        fit_rows=dict(feature_rows=int(len(rows)), nodes=int(rows.node_index.nunique()),
                      first_time_utc=str(rows.time_utc.min()), last_time_utc=str(rows.time_utc.max()),
                      label_first_time_utc=str(first_train_tau), label_last_time_utc=str(last_train_tau)),
        features=stats, targets=targets)


# Build --------------------------------------------------------------------------------
def build_snapshot(ground_truth_dir: Path | str, out_dir: Path | str, config_path: Path | str = DEFAULT_CONFIG,
                   dataset_processed: Path | str | None = None) -> dict:
    cfg, cfg_raw = load_config(config_path)
    gt, out = Path(ground_truth_dir), Path(out_dir)
    if out.exists() and any(out.iterdir()):
        raise FileExistsError(f"{out} tidak kosong; snapshot tidak ditimpa")
    out.mkdir(parents=True, exist_ok=True)
    (out / "splits").mkdir()
    scope, feat = cfg["scope"], cfg["features"]
    blocks = {k: tuple(v) for k, v in cfg["blocks"].items() if k in SPLITS}
    L = int(feat["lag_windows"])

    # 1. Pembacaan beranotasi → label stasiun-jam.
    readings, raw_stations = annotate_readings(gt, label_rules(cfg), until=scope["readings_until"])
    labels = station_hour_labels(readings, cfg)

    # 2. Stasiun berlabel → sel, grup, split stasiun.
    has_label = labels[[f"{t}_ugm3" for t in scope["targets"]]].notna().any(axis=1)
    usable = raw_stations[raw_stations.uuid.isin(set(labels.loc[has_label, "station_uuid"]))]
    located = station_cells(usable).rename(columns={"type": "station_type"})
    valid = readings[readings.exclusion_reason.isna()]
    grouped = station_groups(located, valid, cfg["groups"]["max_distance_m"],
                             cfg["groups"]["duplicate_identical_fraction"])
    groups = split_groups(grouped, cfg["station_split"]["fractions"], cfg["station_split"]["strata"], cfg["seed"])
    stations = grouped.merge(groups[["group_id", "station_split"]], on="group_id", how="left")

    labels = labels[labels.station_uuid.isin(set(stations.uuid))].copy()
    labels["period"] = period_of(labels.time_window_start, blocks)
    meta = stations.set_index("uuid")
    for col, src in (("kode", "kode"), ("station_type", "station_type"), ("kota", "kota"), ("grid_id", "grid_id"),
                     ("group_id", "group_id"), ("station_split", "station_split")):
        labels[col] = labels.station_uuid.map(meta[src]).astype("string")
    low = low_bias_stations(labels, stations, cfg["labels"]["low_bias_suspect"])
    stations["suspected_low_bias"] = stations.uuid.isin(low)
    labels["suspected_low_bias"] = labels.station_uuid.isin(low)
    # PM2.5 blind ganda: stasiun split s × blok s. NO2: blok temporal (evaluasi LOSO).
    labels["pm25_split"] = labels.station_split.where(labels.station_split == labels.period).astype("string")
    labels["no2_split"] = labels.period.where(labels.period != "gap").astype("string")
    labels.loc[labels.pm25_ugm3.isna(), "pm25_split"] = pd.NA
    labels.loc[labels.no2_ugm3.isna(), "no2_split"] = pd.NA
    label_cols = ["station_uuid", "kode", "station_type", "kota", "grid_id", "group_id", "station_split",
                  "time_window_start", "time_utc", "period",
                  "pm25_ugm3", "pm25_n_obs", "pm25_n_raw", "pm25_exclusion_reason", "pm25_split",
                  "no2_ugm3", "no2_n_obs", "no2_n_raw", "no2_exclusion_reason", "no2_split", "suspected_low_bias"]
    labels = labels[label_cols].sort_values(["time_window_start", "kode"]).reset_index(drop=True)
    labels["period"] = labels.period.astype("string")

    # 3. FeatureMatrix ST-GNN (TI-AI-02) pada graf seluruh stasiun berlabel.
    usable_rows = labels[labels.pm25_ugm3.notna() | labels.no2_ugm3.notna()]
    st_meta = stations.set_index("uuid")
    targets = pd.DataFrame({
        "station_uuid": usable_rows.station_uuid.astype(str).to_numpy(),
        "station_name": usable_rows.station_uuid.map(st_meta["name"]).to_numpy(),
        "station_code": usable_rows.kode.astype(str).to_numpy(),
        "station_type": usable_rows.station_type.astype(str).to_numpy(),
        "grid_id": usable_rows.grid_id.astype(str).to_numpy(),
        "time_utc": usable_rows.time_utc.to_numpy(),
        "station_latitude": usable_rows.station_uuid.map(st_meta["lat"]).to_numpy(),
        "station_longitude": usable_rows.station_uuid.map(st_meta["lng"]).to_numpy(),
        "station_grid_distance_m": usable_rows.station_uuid.map(st_meta["station_grid_distance_m"]).to_numpy(),
        "target_window_start_utc": usable_rows.time_window_start.to_numpy(),
        "target_pm25": usable_rows.pm25_ugm3.to_numpy(), "target_no2": usable_rows.no2_ugm3.to_numpy(),
        "target_pm25_n_readings": usable_rows.pm25_n_obs.to_numpy(),
        "target_no2_n_readings": usable_rows.no2_n_obs.to_numpy()})
    targets["time_utc"] = pd.to_datetime(targets.time_utc, utc=True).astype("datetime64[ns, UTC]")
    targets["target_window_start_utc"] = pd.to_datetime(targets.target_window_start_utc, utc=True) \
        .astype("datetime64[ns, UTC]")
    nodes, edges = build_grid_graph(active_cells(), sorted(targets.grid_id.unique()), int(feat["context_hops"]))
    tau = {k: (pd.Timestamp(a) + pd.Timedelta(hours=1), pd.Timestamp(b) + pd.Timedelta(hours=1))
           for k, (a, b) in blocks.items()}
    stgnn_labels = prepare_stgnn_labels(targets, nodes, tau["val"][0], tau["test"][0])
    key = labels.set_index(["station_uuid", "time_utc"]).pm25_split
    split = pd.MultiIndex.from_frame(stgnn_labels[["station_uuid", "time_utc"]].astype({"station_uuid": str}))
    stgnn_labels["split"] = pd.Series(key.reindex(split).to_numpy(), index=stgnn_labels.index) \
        .map(STGNN_SPLIT).fillna("none").astype("string")
    train_sensors = stations[stations.station_split == "train"].rename(
        columns={"lng": "station_longitude", "lat": "station_latitude"})
    reader_args = dict(viirs_cell_rule=feat["viirs_cell_rule"], weather_delay_hours=feat["weather_delay_hours"])
    reader = SourceReader(dataset_processed, **reader_args) if dataset_processed else SourceReader(**reader_args)
    sources = {f"dataset_processed/{p.relative_to(reader.root).as_posix()}": stgnn_hash(p)
               for p in reader.input_files()}
    stgnn_dir = out / "stgnn"
    export_stgnn_inputs(nodes, edges, stgnn_labels, reader, stgnn_dir, window=L, sensors=train_sensors,
                        provenance=dict(sources_sha256=sources, viirs_cell_rule=feat["viirs_cell_rule"],
                                        weather_delay_hours=feat["weather_delay_hours"],
                                        weather_run_interval_hours=reader.weather_run_interval_hours,
                                        snapshot=cfg["version"],
                                        sensor_set="stasiun split train ds-v0.1.0",
                                        split_policy="split = PM2.5 blind ganda ds-v0.1.0 (stasiun x blok); "
                                                     "none = di luar split"))
    write_feature_report(stgnn_dir)
    labels["node_index"] = labels.grid_id.map(nodes.set_index("grid_id").node_index).astype("Int64")

    # 4. Split, fold LOSO, jarak sensor per fold.
    key_cols = ["station_uuid", "kode", "group_id", "station_type", "time_window_start", "time_utc", "node_index"]
    split_frames = {}
    for s in SPLITS:
        parts = []
        for target in scope["targets"]:
            protocol = "fixed_split" if "fixed_split" in cfg["evaluation"][target] else "temporal_block_loso"
            sel = labels[labels[f"{target}_split"].eq(s).fillna(False)][key_cols + [f"{target}_ugm3"]]
            parts.append(sel.rename(columns={f"{target}_ugm3": "value_ugm3"}).assign(target=target, protocol=protocol))
        frame = pd.concat(parts, ignore_index=True)[["target", "protocol", *key_cols, "value_ugm3"]]
        frame = frame.sort_values(["target", "time_window_start", "kode"]).reset_index(drop=True)
        frame.to_parquet(out / "splits" / f"{s}.parquet", index=False)
        split_frames[s] = frame
    folds = loso_folds(labels, stations, scope["targets"])
    folds.to_parquet(out / "splits" / "loso_folds.parquet", index=False)
    loso_dist = loso_sensor_distance(nodes, stations)
    loso_dist.to_parquet(out / "splits" / "loso_sensor_distance.parquet", index=False)

    # 5. Normalisasi (train saja).
    nsplit = node_splits(nodes, stations, int(feat["context_hops"]))
    train_nodes = set(nodes.node_index[nsplit == "train"])
    norm = fit_normalization(stgnn_dir, labels, train_nodes, tau["train"][1] - pd.Timedelta(hours=1),
                             tau["train"][0])
    norm["node_split_counts"] = nsplit.value_counts().to_dict()
    (out / "normalization.json").write_text(json.dumps(norm, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")

    # 6. Tabel stasiun, label, salinan sumber dan konfigurasi.
    station_cols = ["uuid", "kode", "initial", "name", "station_type", "kota", "lat", "lng", "grid_id",
                    "station_grid_distance_m", "group_id", "group_reason", "station_split", "suspected_low_bias"]
    stations = stations[station_cols].sort_values("kode").reset_index(drop=True)
    stations["node_index"] = stations.grid_id.map(nodes.set_index("grid_id").node_index).astype("int64")
    stations.to_parquet(out / "stations.parquet", index=False)
    labels.to_parquet(out / "labels.parquet", index=False)
    (out / "source").mkdir()
    for name in SOURCE_FILES:
        shutil.copyfile(gt / name, out / "source" / name)
    (out / "config.toml").write_bytes(cfg_raw)

    # 7. Spesifikasi split, laporan kebocoran, statistik, aturan eksklusi.
    gaps = block_gaps_hours(blocks)
    split_spec = dict(
        version=cfg["version"], seed=cfg["seed"], lag_windows=L,
        blocks={k: dict(first_window=v[0], end_window_exclusive=v[1]) for k, v in blocks.items()},
        gaps_hours=gaps, min_gap_hours=max(L, int(cfg["blocks"]["min_gap_hours"])),
        groups=cfg["groups"], station_split=cfg["station_split"], evaluation=cfg["evaluation"],
        rules=[
            "Grup stasiun: sel 100 m sama, jarak < 300 m (transitif), atau duplikat penyedia.",
            "Split stasiun per grup: urut strata (tipe stasiun, kota), acak dalam strata dengan seed, "
            "label posisi sistematis (systematic_labels).",
            "PM2.5: split s = stasiun split s × blok s (blind ganda); baris lain tidak masuk split.",
            "NO2: blok temporal untuk seluruh stasiun NO2; evaluasi spasial memakai fold LOSO per grup.",
            "Fitur berbasis sensor (dist_nearest_sensor_m) dari stasiun train; per fold LOSO tanpa grup yang ditahan.",
        ],
        group_assignment=groups.drop(columns="order").to_dict(orient="records"))
    (out / "split_spec.json").write_text(json.dumps(split_spec, indent=2, ensure_ascii=False) + "\n",
                                         encoding="utf-8")
    report = leakage.leakage_report(out, cfg, labels, stations, split_frames, folds, loso_dist, nodes, nsplit,
                                    norm, blocks, L)
    (out / "leakage_report.json").write_text(json.dumps(report, indent=2, ensure_ascii=False) + "\n",
                                             encoding="utf-8")
    (out / "leakage_report.md").write_text(leakage.report_markdown(report), encoding="utf-8")
    (out / "split_stats.md").write_text(docs.split_stats(labels, stations, split_frames, folds, blocks),
                                        encoding="utf-8")
    counts = docs.exclusion_counts(readings, raw_stations, cfg)
    (out / "exclusion_rules.md").write_text(docs.exclusion_rules(cfg, counts), encoding="utf-8")

    # 8. Manifest, README, checksum.
    export_manifest = json.loads((gt / "manifest.json").read_text(encoding="utf-8"))
    manifest = dict(
        version=cfg["version"],
        description="Snapshot dataset awal Spatial Downscaling Model (PM2.5 dan NO2), immutable setelah freeze.",
        time_range=dict(first_window=scope["first_window"], end_window_exclusive=scope["end_window"],
                        window="1 jam, UTC, label awal jam (time_window_start); time_utc = akhir jam = "
                               "waktu inferensi", readings_until=scope["readings_until"]),
        spatial=dict(grid_crs="EPSG:32748", cell_m=100, grid="445 x 445, kiri atas (676900, 9336600), "
                     "ID rRRRR_cCCCC baris 0 di utara", study_area="station.in_jakarta_bbox "
                     "(106,68–106,98 BT; 6,38–6,08 LS)", nodes=int(len(nodes)), edges=int(len(edges)),
                     label_cells=int(stations.grid_id.nunique())),
        targets={t: dict(column=f"{t}_ugm3", unit="µg/m³ (portal tidak menyatakan satuan; terverifikasi terhadap "
                                                    "ISPU portal, scripts/check_ispu_units.py)",
                         evaluation=cfg["evaluation"][t]) for t in scope["targets"]},
        features=dict(spec="stgnn/feature_spec.json", lag_windows=L, context_hops=feat["context_hops"],
                      viirs_cell_rule=feat["viirs_cell_rule"],
                      weather_policy=f"Open-Meteo as-of: valid time terbaru dengan floor(t, 6 jam) + "
                                     f"{feat['weather_delay_hours']:g} jam <= waktu inferensi",
                      feature_version=json.loads((stgnn_dir / "manifest.json").read_text(encoding="utf-8"))["outputs"][
                          "features.parquet"][:16],
                      model_inputs=dict(temporal=[*TEMPORAL_VALUES, *TEMPORAL_AGES],
                                        land_static=[*LAND_VALUES, *LAND_AGES], sensor_static=SENSOR_FEATURES),
                      sensor_set="stasiun split train; per fold LOSO di splits/loso_sensor_distance.parquet",
                      static_policy="Landsat dan VIIRS as-of: pengamatan valid terakhir dengan produced_at <= "
                                    "waktu inferensi; tidak ada komposit statis yang di-fit"),
        counts=docs.counts(labels, stations, split_frames, folds),
        seed=cfg["seed"], split_rules=split_spec["rules"], blocks=split_spec["blocks"], gaps_hours=gaps,
        exclusion_rules=dict(config=cfg["labels"], counts=counts),
        sources=dict(
            ground_truth=dict(export=gt.name, snapshot_utc=export_manifest.get("snapshot_utc"),
                              files={n: stgnn_hash(gt / n) for n in SOURCE_FILES}, copy="source/"),
            dataset_processed=sources),
        leakage=dict(passed=report["passed"], checks={c["id"]: c["status"] for c in report["checks"]}),
        code=_git(), libraries=_versions(),
        config=dict(path="config.toml", sha256=hashlib.sha256(cfg_raw).hexdigest()),
        separation="Snapshot eksperimen immutable dan terversi; bukan cache operasional "
                   "(pollution.* / ground_truth.* di PostgreSQL yang terus berubah).",
    )
    files = sorted(p for p in out.rglob("*") if p.is_file() and p.name not in ("manifest.json", "CHECKSUMS.sha256",
                                                                                "README.md"))
    manifest["files"] = {p.relative_to(out).as_posix(): dict(sha256=stgnn_hash(p), bytes=p.stat().st_size)
                         for p in files}
    (out / "manifest.json").write_text(json.dumps(manifest, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    (out / "README.md").write_text(docs.readme(manifest, report), encoding="utf-8")
    write_checksums(out)
    return manifest


def write_checksums(out: Path) -> None:
    files = sorted(p for p in out.rglob("*") if p.is_file() and p.name != "CHECKSUMS.sha256")
    lines = [f"{stgnn_hash(p)}  {p.relative_to(out).as_posix()}" for p in files]
    (out / "CHECKSUMS.sha256").write_text("\n".join(lines) + "\n", encoding="utf-8")


def verify_checksums(out: Path | str) -> list[str]:
    """Berkas yang hilang atau berubah terhadap CHECKSUMS.sha256 (kosong = utuh)."""
    out = Path(out)
    bad = []
    for line in (out / "CHECKSUMS.sha256").read_text(encoding="utf-8").splitlines():
        digest, name = line.split("  ", 1)
        path = out / name
        if not path.exists() or stgnn_hash(path) != digest:
            bad.append(name)
    return bad
