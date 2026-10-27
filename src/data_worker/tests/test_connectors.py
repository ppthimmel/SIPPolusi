"""Uji unit konektor sumber sekunder TI-SE-02 dengan respons rekaman (tanpa jaringan).

Setiap sumber diuji untuk respons valid, respons kosong, format berubah,
timeout, dan data duplikat (acceptance criteria #2). Respons rekaman ada di
``tests/fixtures/sources``; granule HDF5 dibuat sintetis karena ukurannya
puluhan MB.
"""

from __future__ import annotations

import datetime as dt
import io
import json
import logging
import pathlib

import numpy as np
import pandas as pd
import pytest
import requests

from connectors import geoscf, landsat, open_meteo, sentinel5p, stac, viirs
from connectors.http import FormatError, HttpClient, Policy, RangeFile, SourceError, safe_url

FIX = pathlib.Path(__file__).resolve().parent / "fixtures" / "sources"
UTC = dt.timezone.utc


def fixture_text(name: str) -> str:
    return (FIX / name).read_text(encoding="utf-8")


def fixture_json(name: str):
    return json.loads(fixture_text(name))


class FakeResponse:
    def __init__(self, status=200, body=b"", headers=None, url="https://example.invalid/"):
        self.status_code = status
        self.content = body if isinstance(body, bytes) else body.encode()
        self.headers = headers or {}
        self.url = url

    @property
    def text(self):
        return self.content.decode()

    def json(self):
        return json.loads(self.content)

    def iter_content(self, size):
        for i in range(0, len(self.content), size):
            yield self.content[i:i + size]

    def close(self):
        pass


class FakeSession:
    """``requests.Session`` tiruan: rute (metode, potongan URL) → respons, galat, urutan, atau fungsi."""

    def __init__(self, routes):
        self.routes = routes
        self.headers = {}
        self.calls = []

    def request(self, method, url, **kwargs):
        self.calls.append((method, url, kwargs))
        for m, part, answer in self.routes:
            if m == method and part in url:
                if isinstance(answer, list):
                    answer = answer.pop(0) if len(answer) > 1 else answer[0]
                if callable(answer) and not isinstance(answer, FakeResponse):
                    answer = answer(url, kwargs)
                if isinstance(answer, BaseException):
                    raise answer
                if answer.url == "https://example.invalid/":
                    answer.url = url
                return answer
        return FakeResponse(404, b"tidak ada rute")


def client(routes, source="uji", policy=Policy(timeout_s=1, tries=3, backoff_s=0)):
    return HttpClient(source, policy, session=FakeSession(routes), sleep=lambda s: None)


# HTTP: coba ulang, timeout, gagal permanen -------------------------------------
def test_gagal_sementara_lalu_berhasil_dihitung_sebagai_coba_ulang(caplog):
    c = client([("GET", "/a", [FakeResponse(503), FakeResponse(200, b"ok")])])
    with caplog.at_level(logging.INFO):
        assert c.get("https://x.test/a").text == "ok"
    assert c.counts() == {"requests": 1, "retried_requests": 1, "failed_requests": 0}
    assert "gagal pada percobaan 1/3" in caplog.text and "berhasil pada percobaan 2" in caplog.text


def test_timeout_permanen_menjadi_source_error_dan_tercatat(caplog):
    c = client([("GET", "/a", requests.Timeout("lambat"))])
    with caplog.at_level(logging.WARNING), pytest.raises(SourceError, match="Timeout"):
        c.get("https://x.test/a?token=RAHASIA")
    assert c.counts()["failed_requests"] == 1 and len(c.session.calls) == 3
    assert "tetap gagal" in caplog.text
    assert "RAHASIA" not in caplog.text, "query (token) tidak boleh masuk log"


def test_http_404_tidak_dicoba_ulang():
    c = client([("GET", "/a", FakeResponse(404))])
    with pytest.raises(SourceError, match="HTTP 404"):
        c.get("https://x.test/a")
    assert len(c.session.calls) == 1


def test_http_429_mematuhi_retry_after():
    waits = []
    c = HttpClient("uji", Policy(timeout_s=1, tries=2, backoff_s=0), sleep=waits.append,
                   session=FakeSession([("GET", "/a", [FakeResponse(429, headers={"Retry-After": "7"}),
                                                       FakeResponse(200, b"ok")])]))
    assert c.get("https://x.test/a").text == "ok"
    assert waits == [7.0]


def test_header_kredensial_tidak_dikirim_ke_url_hasil_pengalihan():
    session = FakeSession([("GET", "laads", FakeResponse(206, headers={"Content-Range": "bytes 0-0/10"},
                                                        url="https://bucket.s3.test/f.h5?X-Amz=sig")),
                           ("GET", "bucket", FakeResponse(206, b"0123456789"))])
    c = HttpClient("viirs", Policy(1, 1), session=session, headers={"Authorization": "Bearer t"})
    remote = RangeFile(c, "https://laads.test/f.h5", block_size=10)
    assert remote.read(4) == b"0123"
    assert "Authorization" in session.calls[0][2]["headers"]
    assert "Authorization" not in session.calls[1][2]["headers"]


def test_safe_url_membuang_query():
    assert safe_url("https://a.test/p/f.nc?st=1&sig=abc") == "https://a.test/p/f.nc"


# Open-Meteo -------------------------------------------------------------------
def test_open_meteo_respons_valid():
    frame, quality = open_meteo.parse(fixture_json("open_meteo_forecast.json"))
    assert len(frame) == 96 and frame.time_utc.is_monotonic_increasing
    assert str(frame.time_utc.dt.tz) == "UTC" and frame.time_utc.dt.minute.eq(0).all()
    assert quality["duplicate_hours"] == 0 and frame[open_meteo.WEATHER].notna().all().all()


def test_open_meteo_respons_kosong():
    payload = fixture_json("open_meteo_forecast.json")
    payload["hourly"] = {k: [] for k in payload["hourly"]}
    frame, quality = open_meteo.parse(payload)
    assert frame.empty and quality["hours"] == 0


@pytest.mark.parametrize("change, message", [
    (lambda p: p["hourly"].pop("boundary_layer_height"), "variabel hilang"),
    (lambda p: p["hourly_units"].update(wind_speed_10m="m/s"), "satuan berubah"),
    (lambda p: p.update(utc_offset_seconds=25200), "bukan UTC"),
    (lambda p: p["hourly"]["precipitation"].pop(), "panjang larik"),
    (lambda p: p.pop("hourly"), "tanpa objek"),
])
def test_open_meteo_format_berubah(change, message):
    payload = fixture_json("open_meteo_forecast.json")
    change(payload)
    with pytest.raises(FormatError, match=message):
        open_meteo.parse(payload)


def test_open_meteo_jam_ganda_dan_nilai_di_luar_rentang():
    payload = fixture_json("open_meteo_forecast.json")
    hourly = payload["hourly"]
    for key in hourly:
        hourly[key].append(hourly[key][-1])          # jam terakhir terkirim dua kali
    hourly["relative_humidity_2m"][0] = 140.0
    hourly["temperature_2m"][1] = None
    frame, quality = open_meteo.parse(payload)
    assert len(frame) == 96 and quality["duplicate_hours"] == 1
    assert np.isnan(frame.relative_humidity_2m.iloc[0]) and frame.relative_humidity_2m_qc.iloc[0]
    assert quality["out_of_range"] == {"relative_humidity_2m": 1}
    assert np.isnan(frame.temperature_2m.iloc[1]) and quality["missing"]["temperature_2m"] == 1


def test_open_meteo_timeout_dicoba_tiga_kali():
    c = client([("GET", "open-meteo", requests.Timeout())], source="open_meteo", policy=open_meteo.POLICY)
    c.sleep = lambda s: None
    with pytest.raises(SourceError):
        open_meteo.fetch(c)
    assert len(c.session.calls) == 3 and c.session.calls[0][2]["timeout"] == 30


# GEOS-CF ----------------------------------------------------------------------
ANA_HOURS = list(pd.date_range("2026-09-30 20:00", periods=3, freq="h", tz="UTC"))


def _ana_published():
    meta = fixture_json("geoscf_ana_meta.json")
    return {pd.Timestamp(k): geoscf._http_time(v) for k, v in meta["last_modified"].items()}


def test_geoscf_ana_subset_valid():
    frame, quality = geoscf.parse_ana(fixture_text("geoscf_ana_subset.ascii"), _ana_published())
    assert len(frame) == 27 and set(frame.cell_id) == set(range(9))
    assert list(frame.time_window_start.unique()) == ANA_HOURS
    assert frame.available_at_utc.notna().all() and (frame.latency_hours > 6).all()
    np.testing.assert_allclose(frame.no2_ugm3, frame.no2_ppb * np.float32(geoscf.UGM3_PER_PPB), rtol=1e-6)
    assert quality["duplicate_rows"] == 0


def test_geoscf_ana_hanya_jam_yang_sudah_terbit():
    published = _ana_published()
    published.pop(ANA_HOURS[-1])
    frame, _ = geoscf.parse_ana(fixture_text("geoscf_ana_subset.ascii"), published)
    assert ANA_HOURS[-1] not in set(frame.time_window_start) and len(frame) == 18


def test_geoscf_ana_format_berubah():
    text = fixture_text("geoscf_ana_subset.ascii").replace("lat, [3]", "latitude, [3]")
    with pytest.raises(FormatError):
        geoscf.parse_ana(text, _ana_published())


def test_geoscf_ana_kandidat_melewati_jam_tersimpan():
    now = dt.datetime(2026, 10, 27, 5, 15, tzinfo=UTC)
    stored = {pd.Timestamp("2026-10-26 20:00", tz="UTC")}
    pending = geoscf.ana_candidates(now, stored)
    assert pending[-1] == pd.Timestamp("2026-10-26 22:00", tz="UTC")   # latensi minimum 6 jam
    assert stored.isdisjoint(pending) and len(pending) == geoscf.ANA_LOOKBACK_HOURS - 1


def _ana_routes(head_status=200):
    meta = fixture_json("geoscf_ana_meta.json")
    by_name = {geoscf.ana_file_url(pd.Timestamp(k)).rsplit("/", 1)[1]: v for k, v in meta["last_modified"].items()}

    def head(url, kwargs):
        name = url.rsplit("/", 1)[1]
        if name in by_name:
            return FakeResponse(head_status, headers={"Last-Modified": by_name[name]})
        return FakeResponse(404)
    return [("HEAD", "datashare", head),
            ("GET", ".dds", FakeResponse(200, fixture_text("geoscf_ana.dds"))),
            ("GET", "time[0:0]", FakeResponse(200, fixture_text("geoscf_ana_time0.ascii"))),
            ("GET", "pm25_rh35", FakeResponse(200, fixture_text("geoscf_ana_subset.ascii")))]


def test_geoscf_fetch_ana_dari_rekaman():
    now = dt.datetime(2026, 10, 1, 5, 15, tzinfo=UTC)
    frame, info = geoscf.fetch_ana(client(_ana_routes()), now, stored=set())
    assert info["published_hours"] == 3 and len(frame) == 27


def test_geoscf_fetch_ana_tanpa_jam_baru_tidak_meminta_subset():
    c = client(_ana_routes())
    now = dt.datetime(2026, 10, 1, 5, 15, tzinfo=UTC)
    frame, info = geoscf.fetch_ana(c, now, stored=set(ANA_HOURS))
    assert frame.empty and info["published_hours"] == 0
    assert not any("pm25_rh35" in url for _, url, _ in c.session.calls)


def test_geoscf_timeout_portal_menjadi_source_error():
    c = client([("HEAD", "datashare", requests.Timeout())], policy=geoscf.POLICY)
    c.sleep = lambda s: None
    with pytest.raises(SourceError):
        geoscf.fetch_ana(c, dt.datetime(2026, 10, 1, 5, 15, tzinfo=UTC), stored=set())


def test_geoscf_fcst_run_dari_rekaman():
    heads = fixture_json("geoscf_fcst_meta.json")
    init = pd.Timestamp("2026-09-30 09:00", tz="UTC")
    times = geoscf.fcst_script.run_times(init)
    first, last = (geoscf.fcst_script._lead_url(init, t).rsplit("/", 1)[1] for t in (times[0], times[-1]))
    c = client([("GET", "ascii?pm25_rh35", FakeResponse(200, fixture_text("geoscf_fcst_20260930_09z.ascii"))),
                ("HEAD", first, FakeResponse(200, headers={"Last-Modified": heads["first"]})),
                ("HEAD", last, FakeResponse(200, headers={"Last-Modified": heads["last"]}))])
    frame, quality = geoscf.fetch_fcst(c, init)
    assert len(frame) == 1080 and quality["pm25_missing"] == 0
    assert frame.lead_hours.min() == 0 and frame.lead_hours.max() == 119
    assert (frame.available_at_utc == geoscf._http_time(heads["last"])).all()
    reference = pd.read_parquet(pathlib.Path(__file__).resolve().parents[1]
                                / "dataset_processed/geos-cf/geoscf_fcst_cell_hourly.parquet",
                                filters=[("init_utc", "==", init)])
    np.testing.assert_array_equal(frame.pm25_ugm3.to_numpy(), reference.pm25_ugm3.to_numpy())


def test_geoscf_fcst_inits_terbaru_dulu():
    assert geoscf.fcst_inits(dt.datetime(2026, 10, 27, 5, tzinfo=UTC)) == [
        pd.Timestamp("2026-10-26 09:00", tz="UTC"), pd.Timestamp("2026-10-25 09:00", tz="UTC")]


# STAC (Landsat, Sentinel-5P) ---------------------------------------------------
def test_stac_respons_valid_landsat_dan_filter_lc08_lc09():
    page = fixture_json("stac_landsat.json")
    page["features"].append(dict(page["features"][0], id="LE07_L2SP_122064_20260925_02_T1"))
    c = client([("POST", "/search", FakeResponse(200, json.dumps(page)))])
    items = landsat.search(c, dt.datetime(2026, 10, 10, tzinfo=UTC))
    assert items and all(i["id"].startswith(("LC08_", "LC09_")) for i in items)
    assert [i["properties"]["datetime"] for i in items] == sorted(i["properties"]["datetime"] for i in items)
    body = c.session.calls[0][2]["json"]
    assert body["collections"] == ["landsat-c2-l2"] and body["query"] == landsat.QUERY


def test_stac_respons_kosong():
    c = client([("POST", "/search", FakeResponse(200, json.dumps({"features": [], "links": []})))])
    assert sentinel5p.search(c, dt.datetime(2026, 10, 1, tzinfo=UTC)) == []


def test_stac_halaman_berikutnya_diikuti():
    page = fixture_json("stac_sentinel5p.json")
    first = dict(page, features=page["features"][:1],
                 links=[{"rel": "next", "href": "https://stac.test/search?page=2", "method": "GET"}])
    second = dict(page, features=page["features"][1:2], links=[])
    c = client([("POST", "/search", FakeResponse(200, json.dumps(first))),
                ("GET", "page=2", FakeResponse(200, json.dumps(second)))])
    assert len(sentinel5p.search(c, dt.datetime(2026, 10, 1, tzinfo=UTC))) == 2


@pytest.mark.parametrize("body", [b"<html>pemeliharaan</html>", json.dumps({"type": "FeatureCollection"}).encode(),
                                  json.dumps({"features": [{"id": "x", "properties": {}}]}).encode()])
def test_stac_format_berubah(body):
    c = client([("POST", "/search", FakeResponse(200, body))])
    with pytest.raises(FormatError):
        sentinel5p.search(c, dt.datetime(2026, 10, 1, tzinfo=UTC))


def test_token_sas_tanpa_kunci_token():
    c = client([("GET", "/sas/", FakeResponse(200, json.dumps({"msft:expiry": "x"})))])
    with pytest.raises(FormatError):
        stac.sas_token(c, "landsat-c2-l2")


# Sentinel-5P: granule sintetis -------------------------------------------------
def _s5p_granule(lat, lon, qa, no2):
    import h5py

    buffer = io.BytesIO()
    with h5py.File(buffer, "w") as f:
        f.attrs["date_created"] = "2026-09-30T07:47:38Z"
        f.attrs["time_coverage_start"] = "2026-09-30T07:04:07Z"
        f.attrs["time_coverage_end"] = "2026-09-30T07:09:07Z"
        p = f.create_group("PRODUCT")
        shape = (1, *lat.shape)
        p["latitude"], p["longitude"] = lat.reshape(shape), lon.reshape(shape)
        p["qa_value"] = qa.reshape(shape).astype("uint8")
        p["nitrogendioxide_tropospheric_column"] = no2.reshape(shape).astype("float32")
        p["nitrogendioxide_tropospheric_column_precision"] = np.full(shape, 1e-6, "float32")
        p["time_utc"] = np.array([[f"2026-09-30T07:04:{10 + i:02d}.000000Z".encode() for i in range(lat.shape[0])]])
    buffer.seek(0)
    return h5py.File(buffer, "r")


def test_sentinel5p_qa_dan_median_per_sel():
    lat = np.array([[-6.20, -6.201], [-6.202, -7.5]])            # satu piksel di luar bbox
    lon = np.array([[106.80, 106.801], [106.802, 106.8]])
    qa = np.array([[100, 50], [80, 100]])                         # 50 < 0,75 gagal QA
    no2 = np.array([[1e-4, 9e-4], [3e-4, 5e-4]])
    pixels, record = sentinel5p.read_granule(_s5p_granule(lat, lon, qa, no2), "S5P_UJI")
    assert len(pixels) == 3 and record["valid_pixels"] == 2
    obs = sentinel5p.observations(pixels)
    assert len(obs) == 1 and obs.n_valid_pixels.iloc[0] == 2 and obs.available.iloc[0]
    assert obs.no2_mol_m2.iloc[0] == pytest.approx(np.median([1e-4, 3e-4]), rel=1e-6)


def test_sentinel5p_lintasan_di_luar_bbox_kosong():
    lat = np.full((2, 2), -8.0)
    pixels, record = sentinel5p.read_granule(_s5p_granule(lat, np.full((2, 2), 110.0), np.full((2, 2), 100),
                                                          np.full((2, 2), 1e-4)), "S5P_JAUH")
    assert pixels.empty and record["source_pixels"] == 0 and sentinel5p.observations(pixels).empty


def test_sentinel5p_nilai_isian_menjadi_kosong():
    lat, lon = np.array([[-6.2]]), np.array([[106.8]])
    pixels, _ = sentinel5p.read_granule(_s5p_granule(lat, lon, np.array([[100]]), np.array([[9.96921e36]])), "S")
    obs = sentinel5p.observations(pixels)
    assert not obs.available.iloc[0] and np.isnan(obs.no2_mol_m2.iloc[0])


def test_sentinel5p_format_berubah():
    import h5py

    buffer = io.BytesIO()
    with h5py.File(buffer, "w") as f:
        f.create_group("PRODUCT")["lat"] = np.zeros((1, 1, 1))
    buffer.seek(0)
    with pytest.raises(FormatError, match="tidak memuat"):
        sentinel5p.read_granule(h5py.File(buffer, "r"), "S5P_RUSAK")


# VIIRS -------------------------------------------------------------------------
def test_viirs_cmr_valid_hanya_tile_h28v09():
    payload = fixture_json("cmr_viirs.json")
    c = client([("GET", "cmr.earthdata", FakeResponse(200, json.dumps(payload)))])
    granules = viirs.search(c, dt.datetime(2026, 9, 21, tzinfo=UTC), days=3)
    assert granules and all(".h28v09." in g["id"] for g in granules)
    assert all(g["href"].startswith(viirs.LAADS_PREFIX) for g in granules)
    assert granules == sorted(granules, key=lambda g: (g["observation_date"], g["produced_at"]))


def test_viirs_cmr_kosong_dan_format_berubah():
    c = client([("GET", "cmr.earthdata", FakeResponse(200, json.dumps({"feed": {"entry": []}})))])
    assert viirs.search(c, dt.datetime(2026, 9, 21, tzinfo=UTC)) == []
    c = client([("GET", "cmr.earthdata", FakeResponse(200, json.dumps({"items": []})))])
    with pytest.raises(FormatError):
        viirs.search(c, dt.datetime(2026, 9, 21, tzinfo=UTC))


def test_viirs_token_ditolak_menjadi_source_error():
    c = client([("GET", "laads", FakeResponse(401))])
    with pytest.raises(SourceError, match="HTTP 401"):
        c.download("https://data.laadsdaac.earthdatacloud.nasa.gov/x.h5", 1 << 20)


def _viirs_granule(shift_lat=0.0):
    import h5py

    rows, cols = np.arange(2400), np.arange(2400)
    buffer = io.BytesIO()
    with h5py.File(buffer, "w") as f:
        g = f.create_group(viirs.GROUP)
        g["lat"] = 0 - rows * viirs.CELL_DEG + shift_lat           # tile h28v09: tepi utara 0°, barat 100° BT
        g["lon"] = 100 + cols * viirs.CELL_DEG
        g["DNB_BRDF-Corrected_NTL"] = np.full((2400, 2400), 12.5, "float32")
        g["Gap_Filled_DNB_BRDF-Corrected_NTL"] = np.full((2400, 2400), 11.0, "float32")
        g["Mandatory_Quality_Flag"] = np.zeros((2400, 2400), "uint8")
        cloud = np.full((2400, 2400), (3 << 4), "uint16")          # malam, mutu tinggi, pasti cerah
        cloud[1441, 1585] |= 1 << 9                                # sirus pada sel pertama
        g["QF_Cloud_Mask"] = cloud
        g["Snow_Flag"] = np.zeros((2400, 2400), "uint8")
        g["Latest_High_Quality_Retrieval"] = np.full((2400, 2400), 255, "uint8")
        g["DNB_Lunar_Irradiance"] = np.full((2400, 2400), 65535, "uint16")
    buffer.seek(0)
    return h5py.File(buffer, "r")


def test_viirs_granule_qa_dan_sel_data_latih():
    layers, record = viirs.read_granule(_viirs_granule(), "VNP46A2.A2026263.h28v09.002.2026273184043.h5")
    assert record["cells"] == 9216 and record["valid_cells"] == 9215
    assert np.isnan(layers["ntl"][0]) and layers["ntl"][1] == np.float32(12.5)
    assert np.isnan(layers["lunar_irradiance"]).all() and np.isnan(layers["latest_high_quality_age_days"]).all()
    frame = viirs.daily_frame(layers, record)
    training = pd.read_parquet(pathlib.Path(__file__).resolve().parents[1] / "dataset_processed/viirs/daily.parquet",
                               columns=["viirs_cell_id"], filters=[("observation_date_utc", "==",
                                                                    pd.Timestamp("2026-09-20", tz="UTC"))])
    assert set(frame.viirs_cell_id) == set(training.viirs_cell_id)
    assert record["produced_at"] == pd.Timestamp("2026-09-30T18:40:43Z")


def test_viirs_koordinat_berubah_ditolak():
    with pytest.raises(FormatError, match="koordinat"):
        viirs.read_granule(_viirs_granule(shift_lat=0.5), "VNP46A2.A2026263.h28v09.002.2026273184043.h5")


def test_viirs_nama_granule_tidak_dikenal():
    with pytest.raises(FormatError):
        viirs.parse_name("VNP46A1.A2026263.h28v09.002.x.h5")


# Landsat ----------------------------------------------------------------------
def test_landsat_ambang_fraksi_valid():
    arrays = {f: np.full((445, 445), 0.3) for f in landsat.FEATURES}
    arrays.update({f + "_fraction": np.full((445, 445), 0.6) for f in landsat.FEATURES})
    arrays["ndbi_fraction"][0, 0] = 0.49
    layers, valid = landsat.cell_layers(arrays, np.array([0, 1]), np.array([0, 0]))
    assert np.isnan(layers["ndbi"][0]) and layers["ndbi"][1] == np.float32(0.3)
    assert valid == {"ndvi": 2, "ndbi": 1, "lst_c": 2}
    assert layers["ndbi_valid_fraction"][0] == np.float32(0.49)


def test_landsat_item_tanpa_aset_mtl_format_berubah():
    item = fixture_json("stac_landsat.json")["features"][0]
    item["assets"].pop("mtl.json")
    with pytest.raises(FormatError, match="mtl.json"):
        landsat.produced_at(client([]), item, "token")
