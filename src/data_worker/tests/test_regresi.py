"""Uji regresi untuk cacat yang ditemukan pada telaah kode.

Setiap uji di sini pernah gagal. Kalau ada yang kembali merah, cacat yang
sama sudah kembali.
"""

import datetime as dt
import json
import pathlib
import sys

import psycopg
import pytest

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent))
sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))

from test_collect import FakeFetcher, _config, _patch  # noqa: E402

from spku import collect as collect_mod  # noqa: E402
from spku.extract import extract_json  # noqa: E402
from spku.normalize import (  # noqa: E402
    UTC,
    QC,
    IspuPoint,
    MeteoPoint,
    Observation,
    parse_detail,
    parse_detail_time,
)
from spku.store import Store, iso  # noqa: E402

FIX = pathlib.Path(__file__).resolve().parent / "fixtures"


def load(slug):
    html = (FIX / f"detail_{slug}.html").read_text(encoding="utf-8")
    return parse_detail(extract_json(html, "SPKU_DETAIL_DATA"),
                        today_utc=dt.date(2026, 9, 13))


# 1 -------------------------------------------------------------------------
def test_muatan_terdegradasi_tidak_menghapus_koordinat(db):
    """Halaman 200 dengan metadata kosong tidak boleh menimpa metadata baik."""
    parsed = load("referensi_lengkap")
    with db.store() as store:
        store.upsert_station(parsed, initial="DKI01")
        kosong = load("referensi_lengkap")
        kosong.station.lat = None
        kosong.station.lng = None
        kosong.station.name = None
        kosong.station.type = None
        store.upsert_station(kosong, initial=None)
        row = store.conn.execute(
            "SELECT lat, lng, name, type, initial FROM station"
        ).fetchone()
    assert (row["lat"], row["lng"]) == (-6.195459, 106.822731)
    assert row["name"] == "Bundaran HI"
    assert row["type"] == "Reference"
    assert row["initial"] == "DKI01"


# 2 -------------------------------------------------------------------------
def test_galat_di_tengah_penulisan_mengembalikan_seluruhnya(monkeypatch, db):
    """Lintasan yang gagal tidak boleh menyisakan data separuh jadi."""
    _patch(monkeypatch, "ok")
    config = _config(db)

    asli = Store.upsert_ispu
    hitung = {"n": 0}

    def meledak(self, rows):
        hitung["n"] += 1
        if hitung["n"] == 2:
            raise psycopg.OperationalError("koneksi ke basis data terputus")
        return asli(self, rows)

    monkeypatch.setattr(Store, "upsert_ispu", meledak)
    summary = collect_mod.run_full(config)

    with db.store() as store:
        observasi = store.conn.execute("SELECT COUNT(*) AS n FROM observation").fetchone()["n"]
        stasiun = store.conn.execute("SELECT COUNT(*) AS n FROM station").fetchone()["n"]
        peristiwa = store.conn.execute("SELECT COUNT(*) AS n FROM roster_event").fetchone()["n"]
        menggantung = store.conn.execute(
            "SELECT COUNT(*) AS n FROM run WHERE status='running'"
        ).fetchone()["n"]
    assert summary["status"] == "failed"
    assert (observasi, stasiun, peristiwa) == (0, 0, 0), "penulisan harus dikembalikan seluruhnya"
    assert menggantung == 0, "lintasan tidak boleh berhenti pada status running"


# 3 -------------------------------------------------------------------------
def test_nol_palsu_juga_ditangani_pada_kanal_parameter():
    """AT dan RH mengalir lewat rawHistory pada tiga stasiun LCS."""
    from spku.normalize import clean_concentration

    assert clean_concentration("AT", 0) == (None, QC.ZERO_PLACEHOLDER)
    assert clean_concentration("RH", 0) == (None, QC.ZERO_PLACEHOLDER)
    assert clean_concentration("AIR_HUMID", 0) == (None, QC.ZERO_PLACEHOLDER)
    # Nol pada pencemar adalah pembacaan sah, bukan penanda kosong.
    assert clean_concentration("PM25", 0) == (0.0, QC.OK)


# 4 -------------------------------------------------------------------------
@pytest.mark.parametrize(
    "batas",
    ["2026-09-13T00:00:00Z", "2026-09-13T07:00:00+07:00", "2026-09-13", "2026-09-13T00:00:00"],
)
def test_batas_waktu_ekspor_setara_lintas_penulisan(tmp_path, capsys, batas):
    from spku.cli import _parse_bound

    hasil = _parse_bound(batas, "--since")
    assert hasil == "2026-09-13T00:00:00+00:00"


def test_batas_waktu_tak_terbaca_menghentikan_program():
    from spku.cli import _parse_bound

    with pytest.raises(SystemExit):
        _parse_bound("kemarin", "--since")


# 5 -------------------------------------------------------------------------
def test_nilai_kosong_dari_portal_tidak_menghapus_nilai_baik(db):
    moment = dt.datetime(2026, 9, 13, 6, 0, tzinfo=UTC)
    with db.store() as store:
        store.upsert_observations([Observation("s1", "PM25", moment, 55.7)])
        new, revised = store.upsert_observations([Observation("s1", "PM25", moment, None)])
        nilai = store.conn.execute("SELECT value FROM observation").fetchone()["value"]
        revisi = store.conn.execute("SELECT COUNT(*) AS n FROM observation_revision").fetchone()["n"]
    assert nilai == 55.7
    assert (new, revised, revisi) == (0, 0, 0)


def test_nilai_kosong_yang_kemudian_terisi_tetap_dicatat(db):
    moment = dt.datetime(2026, 9, 13, 6, 0, tzinfo=UTC)
    with db.store() as store:
        store.upsert_observations([Observation("s1", "PM25", moment, None)])
        store.upsert_observations([Observation("s1", "PM25", moment, 55.7)])
        nilai = store.conn.execute("SELECT value FROM observation").fetchone()["value"]
    assert nilai == 55.7


# 6 -------------------------------------------------------------------------
def test_bendera_macet_menyusul_tetap_tersimpan(db):
    """Deret macet baru dikenali setelah cukup panjang pada lintasan berikutnya."""
    base = dt.datetime(2026, 9, 13, 0, 0, tzinfo=UTC)
    pendek = [
        Observation("s1", "SO2", base + dt.timedelta(minutes=30 * i), 41.0)
        for i in range(6)
    ]
    panjang = [
        Observation("s1", "SO2", base + dt.timedelta(minutes=30 * i), 41.0, QC.STUCK)
        for i in range(18)
    ]
    with db.store() as store:
        store.upsert_observations(pendek)
        store.upsert_observations(panjang)
        tanpa_bendera = store.conn.execute(
            "SELECT COUNT(*) AS n FROM observation WHERE qc=''"
        ).fetchone()["n"]
    assert tanpa_bendera == 0


# 7 -------------------------------------------------------------------------
def test_peristiwa_daftar_tidak_berulang_dan_kemunculan_kembali_tercatat(db):
    parsed = load("referensi_lengkap")
    uuid = parsed.station.uuid
    with db.store() as store:
        store.upsert_station(parsed, initial="DKI01")
        store.record_roster({uuid: "DKI01 Bundaran HI"})      # sudah ada
        store.record_roster({})                                # hilang
        store.record_roster({})                                # tetap hilang
        store.record_roster({})                                # tetap hilang
        store.record_roster({uuid: "DKI01 Bundaran HI"})       # menyala lagi
        rows = store.conn.execute(
            "SELECT event, COUNT(*) AS n FROM roster_event GROUP BY event"
        ).fetchall()
    assert dict((r["event"], r["n"]) for r in rows) == {"disappeared": 1, "appeared": 1}


# 8 -------------------------------------------------------------------------
def test_penyaringan_satu_stasiun_tetap_berjalan_setelah_ada_riwayat(monkeypatch, db):
    _patch(monkeypatch, "ok")
    config = _config(db)
    collect_mod.run_full(config)
    summary = collect_mod.run_full(config, only=["LCS01"])
    assert summary["status"] == "ok"
    assert summary["stations_seen"] == 1


# 9 -------------------------------------------------------------------------
def test_beranda_rusak_meninggalkan_jejak(monkeypatch, db):
    """Pendeteksi gangguan tidak boleh gagal tanpa meninggalkan bukti."""
    config = _config(db)
    fake = FakeFetcher("ok")
    fake._body = lambda path: '<script>window.__SPKU_DATA__ = [{"id":"a"'  # terpotong
    monkeypatch.setattr(collect_mod, "Fetcher", lambda *a, **k: fake)

    summary = collect_mod.run_snapshot(config)
    with db.store() as store:
        jejak = store.conn.execute("SELECT COUNT(*) AS n FROM fetch_log").fetchone()["n"]
        menggantung = store.conn.execute(
            "SELECT COUNT(*) AS n FROM run WHERE status='running'"
        ).fetchone()["n"]
    assert summary["status"] == "failed"
    assert jejak == 1
    assert menggantung == 0


# 10 ------------------------------------------------------------------------
def test_kegagalan_jaringan_tidak_tercatat_sebagai_gangguan_portal(monkeypatch, db):
    config = _config(db)
    fake = FakeFetcher("ok")
    fake._body = lambda path: None          # peladen tidak terjangkau
    monkeypatch.setattr(collect_mod, "Fetcher", lambda *a, **k: fake)

    summary = collect_mod.run_snapshot(config)
    with db.store() as store:
        cuplikan = store.conn.execute("SELECT COUNT(*) AS n FROM snapshot").fetchone()["n"]
    assert summary["status"] == "failed"
    assert cuplikan == 0, "tabel cuplikan adalah bukti keadaan portal, bukan keadaan jaringan"


# 11 ------------------------------------------------------------------------
@pytest.mark.parametrize("teks", ["rusakZ", "Z", "2026-09-13T25:00:00Z", "", "  "])
def test_cap_waktu_rusak_menghasilkan_kosong_bukan_galat(teks):
    assert parse_detail_time(teks) is None


def test_offset_yang_sudah_ada_dihormati():
    assert parse_detail_time("2026-09-13T13:00:00+07:00") == dt.datetime(
        2026, 9, 13, 6, 0, tzinfo=UTC
    )


def test_satu_cap_waktu_rusak_tidak_menggagalkan_seluruh_stasiun():
    html = (FIX / "detail_lcs_per_jam.html").read_text(encoding="utf-8")
    payload = extract_json(html, "SPKU_DETAIL_DATA")
    payload["rawHistory"][0]["time"] = "bukan waktu"
    parsed = parse_detail(payload, today_utc=dt.date(2026, 9, 13))
    assert len(parsed.observations) == 47      # satu titik dilewati, sisanya utuh


# 12 ------------------------------------------------------------------------
def test_perubahan_nilai_indeks_juga_meninggalkan_jejak(db):
    moment = dt.datetime(2026, 9, 13, 6, 0, tzinfo=UTC)
    with db.store() as store:
        store.upsert_ispu([IspuPoint("s1", "PM25", moment, 50)])
        store.upsert_ispu([IspuPoint("s1", "PM25", moment, 99)])
        row = store.conn.execute(
            "SELECT series, old_value, new_value FROM observation_revision"
        ).fetchone()
        nilai = store.conn.execute("SELECT ispu FROM observation_ispu").fetchone()["ispu"]
    assert row["series"] == "observation_ispu"
    assert (row["old_value"], row["new_value"]) == (50, 99)
    assert nilai == 99


# 13 ------------------------------------------------------------------------
def test_bendera_meteo_menerangkan_isi_baris_setelah_penggabungan(db):
    moment = dt.datetime(2026, 9, 13, 6, 0, tzinfo=UTC)
    baik = MeteoPoint("s1", moment, 28.4, 71.2, 1.3)
    nol = MeteoPoint("s1", moment, None, None, 1.3,
                     QC.ZERO_PLACEHOLDER, QC.ZERO_PLACEHOLDER, QC.OK)
    with db.store() as store:
        store.upsert_meteo([baik])
        store.upsert_meteo([nol])
        row = store.conn.execute("SELECT temp_c, rh_pct, qc FROM meteo").fetchone()
    assert (row["temp_c"], row["rh_pct"]) == (28.4, 71.2)
    assert row["qc"] == "", "bendera tidak boleh menerangkan lapangan yang sudah terisi"


# 14 ------------------------------------------------------------------------
def test_datetime_tanpa_zona_ditolak_bukan_ditebak():
    with pytest.raises(ValueError):
        iso(dt.datetime(2026, 9, 13, 6, 0))


# 15 ------------------------------------------------------------------------
def test_konfigurasi_terbaca_tanpa_tomllib(tmp_path, monkeypatch):
    """macOS kerap masih membawa Python 3.10 yang belum punya tomllib."""
    from spku import config as config_mod

    berkas = tmp_path / "c.toml"
    berkas.write_text(
        '[collector]\n'
        'database = "postgresql://u@h/db"\n'
        'contact = "a@b.ac.id"   # komentar diabaikan\n'
        'delay_seconds = 1.5\n'
        'retries = 3\n'
        'min_roster_ratio = 0.5\n',
        encoding="utf-8",
    )
    monkeypatch.setattr(config_mod, "tomllib", None)
    cfg = config_mod.Config.load(berkas)
    assert cfg.contact == "a@b.ac.id"
    assert cfg.delay_seconds == 1.5
    assert cfg.retries == 3
    assert cfg.min_roster_ratio == 0.5
    assert cfg.timeout_seconds == 45      # nilai bawaan tetap berlaku


# 16 ------------------------------------------------------------------------
def test_label_rinci_adalah_wib_selaras_dengan_last_update_bertanda_z():
    """Regresi 28 September 2026. Pada DKI02 pukul 09.53 UTC, lastUpdate
    bernilai "2026-09-28T09:00:00Z" sementara label rawHistory terbaru
    "2026-09-28T16:30:00". Kolektor lama menafsirkan label sebagai UTC dan
    menyimpan seluruh observasi tujuh jam terlalu maju."""
    payload = {
        "datasourceID": "s1", "lastUpdate": "2026-09-28T09:00:00Z",
        "rawHistory": [
            {"metricName": "PM25", "time": "2026-09-28T16:00:00", "value": 33.1},
            {"metricName": "PM25", "time": "2026-09-28T16:30:00", "value": 30.75},
        ],
    }
    parsed = parse_detail(payload, today_utc=dt.date(2026, 9, 28))
    terbaru = parsed.newest_raw_ts
    assert terbaru == dt.datetime(2026, 9, 28, 9, 30, tzinfo=UTC)
    assert abs(terbaru - parse_detail_time(payload["lastUpdate"])) <= dt.timedelta(hours=1)
