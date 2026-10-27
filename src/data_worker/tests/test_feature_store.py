"""Uji penyimpanan ``features`` dan langkah siklus TI-SE-02 terhadap PostgreSQL sungguhan.

Membutuhkan ``TEST_DATABASE_URL`` atau Docker (lihat ``conftest.py``); tanpa itu dilewati.
"""

from __future__ import annotations

import datetime as dt
import json
import logging
import pathlib

import numpy as np
import pandas as pd
import pytest

from connectors import open_meteo
from connectors.http import Policy, SourceError
from contracts import TimeWindow
from feature_matrix.grid import active_cells
from feature_matrix.sources import WEATHER, SourceReader
from feature_store import steps
from feature_store.reader import StoreSourceReader
from feature_store.seed import seed
from feature_store.store import decode_layer, encode_layer
from test_connectors import FakeResponse, FakeSession, fixture_json

UTC = dt.timezone.utc
T0 = dt.datetime(2026, 10, 27, 5, 15, tzinfo=UTC)
WINDOW = TimeWindow.starting_at(dt.datetime(2026, 10, 27, 4, tzinfo=UTC))
DATASET = pathlib.Path(__file__).resolve().parents[1] / "dataset_processed"


@pytest.fixture
def fs(db):
    store = db.features()
    yield store
    store.close()


def _weather_frame():
    frame, _ = open_meteo.parse(fixture_json("open_meteo_forecast.json"))
    return frame


def test_layer_dikodekan_tanpa_kehilangan():
    values = np.array([0.1, np.nan, -3.5], "float32")
    np.testing.assert_array_equal(decode_layer(*encode_layer(values)), values)
    with pytest.raises(ValueError):
        dtype, n, data = encode_layer(values)
        decode_layer(dtype, n + 1, data)


def test_cuaca_idempoten_dan_revisi_berubah(fs):
    frame = _weather_frame()
    assert fs.write_weather(frame, "f1", T0) == 96
    assert fs.write_weather(frame, "f2", T0 + dt.timedelta(hours=1)) == 0, "data duplikat tidak ditulis ulang"
    changed = frame.copy()
    changed.loc[5, "temperature_2m"] = 40.0
    changed.loc[6, "precipitation"] = np.nan
    later = T0 + dt.timedelta(hours=2)
    assert fs.write_weather(changed, "f3", later) == 2
    latest = fs.read_weather()
    assert len(latest) == 96 and latest.temperature_2m.iloc[5] == 40.0
    assert not latest.precipitation_available.iloc[6], "nilai hilang tetap NULL dan bertanda tidak tersedia"
    before = fs.read_weather(as_of=T0)
    assert before.temperature_2m.iloc[5] == frame.temperature_2m.iloc[5], "as_of membaca revisi lama"


def test_geoscf_dan_produk_satelit_duplikat_diabaikan(fs):
    frame = pd.DataFrame({"time_window_start": pd.Timestamp("2026-10-26 10:00", tz="UTC"), "cell_id": np.arange(9),
                          "pm25_ugm3": 50.0, "no2_ppb": 10.0, "no2_ugm3": 18.8,
                          "available_at_utc": pd.Timestamp("2026-10-27 03:00", tz="UTC"), "latency_hours": 16.0})
    assert fs.write_geoscf_ana(frame, "g1", T0) == 9
    assert fs.write_geoscf_ana(frame, "g2", T0) == 0
    record = {"scene_id": "VNP46A2.A2026290.h28v09.002.2026298100727.h5", "observed_start_at": pd.Timestamp(
        "2026-10-17", tz="UTC"), "observed_at": pd.Timestamp("2026-10-17 23:59:59", tz="UTC"),
        "produced_at": pd.Timestamp("2026-10-25 10:07:27", tz="UTC")}
    layers = {"ntl": np.array([1.0, np.nan], "float32")}
    kwargs = dict(fetch_id="v1", fetched_utc=T0, processing_version="viirs-ntl-3", n_cells=2, layers=layers)
    assert fs.write_product("viirs", record, **kwargs) is True
    assert fs.write_product("viirs", record, **kwargs) is False
    np.testing.assert_array_equal(fs.layers("viirs", record["scene_id"])["ntl"], layers["ntl"])
    assert fs.product_ids("viirs") == {record["scene_id"]}


def test_retensi_memangkas_data_lama(fs):
    assert fs.write_weather(_weather_frame(), "f1", T0) == 96
    removed = fs.prune(T0 + dt.timedelta(days=60))
    assert removed["weather_hourly"] == 96 and fs.read_weather().empty


def test_sumber_gagal_tercatat_dan_sumber_lain_tetap_berjalan(fs):
    def broken(client, fetch_id):
        raise SourceError("geoscf_ana: HEAD https://portal.test/x.nc4 gagal: Timeout")

    def fine(client, fetch_id):
        return steps.SourceResult("ok", rows_written=3)

    a = steps.run_source(fs, "geoscf_ana", WINDOW, Policy(1), broken, now=T0, environ={})
    b = steps.run_source(fs, "open_meteo", WINDOW, Policy(1), fine, now=T0, environ={})
    assert a["status"] == "failed" and "Timeout" in a["note"] and b["status"] == "ok"
    combined = steps.combine({"geoscf_ana": a, "open_meteo": b})
    assert combined["status"] == "failed" and "geoscf_ana" in combined["note"]
    log = fs.fetch_log(T0 - dt.timedelta(days=1)).set_index("source")
    assert log.loc["geoscf_ana", "status"] == "failed" and log.loc["geoscf_ana", "finished_utc"] is not None
    assert "Timeout" in log.loc["geoscf_ana", "note"] and log.loc["open_meteo", "rows_written"] == 3


def test_galat_tak_terduga_tidak_menghentikan_dan_format_berubah_dicatat(fs):
    from connectors.http import FormatError

    def bug(client, fetch_id):
        raise KeyError("x")

    def changed(client, fetch_id):
        raise FormatError("open_meteo: satuan berubah")

    assert steps.run_source(fs, "landsat", WINDOW, Policy(1), bug, now=T0, environ={})["status"] == "failed"
    result = steps.run_source(fs, "open_meteo", WINDOW, Policy(1), changed, now=T0, environ={})
    assert result["status"] == "failed" and result["note"].startswith("format respons berubah")


def test_jadwal_katalog_harian_dan_sumber_nonaktif(fs):
    calls = []

    def fn(client, fetch_id):
        calls.append(fetch_id)
        return steps.SourceResult("ok", detail={"pending": 0})

    first = steps.run_source(fs, "landsat", WINDOW, Policy(1), fn, now=T0, environ={})
    again = steps.run_source(fs, "landsat", WINDOW, Policy(1), fn, now=T0 + dt.timedelta(hours=1), environ={})
    later = steps.run_source(fs, "landsat", WINDOW, Policy(1), fn, now=T0 + dt.timedelta(hours=25), environ={})
    assert (first["status"], again["status"], later["status"]) == ("ok", "not_due", "ok") and len(calls) == 2
    off = steps.run_source(fs, "viirs", WINDOW, Policy(1), fn, now=T0, environ={"FEATURES_SOURCES": "open_meteo"})
    assert off["status"] == "disabled"
    assert steps.combine({"viirs": off, "landsat": again})["status"] == "ok"


def test_langkah_meteo_dengan_respons_rekaman_dan_timeout(db, fs, monkeypatch):
    from connectors import http
    from test_collect import _config

    payload = json.dumps(fixture_json("open_meteo_forecast.json"))
    monkeypatch.setenv("FEATURES_SOURCES", "open_meteo")
    monkeypatch.setattr(http.requests, "Session", lambda: FakeSession([("GET", "open-meteo", FakeResponse(200, payload))]))
    context = {}
    result = steps.fetch_meteo(WINDOW, _config(db), context)
    assert result["status"] == "ok" and result["sources"]["open_meteo"]["rows_written"] == 96
    again = steps.fetch_meteo(WINDOW, _config(db), context)
    assert again["sources"]["open_meteo"]["rows_written"] == 0
    import requests

    monkeypatch.setattr(http.requests, "Session", lambda: FakeSession([("GET", "open-meteo", requests.Timeout())]))
    monkeypatch.setattr(http.time, "sleep", lambda s: None)
    failed = steps.fetch_meteo(WINDOW, _config(db), context)
    assert failed["status"] == "failed" and failed["sources"]["open_meteo"]["failed_requests"] == 1
    assert failed["sources"]["open_meteo"]["requests"] == 1
    context["feature_store"].close()


def test_kendali_mutu_menandai_sumber_tidak_tersedia(fs):
    fs.write_weather(_weather_frame(), "f1", T0)
    availability = steps.source_availability(fs, pd.Timestamp(WINDOW.end))
    assert availability["open_meteo"]["available"] and availability["open_meteo"]["age_hours"] <= 8
    assert not availability["landsat"]["available"] and availability["landsat"]["latest"] is None


@pytest.fixture
def seeded(fs):
    """Arsip data latih di skema features uji: satelit seluruhnya (as-of memakai pengamatan valid
    terakhir, berapa pun umurnya), sumber per jam sejak 20 September."""
    if not (DATASET / "landsat/scenes").exists():
        pytest.skip("dataset_processed tidak tersedia")
    since = {s: "2026-09-20" for s in ("open_meteo", "geoscf_ana", "geoscf_fcst")}
    since.update({s: "2026-01-01" for s in ("sentinel5p", "viirs", "landsat")})
    seed(fs, DATASET, since=since)
    return fs


def test_reader_postgresql_identik_dengan_reader_data_latih(seeded):
    tau = pd.Timestamp("2026-09-29T12:00Z")
    cells = active_cells().grid_id.tolist()[::150]
    expected = SourceReader(DATASET)(cells, tau, tau + pd.Timedelta(hours=3))
    actual = StoreSourceReader(seeded)(cells, tau, tau + pd.Timedelta(hours=3))
    assert list(actual.columns) == list(expected.columns) and len(actual) == len(expected)
    for column in expected.columns:
        a, b = expected[column], actual[column]
        if pd.api.types.is_float_dtype(a):
            np.testing.assert_array_equal(a.to_numpy("float64"), b.to_numpy("float64"), err_msg=column)
        else:
            same = (a.astype(object) == b.astype(object)) | (a.isna() & b.isna())
            assert same.all(), column


def test_write_features_dan_bangun_ulang_feature_version(seeded, db, monkeypatch):
    window = TimeWindow.starting_at(dt.datetime(2026, 9, 29, 11, tzinfo=UTC))
    # Time window arsip lebih tua dari retensi relatif ke jam sekarang; retensi diuji terpisah.
    monkeypatch.setattr(type(seeded), "prune", lambda self, now, retention=None: {})
    sensors = pd.DataFrame({"station_longitude": [106.8227], "station_latitude": [-6.1754]})
    context = {"feature_store": seeded, "run_id": "uji",
               "availability": steps.source_availability(seeded, pd.Timestamp(window.end))}
    built = dt.datetime.now(UTC)
    context.update(feature_matrix=steps.build_feature_matrix(seeded, window, sensors, as_of=built),
                   built_utc=built, sensors=sensors)
    matrix = context["feature_matrix"]
    assert matrix.shape == (195782, 89) and matrix.time_utc.eq(pd.Timestamp(window.end)).all()
    assert matrix.ndvi_available.any() and matrix.geoscf_fcst_pm25_ugm3_available.all()
    result = steps.write_features(window, None, context)
    assert result["status"] == "ok" and result["new_version"] and len(result["feature_version"]) == 64
    assert steps.write_features(window, None, context)["new_version"] is False, "penulisan ulang idempoten"
    # Revisi sumber yang masuk sesudah built_utc tidak mengubah hasil bangun ulang.
    frame = seeded.read_weather(since=pd.Timestamp("2026-09-29 00:00", tz="UTC"))
    frame[WEATHER] = frame[WEATHER] + 1.0
    seeded.write_weather(frame.assign(**{f"{v}_qc": False for v in WEATHER}), "late", dt.datetime.now(UTC))
    rebuilt, manifest = steps.read_features(seeded, window)
    assert manifest["feature_version"] == result["feature_version"]
    pd.testing.assert_frame_equal(rebuilt, matrix)
    # Data sumber yang hilang (mis. terpangkas retensi) terdeteksi, bukan diam-diam menghasilkan matriks lain.
    seeded.conn.execute(f"DELETE FROM {seeded.s}.geoscf_ana_hourly")
    with pytest.raises(ValueError, match="tidak cocok"):
        steps.read_features(seeded, window)


def test_galat_tak_terduga_dengan_url_bertanda_tangan_tidak_bocor(fs, caplog):
    """Pesan pustaka yang memuat URL SAS tidak masuk log maupun fetch_log (tinjauan PR #63)."""
    def leaky(client, fetch_id):
        raise RuntimeError("baca gagal https://blob.test/s5p.nc?sv=2026&sig=RAHASIA%3D")

    with caplog.at_level(logging.INFO):
        result = steps.run_source(fs, "sentinel5p", WINDOW, Policy(1), leaky, now=T0, environ={})
    assert result["status"] == "failed" and "https://blob.test/s5p.nc" in result["note"]
    note = fs.fetch_log(T0 - dt.timedelta(days=1)).set_index("source").loc["sentinel5p", "note"]
    assert "RAHASIA" not in note and "RAHASIA" not in result["note"]
    assert "RAHASIA" not in caplog.text and "RuntimeError" in caplog.text
