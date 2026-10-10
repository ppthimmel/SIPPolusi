"""Uji FeatureMatrix ST-GNN (TI-AI-02).

Fitur dibaca dari ``dataset_processed`` di repo. Label memakai ekspor ground
truth sintetis kecil (ekspor asli tidak di-commit). Aturan *as-of* diperiksa
ulang secara brute force dari data mentah, bukan dari keluaran modul sendiri.
"""

from __future__ import annotations

import json

import numpy as np
import pandas as pd
import pytest

from feature_matrix.grid import active_cells, nearest_sensor_distance, to_grid_xy
from feature_matrix.labels import station_cells, station_hour_targets
from feature_matrix.prepare_stgnn import (AVAILABILITY_FLAGS, SENSOR_FEATURES, build_grid_graph,
                                          check_availability_flags, export_stgnn_inputs, prepare_stgnn_labels,
                                          read_stgnn_batch)
from feature_matrix.quality import quality_report
from feature_matrix.sources import SourceReader, s5p_cell_ids, viirs_tile_cell_ids
from feature_matrix.spec import spec_document

# Tiga stasiun nyata (lon, lat) di dalam bbox studi; satu di luar bbox.
STATIONS = pd.DataFrame({
    "uuid": ["a", "b", "c", "x"], "kode": ["DKI_PM25_64", "DKJ35", "DKI_PM25_33", "LCS-14"],
    "initial": ["DKI1", "DKI2", "DKI66", "LCS10"], "name": ["A", "B", "C", "X"],
    "type": ["Sensor", "Reference", "Sensor", "Sensor"],
    "lat": [-6.19306, -6.161974, -6.268989, -5.743], "lng": [106.794973, 106.716769, 106.837096, 106.61],
    "in_jakarta_bbox": [True, True, True, False]})


def _ts(s):
    return pd.Timestamp(s, tz="UTC")


@pytest.fixture(scope="module")
def ground_truth(tmp_path_factory):
    root = tmp_path_factory.mktemp("gt")
    rows = []
    for station in "abcx":
        for ts in pd.date_range("2026-09-19T00:00Z", "2026-09-20T23:30Z", freq="30min"):
            rows.append((station, "PM25", ts, 20.0 + ts.hour, ""))
    rows += [
        ("a", "NO2", _ts("2026-09-19T05:00"), 10.0, ""), ("a", "NO2", _ts("2026-09-19T05:30"), 30.0, ""),
        ("a", "PM25", _ts("2026-09-19T05:10"), 0.0, ""),            # nol = tidak ada data
        ("a", "PM25", _ts("2026-09-19T05:20"), 999.99, ""),         # sentinel
        ("a", "PM25", _ts("2026-09-19T05:40"), 500.0, "S"),         # flag QC
    ]
    obs = pd.DataFrame(rows, columns=["station_uuid", "metric", "ts_utc", "value", "qc"])
    obs["ts_utc"] = obs.ts_utc.astype("datetime64[us, UTC]")
    STATIONS.to_parquet(root / "station.parquet")
    obs.to_parquet(root / "observation.parquet")
    (root / "manifest.json").write_text("{}", encoding="utf-8")
    return root


@pytest.fixture(scope="module")
def targets(ground_truth):
    return station_hour_targets(ground_truth, "2026-09-19T01:00Z", "2026-09-21T00:00Z")


@pytest.fixture(scope="module")
def exported(targets, tmp_path_factory):
    out = tmp_path_factory.mktemp("stgnn")
    nodes, edges = build_grid_graph(active_cells(), targets.grid_id.unique().tolist(), 1)
    labels = prepare_stgnn_labels(targets, nodes, "2026-09-20T00:00Z", "2026-09-20T12:00Z")
    export_stgnn_inputs(nodes, edges, labels, SourceReader(), out, window=6)
    return out


# Data dictionary dan grid ------------------------------------------------------------
def test_feature_spec_json_sinkron_dengan_spec_py():
    from feature_matrix.__main__ import SPEC_PATH

    assert json.loads(SPEC_PATH.read_text(encoding="utf-8")) == json.loads(json.dumps(spec_document()))


def test_grid_sama_dengan_lookup_geoscf_ti_ai_01():
    cells = active_cells()
    assert len(cells) == 195_782
    root = SourceReader().root
    lookup = pd.read_parquet(root / "geos-cf/grid100m_geoscf_lookup.parquet")
    lookup["grid_id"] = [f"r{444 - y:04d}_c{x:04d}" for x, y in zip(lookup.gx, lookup.gy)]
    merged = cells.merge(lookup, on="grid_id")
    assert len(merged) == len(cells)
    assert np.allclose(merged.easting, merged.x_utm) and np.allclose(merged.northing, merged.y_utm)


def test_graf_delapan_tetangga_dua_arah_tanpa_self_loop():
    nodes, edges = build_grid_graph(active_cells(), ["r0200_c0200"], 1)
    assert len(nodes) == 9 and len(edges) == 40
    assert not (edges.source_node == edges.target_node).any()
    pairs = set(zip(edges.source_node, edges.target_node))
    assert all((t, s) in pairs for s, t in pairs)
    assert set(np.round(edges.distance_m, 2)) == {100.0, 141.42}


# Label --------------------------------------------------------------------------------
def test_stasiun_dipetakan_ke_sel_yang_memuatnya(targets):
    located = station_cells(STATIONS.iloc[:3])
    x, y = to_grid_xy(located.lng, located.lat)
    cells = active_cells().set_index("grid_id").loc[located.grid_id]
    assert (np.abs(x - cells.easting.to_numpy()) <= 50).all() and (np.abs(y - cells.northing.to_numpy()) <= 50).all()
    assert (located.station_grid_distance_m < 71).all()
    assert "x" not in set(targets.station_uuid)                                  # di luar bbox


def test_label_rata_rata_jam_dicap_akhir_jam_dan_aturan_validitas(targets):
    a = targets[(targets.station_uuid == "a") & (targets.time_utc == _ts("2026-09-19T06:00"))].iloc[0]
    # [05:00, 06:00): PM2.5 05:00 dan 05:30 = 25,0; nol, 999,99 dan flag S dibuang.
    assert a.target_pm25 == 25.0 and a.target_pm25_n_readings == 2
    assert a.target_no2 == 20.0 and a.target_no2_n_readings == 2
    assert a.target_window_start_utc == _ts("2026-09-19T05:00")
    assert targets.time_utc.min() == _ts("2026-09-19T01:00") and targets.time_utc.max() == _ts("2026-09-20T23:00")


def test_pm25_dki_pm25_33_dibuang_sejak_macet(targets):
    c = targets[targets.station_uuid == "c"]
    assert c.time_utc.max() <= _ts("2026-09-19T10:00")                       # pembacaan 09:30 masih valid


# Reader dan kebocoran waktu -----------------------------------------------------------
def test_geoscf_dan_sentinel5p_as_of_brute_force(exported):
    f = pd.read_parquet(exported / "features.parquet")
    root = SourceReader().root
    geos = pd.read_parquet(root / "geos-cf/geoscf_cell_hourly.parquet")
    obs = pd.read_parquet(root / "sentinel5p/observations.parquet")
    obs = obs[obs.available]
    for r in f.sample(40, random_state=1).itertuples():
        g = geos[(geos.cell_id == r.geoscf_cell_id) & (geos.available_at_utc <= r.time_utc)]
        latest = g.time_window_start.max()
        assert r.geoscf_pm25_time_window_start == latest
        assert r.geoscf_pm25_ugm3 == pytest.approx(g.loc[g.time_window_start == latest, "pm25_ugm3"].iloc[0])
        assert r.geoscf_pm25_age_hours == pytest.approx((r.time_utc - latest).total_seconds() / 3600 - 1)
        # Sentinel-5P: pengamatan terbaru di sel 5 km dengan produced_at <= τ.
        cand = obs[(obs.grid_id == s5p_cell_ids([r.easting], [r.northing])[0]) & (obs.produced_at <= r.time_utc)]
        best = cand.sort_values(["observed_at", "produced_at"]).iloc[-1]
        assert r.no2_mol_m2_observed_at == best.observed_at and r.no2_mol_m2_scene_id == best.scene_id
        assert r.no2_mol_m2 == pytest.approx(best.no2_mol_m2, rel=1e-6)


def test_cuaca_as_of_waktu_tersedia_brute_force(exported):
    f = pd.read_parquet(exported / "features.parquet")
    w = pd.read_parquet(SourceReader().root / "open_meteo/hourly.parquet")
    # Brute force: valid time t tersedia pada floor(t, 6 jam) + 8 jam; ambil t terbaru yang tersedia <= τ.
    avail = w.time_utc.dt.floor("6h") + pd.Timedelta(hours=8)
    for tau in f.time_utc.drop_duplicates().sample(20, random_state=3):
        best = w.loc[avail <= tau, "time_utc"].max()
        rows = f[f.time_utc == tau]
        assert (rows.weather_time_utc == best).all() and (rows.weather_available_at_utc <= tau).all()
        expected = w.set_index("time_utc").loc[best, "temperature_2m"]
        assert np.allclose(rows.temperature_2m, expected, rtol=1e-6)
        assert np.allclose(rows.weather_age_hours, (tau - best) / pd.Timedelta(hours=1))
    assert f.weather_age_hours.between(3, 8).all()


def test_cuaca_aturan_bundle_memakai_valid_time_tau():
    nodes, _ = build_grid_graph(active_cells(), ["r0200_c0200"], 1)
    legacy = SourceReader(weather_delay_hours=None)(nodes.grid_id, "2026-09-20T00:00Z", "2026-09-20T03:00Z")
    assert (legacy.weather_time_utc == legacy.time_utc).all() and (legacy.weather_age_hours == 0).all()


def test_manifest_tanpa_hash_keluaran_ditolak(exported, tmp_path):
    import shutil

    copy = tmp_path / "copy"
    shutil.copytree(exported, copy)
    manifest = json.loads((copy / "manifest.json").read_text(encoding="utf-8"))
    for outputs in ({}, {k: v for k, v in manifest["outputs"].items() if k != "labels.parquet"}):
        (copy / "manifest.json").write_text(json.dumps(manifest | {"outputs": outputs}), encoding="utf-8")
        check = next(c for c in quality_report(copy)["checks"] if c["check"].startswith("manifest"))
        assert check["status"] == "gagal", outputs.keys()


def test_viirs_aturan_tile_memakai_sudut_barat_laut():
    reader = SourceReader()
    daily = pd.read_parquet(reader.root / "viirs/daily.parquet", columns=["viirs_cell_id", "longitude", "latitude"])
    cell = daily.drop_duplicates("viirs_cell_id").iloc[500]
    # Titik sedikit di dalam sel dari sudut barat laut yang tercatat → sel itu sendiri.
    assert viirs_tile_cell_ids([cell.longitude + 0.1 / 240], [cell.latitude - 0.1 / 240]) == [cell.viirs_cell_id]
    nodes, _ = build_grid_graph(active_cells(), ["r0200_c0200"], 1)
    tile = reader.cell_mapping(nodes.grid_id).viirs_cell_id
    legacy = SourceReader(viirs_cell_rule="nearest_listed").cell_mapping(nodes.grid_id).viirs_cell_id
    assert (tile != legacy).any()


# Penanda ketersediaan, jarak sensor, laporan ------------------------------------------
def test_ekspor_lolos_laporan_kualitas(exported):
    report = quality_report(exported)
    assert report["passed"], [c for c in report["checks"] if c["status"] != "lolos"]


def test_penanda_ketersediaan_konsisten_dan_ditolak_bila_tidak(exported):
    f = pd.read_parquet(exported / "features.parquet")
    assert set(AVAILABILITY_FLAGS) <= set(f.columns)
    assert (f.ndvi_available == f.ndvi.notna()).all() and (~f.lst_c_available).any()
    check_availability_flags(f)
    broken = f.head(5).copy()
    broken.loc[0, "ndvi_available"] = not broken.loc[0, "ndvi_available"]
    with pytest.raises(ValueError, match="ndvi_available"):
        check_availability_flags(broken)
    with pytest.raises(ValueError, match="Unexpected Boolean"):
        check_availability_flags(f.head(5).assign(sneaky=True))


def test_jarak_sensor_dan_hitung_ulang_per_fold(exported, targets):
    f = pd.read_parquet(exported / "features.parquet")
    labelled = f.grid_id.isin(set(targets.grid_id))
    assert (f.loc[labelled, "dist_nearest_sensor_m"] < 71).all()
    assert (f.loc[~labelled, "dist_nearest_sensor_m"] >= 50).all()
    batch = read_stgnn_batch(exported, "2026-09-20T12:00Z")
    assert batch["sensor_static"].shape == (len(batch["grid_ids"]), len(SENSOR_FEATURES))
    others = targets[targets.station_uuid != "a"]
    held_out = read_stgnn_batch(exported, "2026-09-20T12:00Z", sensors=others)["sensor_static"][:, 0]
    a_cell = targets.loc[targets.station_uuid == "a", "grid_id"].iloc[0]
    i = list(batch["grid_ids"]).index(a_cell)
    assert held_out[i] > 1_000 > batch["sensor_static"][i, 0]


def test_jarak_sensor_kosong_bila_tanpa_stasiun():
    assert np.isnan(nearest_sensor_distance([700000.0], [9300000.0], [], [])).all()
