"""Uji FeatureMatrix (TI-AI-02) terhadap keluaran dataset_processed di repo.

Aturan *as-of* diverifikasi ulang secara brute force dari data mentah, bukan
hanya dari audit yang dihasilkan modul itu sendiri.
"""

import hashlib
import json

import numpy as np
import pandas as pd
import pytest

from contracts import JAKARTA_BBOX
from feature_matrix.build import build_feature_matrix
from feature_matrix.quality import quality_report
from feature_matrix.sources import MAX_AGE_H, Sources
from feature_matrix.spec import CANONICAL_GRID, FEATURE_NAMES, FEATURES_BY_NAME, spec_document
from spatial_model.grid import GridSpec

WINDOWS = pd.date_range("2026-09-21T00:00Z", periods=24, freq="h")


@pytest.fixture(scope="module")
def src():
    return Sources()


@pytest.fixture(scope="module")
def stations(src):
    st = src.stations()
    return st[st["grid_id"] >= 0].reset_index(drop=True)


@pytest.fixture(scope="module")
def built(src, stations):
    return build_feature_matrix(WINDOWS, stations["grid_id"], sources=src, sensors=stations)


def _hash(df):
    return hashlib.sha256(pd.util.hash_pandas_object(df, index=False).to_numpy().tobytes()).hexdigest()


# Spesifikasi dan grid ---------------------------------------------------------
def test_feature_spec_json_sinkron_dengan_spec_py():
    from feature_matrix.__main__ import SPEC_PATH

    assert json.loads(SPEC_PATH.read_text(encoding="utf-8")) == json.loads(json.dumps(spec_document()))


def test_grid_kanonik_sama_dengan_grid_sumber_ti_ai_01(src):
    lookup = pd.read_parquet(src.root / "geos-cf/grid100m_geoscf_lookup.parquet")
    assert (CANONICAL_GRID.cell_index(lookup["x_utm"], lookup["y_utm"]) == lookup["grid_id"]).all()


def test_grid_ti_ai_05_termuat_dan_sejajar_kisi():
    b = GridSpec.from_bbox(JAKARTA_BBOX.as_tuple(), 100.0)
    a = CANONICAL_GRID
    assert (b.x0 - a.x0) % 100 == 0 and (b.y0 - a.y0) % 100 == 0
    assert a.x0 <= b.x0 and a.y0 <= b.y0
    assert b.x0 + b.nx * 100 <= a.x0 + a.nx * 100 and b.y0 + b.ny * 100 <= a.y0 + a.ny * 100


# Skema, ketertelusuran, missingness --------------------------------------------
def test_kolom_dan_tipe_mengikuti_data_dictionary(built):
    fm, _ = built
    assert list(fm.columns) == FEATURE_NAMES
    for name in FEATURE_NAMES:
        expected = FEATURES_BY_NAME[name].dtype
        assert str(fm[name].dtype).startswith(expected.split("[")[0]), name


def test_setiap_baris_tertelusur_ke_sel_dan_time_window(built):
    fm, _ = built
    assert (CANONICAL_GRID.cell_index(fm["x_utm"], fm["y_utm"]) == fm["grid_id"]).all()
    assert (fm["time_window_start"] == fm["time_window_start"].dt.floor("h")).all()
    assert (fm["inference_time"] - fm["time_window_start"] == pd.Timedelta(hours=1)).all()
    assert not fm.duplicated(["grid_id", "time_window_start"]).any()


def test_laporan_kualitas_lolos_dan_missingness_eksplisit(built):
    report = quality_report(*built)
    failed = [c for c in report["checks"] if c["status"] == "gagal"]
    assert not failed, failed
    assert set(report["missing_fraction"]) >= {"s5p_no2_umol_m2", "land_lst_c"}


# Kebocoran waktu (brute force dari data mentah) ----------------------------------
def test_geoscf_as_of_brute_force(src, built):
    fm, audit = built
    raw = pd.read_parquet(src.root / "geos-cf/geoscf_cell_hourly.parquet")
    raw["time_window_start"] = pd.to_datetime(raw["time_window_start"], utc=True)
    raw["available_at_utc"] = pd.to_datetime(raw["available_at_utc"], utc=True)
    for row in audit.itertuples():
        usable = raw[raw["available_at_utc"] <= row.inference_time]
        assert row.geoscf_valid_time == usable["time_window_start"].max()
        assert row.geoscf_available_at <= row.inference_time
    # nilai di sel stasiun sama dengan nilai sel GEOS-CF valid time tersebut
    lookup = pd.read_parquet(src.root / "geos-cf/grid100m_geoscf_lookup.parquet")
    r = fm.iloc[0]
    v = audit.loc[audit["time_window_start"] == r.time_window_start, "geoscf_valid_time"].iloc[0]
    cell = lookup.loc[lookup["grid_id"] == r.grid_id, "geoscf_cell_id"].iloc[0]
    expected = raw[(raw["time_window_start"] == v) & (raw["cell_id"] == cell)]["pm25_ugm3"].iloc[0]
    assert r.geoscf_pm25_ugm3 == pytest.approx(expected)
    assert r.geoscf_age_h == pytest.approx((r.time_window_start - v) / pd.Timedelta(hours=1))


def test_sentinel5p_as_of_brute_force(src, built):
    fm, _ = built
    obs = pd.read_parquet(src.root / "sentinel5p/observations.parquet")
    obs = obs[obs["available"]]
    for col in ("observed_at", "produced_at"):
        obs[col] = pd.to_datetime(obs[col], utc=True)
    s5p_cell = src.s5p()["cell_of_grid"]
    g = json.loads((src.root / "sentinel5p/manifest.json").read_text(encoding="utf-8"))["grid"]
    rows = fm.sample(60, random_state=42)
    for r in rows.itertuples():
        c = s5p_cell[r.grid_id]
        gid = f"r{c // g['width']:04d}_c{c % g['width']:04d}"
        cand = obs[(obs["grid_id"] == gid) & (obs["produced_at"] <= r.inference_time)]
        if len(cand):
            latest = cand.sort_values(["observed_at", "produced_at"]).iloc[-1]
            age = (r.time_window_start - latest.observed_at) / pd.Timedelta(hours=1)
            if age <= MAX_AGE_H:
                assert r.s5p_available and r.s5p_no2_umol_m2 == pytest.approx(latest.no2_mol_m2 * 1e6)
                continue
        assert not r.s5p_available and np.isnan(r.s5p_no2_umol_m2)


def test_fitur_statis_tidak_boleh_sesudah_waktu_inferensi_kecuali_eksplisit(src, stations):
    with pytest.raises(ValueError, match="strict_static"):
        build_feature_matrix(WINDOWS[:1], stations["grid_id"][:3], sources=src, static_cutoff="2026-09-24T00:00Z")
    _, audit = build_feature_matrix(WINDOWS[:1], stations["grid_id"][:3], sources=src,
                                    static_cutoff="2026-09-24T00:00Z", strict_static=False)
    assert audit["static_after_inference"].all()


def test_landsat_hanya_scene_sebelum_batas_waktu(src):
    cutoff = pd.Timestamp("2026-09-20T00:00Z")
    times = pd.read_parquet(src.root / "landsat/scene_times.parquet").set_index("scene_id")
    chosen = src.xland_scenes(cutoff)
    assert chosen
    assert (pd.to_datetime(times.loc[chosen, "observed_at"], utc=True) < cutoff).all()
    assert (pd.to_datetime(times.loc[chosen, "produced_at"], utc=True) <= cutoff).all()


def test_viirs_memakai_konvensi_sudut_barat_laut(src):
    cutoff = pd.Timestamp("2026-09-20T00:00Z")
    act = src.xactivity(cutoff)
    daily = src._viirs_daily()
    some = daily[daily["ntl_available"]].iloc[0]
    rc = [int(x) for x in pd.Series([some.viirs_cell_id]).str.extract(r"h(\d+)v(\d+)_r(\d+)_c(\d+)").iloc[0]]
    lon_w, lat_n = -180 + 10 * rc[0] + rc[3] / 240, 90 - 10 * rc[1] - rc[2] / 240
    from spatial_model.grid import to_grid_xy
    x, y = to_grid_xy(lon_w + 0.5 / 240, lat_n - 0.5 / 240)          # pusat sel VIIRS
    gid = int(CANONICAL_GRID.cell_index(x, y))
    start = cutoff - pd.Timedelta(days=90)
    sel = daily[(daily["viirs_cell_id"] == some.viirs_cell_id) & daily["ntl_available"]
                & (daily["observation_date_utc"] >= start)
                & (daily["observation_date_utc"] + pd.Timedelta(days=1) <= cutoff) & (daily["produced_at"] <= cutoff)]
    assert act.loc[gid, "act_ntl_n_obs"] == len(sel)
    if len(sel):
        assert act.loc[gid, "act_ntl_nw_cm2_sr"] == pytest.approx(sel["ntl"].median(), rel=1e-6)


# Sensor dan determinisme ---------------------------------------------------------
def test_jarak_sensor_hanya_dari_stasiun_yang_diberikan(src, stations):
    one = stations.iloc[[0]]
    others = stations.iloc[1:]
    fm_all, _ = build_feature_matrix(WINDOWS[:1], one["grid_id"], sources=src, sensors=stations)
    fm_excl, _ = build_feature_matrix(WINDOWS[:1], one["grid_id"], sources=src, sensors=others)
    fm_none, _ = build_feature_matrix(WINDOWS[:1], one["grid_id"], sources=src, sensors=None)
    assert fm_all["dist_nearest_sensor_m"].iloc[0] < 71                 # stasiun sendiri di dalam sel 100 m
    assert fm_excl["dist_nearest_sensor_m"].iloc[0] > fm_all["dist_nearest_sensor_m"].iloc[0]
    assert np.isnan(fm_none["dist_nearest_sensor_m"].iloc[0]) and not fm_none["sensor_available"].iloc[0]


def test_deterministik(src, stations, built):
    again, audit2 = build_feature_matrix(WINDOWS, stations["grid_id"], sources=Sources(), sensors=stations)
    assert _hash(built[0]) == _hash(again)
    assert _hash(built[1]) == _hash(audit2)
