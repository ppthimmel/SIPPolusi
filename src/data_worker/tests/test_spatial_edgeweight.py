"""Uji estimasi grid sampai EdgeWeight (TI-AI-05).

Tabel 5.7: UT-SDM-01 (run_downscale_inference), UT-SDM-06 (estimate_confidence),
UT-SDM-07 (aggregate_grid_to_edges). Tabel 5.10: UT-OPS-01
(read_pollution_weight) dan UT-OPS-02 (write_pollution_weight), dijalankan
terhadap PostgreSQL + PostGIS sungguhan pada skema sementara yang dibuat dari
model SQLAlchemy ``database/models.py`` (Tabel 3.17).
"""

import datetime as dt
import uuid

import numpy as np
import pandas as pd
import pytest

from contracts import BBox, EdgeWeight, TimeWindow
from spatial_model.aggregate import aggregate_grid_to_edges, edge_cell_pieces
from spatial_model.confidence import IDW_CAP, estimate_confidence
from spatial_model.grid import GridSpec, to_grid_xy, to_lonlat
from spatial_model.idw import NoGroundTruthError
from spatial_model.inference import load_idw_settings, make_run_id, run_downscale_inference, to_edge_weights

UTC = dt.timezone.utc
WINDOW = TimeWindow.starting_at(dt.datetime(2026, 10, 2, 4, tzinfo=UTC))
# Grid 3 × 1 sel 100 m di Jakarta (EPSG:32748).
X0, Y0 = 700_000.0, 9_310_000.0
SPEC = GridSpec(x0=X0, y0=Y0, nx=3, ny=1, cell_m=100.0)


def _edges(*lines_m):
    """Ruas dari titik-titik dalam meter relatif terhadap (X0, Y0); dikembalikan dalam lon/lat."""
    coords, index = [], []
    for i, line in enumerate(lines_m):
        pts = np.asarray(line, dtype=float)
        lon, lat = to_lonlat(X0 + pts[:, 0], Y0 + pts[:, 1])
        coords.append(np.column_stack([lon, lat]))
        index += [i] * len(pts)
    return np.arange(1, len(lines_m) + 1), np.vstack(coords), np.asarray(index)


def _aggregate(cells, *lines_m):
    grid = pd.DataFrame({"cell_id": np.arange(SPEC.n_cells), **cells})
    pieces, edges = edge_cell_pieces(*_edges(*lines_m), SPEC)
    weights, missing = aggregate_grid_to_edges(grid, pieces, edges)
    return weights.set_index("edge_id"), missing, pieces


# UT-SDM-07 -----------------------------------------------------------------
def test_ut_sdm_07a_ruas_dalam_satu_sel():
    w, _, _ = _aggregate({"pm25_ugm3": [30.0, 0, 0], "no2_ugm3": [50.0, 0, 0]}, [[10, 50], [90, 50]])
    assert w.loc[1, "pm25_ugm3"] == pytest.approx(30.0, abs=1e-6)
    assert w.loc[1, "no2_ugm3"] == pytest.approx(50.0, abs=1e-6)
    assert w.loc[1, "exposure_index"] == pytest.approx(0.5 * (30 / 15 + 50 / 25))


def test_ut_sdm_07b_rata_rata_berbobot_panjang():
    # 60 m di sel bernilai 10, 40 m di sel bernilai 20; ruas bertekuk di sel pertama.
    w, _, pieces = _aggregate({"pm25_ugm3": [10.0, 20.0, 0], "no2_ugm3": [np.nan] * 3},
                              [[40, 50], [70, 50], [140, 50]])
    assert w.loc[1, "pm25_ugm3"] == pytest.approx(14.0, abs=1e-4)
    # Jejak sel sumber: dua potongan 60 m dan 40 m.
    assert sorted(pieces["length_m"].round(3)) == [40.0, 60.0]


def test_ut_sdm_07c_hanya_pm25():
    w, _, _ = _aggregate({"pm25_ugm3": [30.0] * 3, "no2_ugm3": [np.nan] * 3}, [[10, 50], [90, 50]])
    assert np.isnan(w.loc[1, "no2_ugm3"])
    assert w.loc[1, "exposure_index"] == pytest.approx(30.0 / 15)


def test_ut_sdm_07d_hanya_no2():
    w, _, _ = _aggregate({"pm25_ugm3": [np.nan] * 3, "no2_ugm3": [50.0] * 3}, [[10, 50], [90, 50]])
    assert np.isnan(w.loc[1, "pm25_ugm3"])
    assert w.loc[1, "exposure_index"] == pytest.approx(2.0)


def test_ut_sdm_07e_ruas_di_luar_grid_tanpa_estimasi():
    w, missing, _ = _aggregate({"pm25_ugm3": [10.0, 20.0, 30.0], "no2_ugm3": [1.0] * 3},
                               [[500, 500], [600, 500]],          # di luar grid
                               [[0, 0], [300, 100]],              # diagonal melintasi tiga sel
                               [[250, 50], [350, 50]])            # separuh di luar grid
    assert 1 not in w.index and missing["edge_id"].tolist() == [1]
    assert w.loc[2, "pm25_ugm3"] == pytest.approx(20.0, abs=1e-4)
    assert w.loc[2, "n_cells"] == 3
    assert w.loc[3, "pm25_ugm3"] == pytest.approx(30.0, abs=1e-6)


# UT-SDM-06 -----------------------------------------------------------------
def test_ut_sdm_06a_rentang_dan_monoton_terhadap_jarak():
    rng = np.random.default_rng(42)
    d = np.sort(rng.uniform(0, 50_000, 2_000))
    for source in ("idw", "stgnn"):
        c = estimate_confidence(d, estimation_source=source)
        assert ((c >= 0) & (c <= 1)).all()
        assert (np.diff(c) <= 1e-12).all()


def test_ut_sdm_06b_sel_tanpa_aod_lebih_rendah():
    c = estimate_confidence([1000.0, 1000.0], affected_zones=[False, True], estimation_source="stgnn")
    assert c[1] < c[0]


def test_ut_sdm_06c_idw_berkategori_keyakinan_rendah():
    c = estimate_confidence([0.0, 1.0, 500.0, np.nan], estimation_source="idw")
    assert (c <= 0.4).all() and (c < 0.4).all()        # kategori rendah: < 0,4
    assert c[0] == pytest.approx(IDW_CAP) and c[3] == 0.0


def test_estimate_confidence_menolak_sumber_tidak_dikenal():
    with pytest.raises(ValueError):
        estimate_confidence([0.0], estimation_source="lidar")


# Fixture inferensi ------------------------------------------------------------
STATIONS = pd.DataFrame({
    "station_id": ["A", "B", "C"],
    "x": [X0 + 50, X0 + 150, X0 + 250],
    "pm25": [10.0, 20.0, 30.0],
    "no2": [25.0, np.nan, 75.0],
})


def _ground_truth():
    lon, lat = to_lonlat(STATIONS["x"], np.full(3, Y0 + 50))
    rows = []
    for i, s in STATIONS.iterrows():
        for p in ("pm25", "no2"):
            if np.isfinite(s[p]):
                rows.append({"station_id": s.station_id, "lon": lon[i], "lat": lat[i], "pollutant": p, "value": s[p]})
    return pd.DataFrame(rows)


EDGE_LINES = ([[10, 50], [90, 50]], [[40, 20], [70, 20], [140, 20]], [[0, 0], [300, 100]], [[500, 500], [600, 500]])


def _run(**kwargs):
    return run_downscale_inference(WINDOW, ground_truth=_ground_truth(), edges=_edges(*EDGE_LINES),
                                   graph_version="g-test", grid_spec=SPEC, **kwargs)


# UT-SDM-01 -----------------------------------------------------------------
def test_ut_sdm_01a_c_tanpa_model_jatuh_ke_idw_dan_runsummary_lengkap(tmp_path):
    summary = _run(artifact_dir=tmp_path)
    settings = load_idw_settings()
    assert summary.status == "complete"
    assert summary.estimation_source == "idw"
    assert summary.model_version == f"idw-p{settings['power']:g}-k{settings['neighbors']}"
    assert "ModelUnavailableError" in summary.fallback_reason
    assert summary.coverage_ratio == pytest.approx(3 / 4)          # satu ruas di luar grid
    dist = summary.confidence_distribution
    assert dist["n"] == 3 and dist["max"] < 0.4 and dist["low_lt_0_4"] == 3
    run_dir = tmp_path / summary.run_id
    for name in ("grid_prediction.parquet", "edge_cell_pieces.parquet", "edge_weights.parquet",
                 "ground_truth.parquet", "edges_without_estimate.parquet", "run_manifest.json"):
        assert (run_dir / name).exists()


def test_ut_sdm_01b_run_id_deterministik():
    assert make_run_id(WINDOW, "idw-p2-k8") == make_run_id(WINDOW, "idw-p2-k8")
    assert make_run_id(WINDOW, "idw-p2-k8") != make_run_id(WINDOW, "idw-p1-k8")
    assert _run().run_id == _run().run_id


def test_ut_sdm_08d_tanpa_ground_truth_tidak_ada_keluaran():
    with pytest.raises(NoGroundTruthError):
        run_downscale_inference(WINDOW, ground_truth=_ground_truth().iloc[0:0], edges=_edges(*EDGE_LINES),
                                graph_version="g-test", grid_spec=SPEC)


def test_edge_weight_sesuai_kontrak_tabel_3_3():
    summary_weights = to_edge_weights(
        pd.DataFrame({"edge_id": [7], "pm25_ugm3": [12.5], "no2_ugm3": [np.nan], "exposure_index": [12.5 / 15],
                      "confidence_score": [0.2], "background_source": [None], "estimation_source": ["idw"]}),
        WINDOW)
    w = summary_weights[0]
    assert isinstance(w, EdgeWeight)
    assert w.no2_ugm3 is None and w.time_window_start == WINDOW.start and w.estimation_source == "idw"


# Basis data: UT-OPS-01, UT-OPS-02, kompatibilitas cache --------------------------
@pytest.fixture
def cache_db(pg_dsn):
    """Skema pollution dan osm sementara, dibuat dari model SQLAlchemy Tabel 3.17."""
    import psycopg
    from sqlalchemy import create_engine, text

    from database.models import OsmBase, PollutionBase

    suffix = uuid.uuid4().hex[:10]
    schemas = {"pollution": f"p_{suffix}", "osm": f"o_{suffix}"}
    url = pg_dsn.replace("postgresql://", "postgresql+psycopg2://", 1)
    engine = create_engine(url)
    with engine.begin() as conn:
        conn.execute(text("CREATE EXTENSION IF NOT EXISTS postgis"))
        for s in schemas.values():
            conn.execute(text(f'CREATE SCHEMA "{s}"'))
    mapped = engine.execution_options(schema_translate_map=schemas)
    PollutionBase.metadata.create_all(mapped)
    OsmBase.metadata.create_all(mapped)
    conn = psycopg.connect(pg_dsn, autocommit=True)
    # Ruas uji di osm.road_edge, graf berstatus active.
    edge_ids, coords, index = _edges(*EDGE_LINES)
    with conn.transaction():
        for eid in edge_ids:
            pts = coords[index == eid - 1]
            wkt = "LINESTRING(" + ", ".join(f"{x} {y}" for x, y in pts) + ")"
            conn.execute(f'INSERT INTO "{schemas["osm"]}".road_edge (edge_id, graph_version, u, v, length_m, geom) '
                         "VALUES (%s, 'g-test', 0, 0, 0, ST_GeomFromText(%s, 4326))", (int(eid), wkt))
        conn.execute(f'INSERT INTO "{schemas["osm"]}".graph_version (version, status) VALUES (\'g-test\', \'active\')')
    yield conn, schemas
    conn.close()
    with engine.begin() as c:
        for s in schemas.values():
            c.execute(text(f'DROP SCHEMA IF EXISTS "{s}" CASCADE'))
    engine.dispose()


def _synthetic_weights(n, start_id=1_000_000, value=20.0):
    return pd.DataFrame({
        "edge_id": np.arange(start_id, start_id + n), "pm25_ugm3": value, "no2_ugm3": 40.0,
        "exposure_index": 0.5 * (value / 15 + 40 / 25), "confidence_score": 0.3,
        "background_source": None, "estimation_source": "idw",
    })


def test_ut_ops_02a_b_tulis_1000_baris_dan_ulang_tanpa_duplikat(cache_db):
    from spatial_model import cache

    conn, s = cache_db
    n = cache.write_pollution_weight(conn, _synthetic_weights(1000), WINDOW, "g-test", "idw-p2-k8",
                                     coverage_ratio=1.0, pollution_schema=s["pollution"], osm_schema=s["osm"])
    assert n == 1000
    assert cache.window_record(conn, WINDOW.start, s["pollution"])["status"] == "complete"
    cache.write_pollution_weight(conn, _synthetic_weights(1000, value=25.0), WINDOW, "g-test", "idw-p2-k8",
                                 pollution_schema=s["pollution"], osm_schema=s["osm"])
    count, distinct, pm25 = conn.execute(
        f'SELECT count(*), count(DISTINCT edge_id), max(pm25_ugm3) FROM "{s["pollution"]}".edge_pollution'
    ).fetchone()
    assert (count, distinct) == (1000, 1000) and pm25 == pytest.approx(25.0)


def test_ut_ops_02c_galat_di_tengah_transaksi_tidak_meninggalkan_apa_pun(cache_db):
    from spatial_model import cache

    conn, s = cache_db
    previous = TimeWindow.starting_at(WINDOW.start - dt.timedelta(hours=1))
    cache.write_pollution_weight(conn, _synthetic_weights(10), previous, "g-test", "idw-p2-k8",
                                 pollution_schema=s["pollution"], osm_schema=s["osm"])

    def boom():
        raise RuntimeError("galat disuntikkan")

    with pytest.raises(RuntimeError):
        cache.write_pollution_weight(conn, _synthetic_weights(1000), WINDOW, "g-test", "idw-p2-k8",
                                     pollution_schema=s["pollution"], osm_schema=s["osm"], before_commit=boom)
    rows = conn.execute(f'SELECT count(*) FROM "{s["pollution"]}".edge_pollution WHERE time_window_start = %s',
                        (WINDOW.start,)).fetchone()[0]
    assert rows == 0
    assert cache.window_record(conn, WINDOW.start, s["pollution"]) is None
    assert cache.latest_complete_window(conn, s["pollution"]) == previous.start


def test_ut_ops_01_baca_time_window_complete_terakhir_dan_bbox(cache_db):
    from spatial_model import cache

    conn, s = cache_db
    # (c) belum ada time window complete
    assert cache.read_pollution_weight(conn, BBox(106, -7, 107, -6), pollution_schema=s["pollution"]) == ([], None)
    _run(conn=conn, pollution_schema=s["pollution"], osm_schema=s["osm"])
    # (a) 05.00 berstatus writing tidak terbaca
    later = TimeWindow.starting_at(WINDOW.start + dt.timedelta(hours=1))
    conn.execute(f'INSERT INTO "{s["pollution"]}".pollution_window (time_window_start, status) VALUES (%s, %s)',
                 (later.start, "writing"))
    weights, window_start = cache.read_pollution_weight(conn, BBox(106, -7, 107, -6), pollution_schema=s["pollution"])
    assert window_start == WINDOW.start
    assert sorted(w.edge_id for w in weights) == [1, 2, 3]
    # (b) bbox memotong sebagian ruas: hanya sekitar ruas 1 (x 10–90 m dari X0)
    lon0, lat0 = to_lonlat(X0 + 5, Y0 + 45)
    lon1, lat1 = to_lonlat(X0 + 30, Y0 + 55)
    part, _ = cache.read_pollution_weight(conn, BBox(float(lon0), float(lat0), float(lon1), float(lat1)),
                                          pollution_schema=s["pollution"])
    assert [w.edge_id for w in part] == [1]


def test_kompatibilitas_cache_tulis_baca_ulang_dan_idempoten(cache_db, tmp_path):
    from spatial_model import cache

    conn, s = cache_db
    first = _run(conn=conn, artifact_dir=tmp_path, pollution_schema=s["pollution"], osm_schema=s["osm"])
    assert first.status == "complete" and first.rows_written == 3
    record = cache.window_record(conn, WINDOW.start, s["pollution"])
    assert record["status"] == "complete" and record["model_version"] == first.model_version
    assert record["coverage_ratio"] == pytest.approx(0.75)
    # Nilai yang dibaca Backend sama dengan EdgeWeight yang dihitung (REAL ≈ float32).
    computed = pd.read_parquet(tmp_path / first.run_id / "edge_weights.parquet").set_index("edge_id")
    weights, _ = cache.read_pollution_weight(conn, BBox(106, -7, 107, -6), WINDOW, pollution_schema=s["pollution"])
    for w in weights:
        row = computed.loc[w.edge_id]
        assert w.pm25_ugm3 == pytest.approx(row.pm25_ugm3, rel=1e-6)
        assert w.exposure_index == pytest.approx(row.exposure_index, rel=1e-6)
        assert w.confidence_score == pytest.approx(row.confidence_score, rel=1e-6)
        assert w.estimation_source == "idw" and w.background_source is None
    # Ruas 1 hanya di sel stasiun A (PM2.5 10, NO2 25) dan bertetangga; NO2 B kosong tidak membuat NO2 ruas kosong.
    assert all(w.no2_ugm3 is not None for w in weights)
    # graph_version, model_version, dan geometri ikut tersimpan.
    gv, mv, has_geom = conn.execute(
        f'SELECT min(graph_version), min(model_version), bool_and(geom IS NOT NULL) FROM "{s["pollution"]}".edge_pollution'
    ).fetchone()
    assert (gv, mv, has_geom) == ("g-test", first.model_version, True)
    # (UT-SDM-01b) pemanggilan kedua: tidak ada eksekusi ganda, run_id sama.
    second = _run(conn=conn, pollution_schema=s["pollution"], osm_schema=s["osm"])
    assert second.status == "skipped" and second.run_id == first.run_id
