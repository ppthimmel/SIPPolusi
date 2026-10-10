"""Uji snapshot dataset (TI-AI-03): determinisme, kebocoran, grup, split, aturan label.

Ekspor ground truth sintetis kecil (stasiun pada koordinat nyata, 19–23 Sep)
dengan konfigurasi berblok pendek; fitur dibaca dari ``dataset_processed`` repo.
"""

from __future__ import annotations

import json
import tomllib

import numpy as np
import pandas as pd
import pytest

from feature_matrix.labels import LabelRules, annotate_readings
from spatial_model.baseline.evaluate import fixed_split_predict, loso_predict
from spatial_model.snapshot.build import DEFAULT_CONFIG, build_snapshot, verify_checksums
from spatial_model.snapshot.split import split_groups, station_groups, systematic_labels

# 14 lokasi di Jakarta; s00 dan s01 berjarak ±150 m (satu grup).
COORDS = [(106.8000, -6.2000), (106.8012, -6.2008), (106.7500, -6.1500), (106.8500, -6.1500), (106.9000, -6.2000),
          (106.7200, -6.2500), (106.8200, -6.2600), (106.8800, -6.3000), (106.7700, -6.3200), (106.9300, -6.1200),
          (106.8300, -6.1200), (106.7900, -6.1700), (106.8600, -6.2300), (106.7400, -6.2000)]
KOTA = ["KOTA ADM. JAKARTA PUSAT", "KOTA ADM. JAKARTA SELATAN", "KOTA ADM. JAKARTA TIMUR"]


def _export(root, start="2026-09-19T00:00Z", end="2026-09-24T00:00Z"):
    stations = pd.DataFrame({
        "uuid": [f"u{i:02d}" for i in range(len(COORDS))], "kode": [f"S{i:02d}" for i in range(len(COORDS))],
        "initial": [f"I{i:02d}" for i in range(len(COORDS))], "name": [f"Stasiun {i}" for i in range(len(COORDS))],
        "type": ["Reference" if i % 5 == 0 else "Sensor" for i in range(len(COORDS))],
        "kota": [KOTA[i % 3] for i in range(len(COORDS))],
        "lat": [c[1] for c in COORDS], "lng": [c[0] for c in COORDS], "in_jakarta_bbox": True})
    rng = np.random.default_rng(0)
    rows = []
    for i, uuid in enumerate(stations.uuid):
        for ts in pd.date_range(start, end, freq="30min", inclusive="left"):
            rows.append((uuid, "PM25", ts, round(20 + i + 10 * np.sin(ts.hour / 24 * 2 * np.pi)
                                                 + rng.normal(0, 2), 2), ""))
            if i < 4:
                rows.append((uuid, "NO2", ts, round(30 + 3 * i + rng.normal(0, 3), 2), ""))
    obs = pd.DataFrame(rows, columns=["station_uuid", "metric", "ts_utc", "value", "qc"])
    obs["ts_utc"] = obs.ts_utc.astype("datetime64[us, UTC]")
    root.mkdir(parents=True, exist_ok=True)
    stations.to_parquet(root / "station.parquet")
    obs.to_parquet(root / "observation.parquet")
    (root / "manifest.json").write_text(json.dumps({"snapshot_utc": "2026-10-06T06:22:15+00:00"}), encoding="utf-8")
    return root


def _config(tmp_path):
    text = DEFAULT_CONFIG.read_text(encoding="utf-8")
    cfg = tomllib.loads(text)
    replace = {
        'first_window = "2026-09-13T00:00:00Z"': 'first_window = "2026-09-19T00:00:00Z"',
        'end_window = "2026-09-30T23:00:00Z"': 'end_window = "2026-09-23T23:00:00Z"',
        'readings_until = "2026-10-01T00:00:00Z"': 'readings_until = "2026-09-24T00:00:00Z"',
        'lag_windows = 24': 'lag_windows = 6',
        'train = ["2026-09-13T00:00:00Z", "2026-09-24T00:00:00Z"]': 'train = ["2026-09-19T00:00:00Z", "2026-09-21T00:00:00Z"]',
        'val = ["2026-09-25T00:00:00Z", "2026-09-28T00:00:00Z"]': 'val = ["2026-09-22T00:00:00Z", "2026-09-22T12:00:00Z"]',
        'test = ["2026-09-29T00:00:00Z", "2026-09-30T23:00:00Z"]': 'test = ["2026-09-23T12:00:00Z", "2026-09-23T23:00:00Z"]',
        'fractions = { train = 0.70, val = 0.15, test = 0.15 }': 'fractions = { train = 0.60, val = 0.2, test = 0.2 }',
    }
    for old, new in replace.items():
        assert old in text, old
        text = text.replace(old, new)
    assert cfg["version"] == "ds-v0.1.0"
    path = tmp_path / "test.toml"
    path.write_text(text, encoding="utf-8")
    return path


@pytest.fixture(scope="module")
def snapshot(tmp_path_factory):
    tmp = tmp_path_factory.mktemp("snap")
    gt = _export(tmp / "gt")
    config = _config(tmp)
    first = build_snapshot(gt, tmp / "a", config)
    second = build_snapshot(gt, tmp / "b", config)
    return dict(dir=tmp / "a", other=tmp / "b", manifest=first, manifest_b=second, gt=gt, config=config)


# Determinisme dan verifikasi ----------------------------------------------------------
def test_dua_pembangunan_menghasilkan_checksum_identik(snapshot):
    a = (snapshot["dir"] / "CHECKSUMS.sha256").read_text(encoding="utf-8")
    b = (snapshot["other"] / "CHECKSUMS.sha256").read_text(encoding="utf-8")
    assert a == b and len(a.splitlines()) >= 20
    assert verify_checksums(snapshot["dir"]) == []


def test_verify_mendeteksi_berkas_berubah(snapshot, tmp_path):
    import shutil

    copy = tmp_path / "copy"
    shutil.copytree(snapshot["dir"], copy)
    (copy / "split_stats.md").write_text("diubah", encoding="utf-8")
    assert verify_checksums(copy) == ["split_stats.md"]


def test_tidak_menimpa_snapshot_yang_ada(snapshot):
    with pytest.raises(FileExistsError):
        build_snapshot(snapshot["gt"], snapshot["dir"], snapshot["config"])


# Kebocoran, grup, split ---------------------------------------------------------------
def test_laporan_kebocoran_lolos(snapshot):
    report = json.loads((snapshot["dir"] / "leakage_report.json").read_text(encoding="utf-8"))
    assert report["passed"], [c for c in report["checks"] if c["status"] != "lolos"]
    assert len(report["checks"]) == 8


def test_stasiun_berimpit_satu_grup_dan_satu_split(snapshot):
    st = pd.read_parquet(snapshot["dir"] / "stations.parquet").set_index("kode")
    assert st.loc["S00", "group_id"] == st.loc["S01", "group_id"]
    assert st.loc["S00", "station_split"] == st.loc["S01", "station_split"]
    assert st.group_id.nunique() == len(st) - 1


def test_split_blind_ganda_pm25(snapshot):
    labels = pd.read_parquet(snapshot["dir"] / "labels.parquet")
    for s in ("train", "val", "test"):
        f = pd.read_parquet(snapshot["dir"] / "splits" / f"{s}.parquet")
        pm = f[f.target == "pm25"]
        assert len(pm) and set(labels.set_index("station_uuid").loc[pm.station_uuid, "station_split"]) == {s}
        assert set(labels.set_index(["station_uuid", "time_window_start"]).loc[
            list(zip(pm.station_uuid, pm.time_window_start)), "period"]) == {s}


def test_fold_loso_menahan_grup_utuh_dan_jarak_sensor_tanpa_grup(snapshot):
    folds = pd.read_parquet(snapshot["dir"] / "splits" / "loso_folds.parquet")
    st = pd.read_parquet(snapshot["dir"] / "stations.parquet")
    pm = folds[folds.target == "pm25"]
    assert pm.fold.nunique() == st.group_id.nunique()
    assert set(pm.loc[pm.group_id == st.set_index("kode").loc["S00", "group_id"], "kode"]) == {"S00", "S01"}
    dist = pd.read_parquet(snapshot["dir"] / "splits" / "loso_sensor_distance.parquet")
    g = st.set_index("kode").loc["S00"]
    held = dist[(dist.held_out_group == g.group_id) & (dist.node_index == g.node_index)].dist_nearest_sensor_m.iloc[0]
    assert held > 1_000                                                     # S00/S01 tidak dipakai


def test_split_stasiun_deterministik_dan_berstrata():
    labels = systematic_labels(20, {"train": 0.7, "val": 0.15, "test": 0.15})
    assert labels.count("test") == 3 and labels.count("val") == 3
    st = pd.DataFrame({"uuid": [f"u{i}" for i in range(30)], "kode": [f"K{i:02d}" for i in range(30)],
                       "group_id": [f"G_K{i:02d}" for i in range(30)], "station_type": ["Sensor"] * 30,
                       "kota": [KOTA[i % 3] for i in range(30)]})
    a = split_groups(st, {"train": 0.7, "val": 0.15, "test": 0.15}, ["station_type", "kota"], 42)
    b = split_groups(st, {"train": 0.7, "val": 0.15, "test": 0.15}, ["station_type", "kota"], 42)
    c = split_groups(st, {"train": 0.7, "val": 0.15, "test": 0.15}, ["station_type", "kota"], 7)
    per_group = lambda g: g.set_index("group_id").station_split.sort_index()  # noqa: E731
    assert a.equals(b) and not per_group(a).equals(per_group(c))
    assert (a.groupby("kota").station_split.apply(lambda s: (s == "train").sum()) > 0).all()


def test_duplikat_penyedia_digabung():
    st = pd.DataFrame({"uuid": ["a", "b"], "kode": ["A", "B"], "lat": [-6.2, -6.3], "lng": [106.8, 106.9],
                       "grid_id": ["r1_c1", "r2_c2"]})
    ts = pd.date_range("2026-09-19", periods=60, freq="h", tz="UTC")
    readings = pd.DataFrame({"station_uuid": ["a"] * 60 + ["b"] * 60, "metric": "PM25",
                             "ts_utc": list(ts) * 2, "value": list(range(60)) * 2})
    out = station_groups(st, readings, 300.0, 0.2)
    assert out.group_id.nunique() == 1 and out.group_reason.iloc[0] == "duplicate_provider"


# Aturan label -------------------------------------------------------------------------
def test_deret_macet_dan_jam_tidak_lengkap(tmp_path):
    root = tmp_path / "gt"
    root.mkdir()
    pd.DataFrame({"uuid": ["a"], "kode": ["A"], "lat": [-6.2], "lng": [106.8], "in_jakarta_bbox": [True]}) \
        .to_parquet(root / "station.parquet")
    ts = list(pd.date_range("2026-09-19", periods=40, freq="30min", tz="UTC"))
    values = [10.0 + i for i in range(40)]
    values[10:24] = [55.0] * 14                       # 14 nilai identik: macet (≥ 12)
    qc = [""] * 40
    qc[30] = "N"                                      # jam 15:00 tinggal satu bacaan valid
    pd.DataFrame({"station_uuid": "a", "metric": "PM25", "ts_utc": ts, "value": values, "qc": qc}) \
        .to_parquet(root / "observation.parquet")
    obs, _ = annotate_readings(root, LabelRules(stuck_run_min=12, require_complete_hour=True))
    assert (obs.exclusion_reason.iloc[10:24] == "stuck_run").all()
    assert obs.exclusion_reason.iloc[31] == "incomplete_hour" and obs.exclusion_reason.iloc[30] == "qc_flag"
    assert obs.exclusion_reason.iloc[:10].isna().all()
    plain, _ = annotate_readings(root)                # aturan bundle: tanpa deret dan jam lengkap
    assert plain.exclusion_reason.iloc[10:24].isna().all()


# Baseline IDW pada snapshot -----------------------------------------------------------
def _toy():
    return pd.DataFrame({
        "station_uuid": ["a", "b", "c", "d"], "pollutant": "pm25",
        "window_start_utc": pd.Timestamp("2026-09-29T00:00Z"), "value_ugm3": [10.0, 100.0, 20.0, 30.0],
        "x": [0.0, 10.0, 1000.0, 2000.0], "y": 0.0, "type": "Sensor", "group_id": ["g1", "g1", "g2", "g3"],
        "station_split": ["test", "test", "train", "val"], "split": "test", "kode": list("abcd"),
        "name": list("abcd"), "kota": "x", "lat": 0.0, "lon": 0.0, "suspected_low_bias": False})


def test_loso_menahan_seluruh_grup():
    pred = loso_predict(_toy(), power=2.0, neighbors=0).set_index("station_uuid")
    # Tanpa grup, b (10 m dari a) akan mendominasi estimasi a.
    assert pred.loc["a", "predicted"] < 30 and pred.loc["a", "n_sources_available"] == 2


def test_split_tetap_hanya_dari_stasiun_train():
    pred = fixed_split_predict(_toy(), power=2.0, neighbors=0)
    assert set(pred.station_uuid) == {"a", "b"} and (pred.predicted == 20.0).all()
