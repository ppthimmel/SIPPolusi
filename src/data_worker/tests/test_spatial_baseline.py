"""Uji baseline IDW Spatial Downscaling Model (TI-AI-04).

UT-SDM-08 (run_idw_fallback) dan UT-MTP-03 (evaluate_model, skema
leave-one-station-out) dari Tabel 5.7 dan Tabel 5.9 Dokumen Desain, serta
reprodusibilitas dataset beku dan run dari manifest yang sama.
"""

import json
import pathlib

import numpy as np
import pandas as pd
import pytest

from spatial_model.baseline.dataset import (
    DATASET_FILE,
    MANIFEST_FILE,
    build_dataset,
    compute_split_boundaries,
    load_dataset,
)
from spatial_model.baseline.evaluate import compute_metrics, loso_predict
from spatial_model.baseline.run import run_baseline
from spatial_model.grid import GridSpec, to_grid_xy, to_lonlat
from spatial_model.idw import NoGroundTruthError, idw_interpolate, run_idw_fallback

CONFIG = pathlib.Path(__file__).resolve().parent.parent / "spatial_model" / "baseline" / "configs" / "idw_baseline.toml"

# Empat stasiun sintetis di Jakarta dengan nilai yang diketahui.
STATIONS = pd.DataFrame({
    "station_id": ["A", "B", "C", "D"],
    "lon": [106.80, 106.85, 106.80, 106.85],
    "lat": [-6.20, -6.20, -6.15, -6.15],
    "pm25": [10.0, 20.0, 30.0, 40.0],
    "no2": [25.0, 50.0, 75.0, 100.0],
})


def _measurements(stations=STATIONS, pollutants=("pm25", "no2")):
    rows = []
    for _, s in stations.iterrows():
        for p in pollutants:
            rows.append({"station_id": s.station_id, "lon": s.lon, "lat": s.lat, "pollutant": p,
                         "value": s[p], "qc": ""})
    return pd.DataFrame(rows)


def _manual_idw(px, py, sx, sy, values, power):
    d = np.hypot(sx - px, sy - py)
    w = 1.0 / d ** power
    return float((w * values).sum() / w.sum())


@pytest.fixture
def small_grid():
    x, y = to_grid_xy(STATIONS["lon"], STATIONS["lat"])
    return GridSpec(x0=float(x.min()) - 300.0, y0=float(y.min()) - 300.0, nx=8, ny=8, cell_m=1000.0)


# UT-SDM-08 -----------------------------------------------------------------
def test_ut_sdm_08a_sama_dengan_idw_manual_dan_bertanda_idw(small_grid):
    out = run_idw_fallback(_measurements(), small_grid, power=2, neighbors=8)
    sx, sy = to_grid_xy(STATIONS["lon"], STATIONS["lat"])
    assert len(out) == small_grid.n_cells
    assert (out["estimation_source"] == "idw").all()
    for _, cell in out.sample(10, random_state=42).iterrows():
        pm25 = _manual_idw(cell.x, cell.y, sx, sy, STATIONS["pm25"].to_numpy(), 2)
        no2 = _manual_idw(cell.x, cell.y, sx, sy, STATIONS["no2"].to_numpy(), 2)
        assert cell.pm25_ugm3 == pytest.approx(pm25)
        assert cell.no2_ugm3 == pytest.approx(no2)
        assert cell.exposure_index == pytest.approx((pm25 / 15 + no2 / 25) / 2)


def test_ut_sdm_08a_indeks_dari_polutan_yang_tersedia(small_grid):
    out = run_idw_fallback(_measurements(pollutants=("pm25",)), small_grid)
    assert out["no2_ugm3"].isna().all()
    np.testing.assert_allclose(out["exposure_index"], out["pm25_ugm3"] / 15)


def test_ut_sdm_08b_sel_berimpit_dengan_stasiun():
    x, y = to_grid_xy(STATIONS["lon"], STATIONS["lat"])
    grid = GridSpec(x0=float(x[0]) - 50.0, y0=float(y[0]) - 50.0, nx=3, ny=3, cell_m=100.0)
    # Pindahkan stasiun A tepat ke pusat sel pertama.
    lon, lat = to_lonlat(grid.x0 + 50.0, grid.y0 + 50.0)
    stations = STATIONS.copy()
    stations.loc[0, ["lon", "lat"]] = [float(lon), float(lat)]
    out = run_idw_fallback(_measurements(stations), grid)
    assert out.loc[0, "pm25_ugm3"] == pytest.approx(10.0)
    assert out.loc[0, "no2_ugm3"] == pytest.approx(25.0)


def test_ut_sdm_08c_power_besar_mendekati_stasiun_terdekat():
    sx, sy = to_grid_xy(STATIONS["lon"], STATIONS["lat"])
    src = np.column_stack([sx, sy])
    # Titik 500 m dari A; A bernilai 10, stasiun lain lebih tinggi.
    point = np.array([[sx[0] + 500.0, sy[0]]])
    p1 = idw_interpolate(src, STATIONS["pm25"], point, power=1)["value"][0]
    p3 = idw_interpolate(src, STATIONS["pm25"], point, power=3)["value"][0]
    assert abs(p3 - 10.0) < abs(p1 - 10.0)


def test_ut_sdm_08d_tanpa_pengukuran_menghasilkan_galat(small_grid):
    with pytest.raises(NoGroundTruthError):
        run_idw_fallback(_measurements().iloc[0:0], small_grid)
    flagged = _measurements().assign(qc="S")
    with pytest.raises(NoGroundTruthError):
        run_idw_fallback(flagged, small_grid)


def test_idw_neighbors_membatasi_stasiun_yang_dipakai():
    sx, sy = to_grid_xy(STATIONS["lon"], STATIONS["lat"])
    src = np.column_stack([sx, sy])
    res = idw_interpolate(src, STATIONS["pm25"], src[:1] + 10.0, power=2, neighbors=2)
    assert res["n_used"][0] == 2
    assert 0 in set(res["source_index"][0])


# UT-MTP-03 -----------------------------------------------------------------
def test_ut_mtp_03a_metrik_sama_dengan_perhitungan_manual():
    y = np.array([10.0, 20.0, 30.0, 40.0])
    yhat = np.array([12.0, 18.0, 33.0, 40.0])
    m = compute_metrics(y, yhat)
    assert m["n"] == 4
    assert m["mae"] == pytest.approx((2 + 2 + 3 + 0) / 4)
    assert m["rmse"] == pytest.approx(np.sqrt((4 + 4 + 9 + 0) / 4))
    assert m["bias"] == pytest.approx((2 - 2 + 3 + 0) / 4)
    assert m["r2"] == pytest.approx(1 - 17 / 500)


def _station_hour(stations=STATIONS, windows=1):
    x, y = to_grid_xy(stations["lon"], stations["lat"])
    rows = []
    for w in range(windows):
        ts = pd.Timestamp("2026-10-02T00:00Z") + pd.Timedelta(hours=w)
        for i, s in stations.iterrows():
            rows.append({"station_uuid": s.station_id, "kode": s.station_id, "name": s.station_id,
                         "type": "Reference", "kota": "X", "lat": s.lat, "lon": s.lon, "x": x[i], "y": y[i],
                         "suspected_low_bias": False, "pollutant": "pm25", "window_start_utc": ts,
                         "value_ugm3": s.pm25, "split": "test"})
    return pd.DataFrame(rows)


def test_ut_mtp_03b_empat_fold_dan_stasiun_uji_tidak_dipakai():
    df = _station_hour()
    pred = loso_predict(df, power=2, neighbors=8)
    assert sorted(pred["station_uuid"]) == ["A", "B", "C", "D"]
    sx, sy = df["x"].to_numpy(), df["y"].to_numpy()
    for i, row in pred.reset_index(drop=True).iterrows():
        idx = df.index[df["station_uuid"] == row.station_uuid][0]
        others = df.index != idx
        expected = _manual_idw(sx[idx], sy[idx], sx[others], sy[others], df["value_ugm3"].to_numpy()[others], 2)
        assert row.predicted == pytest.approx(expected)
        assert row.n_sources_available == 3
    # Mengubah nilai stasiun uji tidak mengubah estimasi untuk dirinya sendiri.
    changed = df.assign(value_ugm3=np.where(df["station_uuid"] == "A", 1e6, df["value_ugm3"]))
    before = pred.set_index("station_uuid")["predicted"]
    after = loso_predict(changed, power=2, neighbors=8).set_index("station_uuid")["predicted"]
    assert after["A"] == pytest.approx(before["A"])
    assert after["B"] > before["B"]


# Dataset beku dan reprodusibilitas -------------------------------------------
def _raw(days=6):
    """Stasiun dan pengukuran mentah sintetis berbentuk tabel skema ground_truth."""
    rng = np.random.default_rng(42)
    kode = ["DKI1", "DKI2", "DKI_PM25_1", "DKI_PM25_2", "DKI_PM25_40", "LCS-1"]
    stations = pd.DataFrame({
        "uuid": [f"u{i}" for i in range(len(kode))],
        "kode": kode, "initial": kode, "name": [f"Stasiun {k}" for k in kode],
        "type": ["Reference", "Reference", "Sensor", "Sensor", "Sensor", "Sensor"],
        "lat": [-6.18, -6.15, -6.22, -6.25, -6.20, -6.30],
        "lng": [106.82, 106.90, 106.78, 106.85, 106.80, 106.75],
        "kota": ["Pusat", "Utara", "Barat", "Selatan", "Selatan", "Selatan"],
        "in_jakarta_bbox": [True] * 5 + [False],
    })
    ts = pd.date_range("2026-09-13T00:00Z", periods=days * 48, freq="30min")
    rows = []
    for i, s in stations.iterrows():
        base = 3.0 if s.kode == "DKI_PM25_2" else 30.0 + 5 * i
        for t in ts:
            rows.append((s.uuid, "PM25", t, base + rng.uniform(-1, 1), ""))
            if s.type == "Reference":
                rows.append((s.uuid, "NO2", t, 40.0 + rng.normal(0, 5), ""))
    obs = pd.DataFrame(rows, columns=["station_uuid", "metric", "ts_utc", "value", "qc"])
    obs.loc[5, "value"] = 0.0          # nol sebagai penanda kosong
    obs.loc[7, "value"] = 999.99       # sentinel
    obs.loc[9, "qc"] = "S"
    return stations, obs


def _config():
    import tomllib

    return tomllib.loads(CONFIG.read_text(encoding="utf-8"))


def test_split_temporal_hari_penuh_dan_berurutan():
    windows = pd.Series(pd.date_range("2026-09-13T05:00Z", "2026-10-05T23:00Z", freq="h"))
    b = compute_split_boundaries(windows, {"train_fraction": 0.7, "val_fraction": 0.15, "drop_partial_days": True})
    assert b["start"].startswith("2026-09-14")       # hari pertama tidak lengkap dibuang
    assert b["end"].startswith("2026-10-06")
    assert b["start"] < b["train_end"] < b["val_end"] < b["end"]


def test_dataset_kendali_mutu_split_dan_manifest(tmp_path):
    stations, obs = _raw()
    manifest = build_dataset(_config(), stations, obs, {"type": "synthetic"}, tmp_path)
    df, loaded = load_dataset(tmp_path / MANIFEST_FILE)
    assert loaded["dataset_version"] == manifest["dataset_version"]
    dropped = manifest["qc_dropped_rows"]
    assert dropped["nonpositive"] == 1 and dropped["sentinel"] == 1 and dropped["qc_flagged"] == 1
    assert dropped["outside_study_area"] > 0
    assert "LCS-1" not in set(df["kode"])                 # di luar bbox
    assert "DKI_PM25_40" not in set(df.loc[df["pollutant"] == "pm25", "kode"])
    assert set(df["split"]) == {"train", "val", "test"}
    # Tidak ada time window yang muncul di dua split.
    assert df.groupby("window_start_utc")["split"].nunique().max() == 1
    # Bias rendah ditandai, tidak dibuang.
    assert df.loc[df["kode"] == "DKI_PM25_2", "suspected_low_bias"].all()
    assert not df.loc[df["kode"] == "DKI_PM25_1", "suspected_low_bias"].any()


def test_dataset_deterministik_dan_perubahan_isi_terdeteksi(tmp_path):
    stations, obs = _raw()
    m1 = build_dataset(_config(), stations, obs, {"type": "synthetic"}, tmp_path / "a")
    m2 = build_dataset(_config(), stations, obs, {"type": "synthetic"}, tmp_path / "b")
    assert m1["dataset_version"] == m2["dataset_version"]
    assert m1["split"]["boundaries"] == m2["split"]["boundaries"]

    path = tmp_path / "a" / DATASET_FILE
    df = pd.read_parquet(path)
    df.loc[0, "value_ugm3"] += 1.0
    df.to_parquet(path, index=False)
    with pytest.raises(ValueError, match="tidak cocok"):
        load_dataset(tmp_path / "a" / MANIFEST_FILE)


def test_run_dapat_diulang_dari_manifest_yang_sama(tmp_path):
    stations, obs = _raw()
    build_dataset(_config(), stations, obs, {"type": "synthetic"}, tmp_path / "dataset")
    manifest = tmp_path / "dataset" / MANIFEST_FILE
    run1 = run_baseline(CONFIG, manifest, tmp_path / "runs1")
    run2 = run_baseline(CONFIG, manifest, tmp_path / "runs2")
    m1 = json.loads((run1 / "run_manifest.json").read_text())
    m2 = json.loads((run2 / "run_manifest.json").read_text())
    assert m1["outputs"]["predictions_sha256"] == m2["outputs"]["predictions_sha256"]
    assert m1["seed"] == 42 and m1["model"]["power"] == 2.0 and m1["model"]["neighbors"] == 8
    assert m1["config"]["sha256"] == m2["config"]["sha256"]
    for name in ("config.toml", "dataset_manifest.json", "metrics.json", "report.md", "tables/per_station.csv"):
        assert (run1 / name).exists()
    metrics = json.loads((run1 / "metrics.json").read_text())
    assert metrics["split"] == "test" and metrics["unit"] == "µg/m³"
    assert {"pm25", "no2"} <= set(metrics["overall"])
