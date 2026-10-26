"""Uji Data Worker sesuai Tabel 5.8 Dokumen Desain (UT-DW-01 dan UT-DW-05)."""

import datetime as dt

import pytest

from acquisition import schedule_acquisition
from contracts import JAKARTA_BBOX, BBox, TimeWindow
from ground_truth import fetch_ground_truth
from test_collect import _config, _patch

UTC = dt.timezone.utc
# Label fixture 2026-09-13T05:00-06:00 WIB = 2026-09-12T22:00-23:00 UTC.
WINDOW = TimeWindow.starting_at(dt.datetime(2026, 9, 12, 22, 0, tzinfo=UTC))


# UT-DW-01 ------------------------------------------------------------------
def test_ut_dw_01a_siklus_0515_memproses_jam_0400_0500():
    window = TimeWindow.just_ended(dt.datetime(2026, 9, 28, 5, 15, tzinfo=UTC))
    assert (window.start, window.end) == (
        dt.datetime(2026, 9, 28, 4, 0, tzinfo=UTC),
        dt.datetime(2026, 9, 28, 5, 0, tzinfo=UTC),
    )


def test_ut_dw_01b_galat_satu_langkah_tidak_menghentikan_langkah_berikutnya():
    urutan = []

    def gagal(window, config):
        urutan.append("gagal")
        raise ConnectionError("sumber tidak terjangkau")

    def berhasil(window, config):
        urutan.append("berhasil")
        return {"status": "ok"}

    report = schedule_acquisition(
        WINDOW, config=object(),
        steps=[("konektor_a", gagal), ("konektor_b", berhasil), ("belum_ada", None)],
    )
    assert urutan == ["gagal", "berhasil"]
    assert [(s["step"], s["status"]) for s in report["steps"]] == [
        ("konektor_a", "failed"), ("konektor_b", "ok"), ("belum_ada", "not_implemented"),
    ]
    assert report["failed"] == ["konektor_a"]


def test_time_window_menolak_bentuk_yang_tidak_sah():
    with pytest.raises(ValueError):
        TimeWindow(dt.datetime(2026, 9, 13, 5, 30, tzinfo=UTC),
                   dt.datetime(2026, 9, 13, 6, 30, tzinfo=UTC))
    with pytest.raises(ValueError):
        TimeWindow(dt.datetime(2026, 9, 13, 5, 0), dt.datetime(2026, 9, 13, 6, 0))


# UT-DW-05 ------------------------------------------------------------------
def test_ut_dw_05c_spku_dari_fixture_halaman_publik(monkeypatch, db):
    _patch(monkeypatch, "ok")
    batch = fetch_ground_truth("spku", JAKARTA_BBOX, WINDOW, config=_config(db))

    assert batch.available
    assert batch.run["status"] == "ok"
    assert {m.pollutant for m in batch.measurements} == {"pm25", "no2"}
    assert {m.station_code for m in batch.measurements} == {"DKI1", "DKI20", "LCS01"}
    for m in batch.measurements:
        assert WINDOW.start <= m.ts_utc < WINDOW.end
        assert m.lat is not None and m.lon is not None
        assert isinstance(m.value_ugm3, float)
        assert isinstance(m.qc, str)
    assert batch.unavailable_stations == []


def test_ut_dw_05d_penyedia_tidak_dikenal():
    with pytest.raises(ValueError):
        fetch_ground_truth("airnow", JAKARTA_BBOX, WINDOW, config=object())


def test_penyedia_terdaftar_yang_belum_diimplementasikan():
    with pytest.raises(NotImplementedError):
        fetch_ground_truth("openaq", JAKARTA_BBOX, WINDOW, config=object())


def test_stasiun_di_luar_bbox_tidak_dikembalikan(monkeypatch, db):
    _patch(monkeypatch, "ok")
    jauh = BBox(min_lon=110.0, min_lat=-7.0, max_lon=110.5, max_lat=-6.5)
    batch = fetch_ground_truth("spku", jauh, WINDOW, config=_config(db))
    assert not batch.available
    assert batch.measurements == []


def test_lintasan_dibatalkan_tetap_memakai_data_jendela_48_jam(monkeypatch, db):
    """Portal kosong pada jam ini, tetapi lintasan sebelumnya sudah membawa
    time window yang sama berkat riwayat 48 jam."""
    config = _config(db)
    _patch(monkeypatch, "ok")
    fetch_ground_truth("spku", JAKARTA_BBOX, WINDOW, config=config)

    _patch(monkeypatch, "kosong")
    batch = fetch_ground_truth("spku", JAKARTA_BBOX, WINDOW, config=config)
    assert batch.run["status"] == "aborted"
    assert batch.available
    assert batch.measurements


def test_siklus_penuh_dengan_langkah_bawaan(monkeypatch, db):
    _patch(monkeypatch, "ok")
    monkeypatch.delenv("DOWNSCALE_WRITE_CACHE", raising=False)
    report = schedule_acquisition(WINDOW, config=_config(db))
    status = {s["step"]: s["status"] for s in report["steps"]}
    assert status["fetch_ground_truth"] == "ok"
    assert report["failed"] == []
    # Penulisan EdgeWeight ke cache hanya aktif bila DOWNSCALE_WRITE_CACHE=1.
    assert status["trigger_downscale_inference"] == "disabled"
    assert set(status.values()) == {"ok", "not_implemented", "disabled"}
