"""Uji kebasian dan kelengkapan pada tingkat parameter.

Dipicu oleh audit 15 September 2026: DKI02 mengirim lima parameter dengan
normal sementara kanal PM10-nya sudah 13,6 jam diam. Dinilai dari stasiunnya,
stasiun itu tampak sehat sempurna.
"""

import datetime as dt
import pathlib
import sys

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent))

from spku.monitor import format_health, health  # noqa: E402
from spku.normalize import UTC, Observation  # noqa: E402
from spku.store import now_utc  # noqa: E402


def _isi(store, *, segar_metrik=("PM25", "SO2"), basi_metrik=("PM10",), basi_jam=14):
    """Satu stasiun: sebagian kanal mutakhir, sebagian sudah lama diam."""
    sekarang = now_utc().replace(second=0, microsecond=0)
    store.conn.execute(
        """INSERT INTO station (uuid, initial, name, first_seen_utc, last_seen_utc, active)
           VALUES ('s1','DKI02','Kelapa Gading',%s,%s,TRUE)""",
        (sekarang.isoformat(), sekarang.isoformat()),
    )
    rows = []
    for metric in segar_metrik:
        for i in range(96):
            rows.append(Observation("s1", metric, sekarang - dt.timedelta(minutes=30 * i), 40 + i))
    for metric in basi_metrik:
        for i in range(7):
            rows.append(
                Observation("s1", metric,
                            sekarang - dt.timedelta(hours=basi_jam) - dt.timedelta(minutes=300 * i),
                            0.0)
            )
    store.upsert_observations(rows)
    return sekarang


def test_kanal_basi_terdeteksi_walau_stasiunnya_segar(db):
    with db.store() as store:
        _isi(store)
        basi_stasiun = store.stale_stations(6.0)
        basi_kanal = store.stale_metrics(6.0)
    assert basi_stasiun == [], "stasiunnya memang masih mengirim, jadi tidak boleh ditandai"
    assert [(r["initial"], r["metric"]) for r in basi_kanal] == [("DKI02", "PM10")]


def test_kanal_yang_masih_mutakhir_tidak_ikut_tertandai(db):
    with db.store() as store:
        _isi(store)
        basi = {r["metric"] for r in store.stale_metrics(6.0)}
    assert "PM25" not in basi and "SO2" not in basi


def test_laporan_kesehatan_menyebut_kanal_basi(db):
    with db.store() as store:
        _isi(store)
        laporan = health(store, stale_hours=6.0)
        teks = format_health(laporan)
    assert len(laporan["parameter_basi"]) == 1
    assert any("kanal parameter diam" in p for p in laporan["peringatan"])
    assert "DKI02/PM10" in teks


def test_lubang_di_dalam_jendela_terdeteksi(db):
    """Deret 30 menit dengan satu lubang sepuluh jam."""
    sekarang = now_utc().replace(second=0, microsecond=0)
    with db.store() as store:
        store.conn.execute(
            """INSERT INTO station (uuid, initial, name, first_seen_utc, last_seen_utc, active)
               VALUES ('s2','DKI42','Contoh',%s,%s,TRUE)""",
            (sekarang.isoformat(), sekarang.isoformat()),
        )
        rows = []
        for i in range(24):                       # 12 jam pertama, rapat
            rows.append(Observation("s2", "PM25", sekarang - dt.timedelta(minutes=30 * i), 30.0 + i))
        for i in range(24):                       # lalu melompat sepuluh jam
            rows.append(Observation("s2", "PM25",
                                    sekarang - dt.timedelta(hours=22) - dt.timedelta(minutes=30 * i),
                                    50.0 + i))
        store.upsert_observations(rows)
        lubang = store.metric_gaps()
    assert len(lubang) == 1
    assert lubang[0]["metric"] == "PM25"
    assert lubang[0]["irama_menit"] == 30
    assert lubang[0]["lubang_terbesar_menit"] >= 600


def test_deret_rapat_tidak_dilaporkan_berlubang(db):
    sekarang = now_utc().replace(second=0, microsecond=0)
    with db.store() as store:
        store.conn.execute(
            """INSERT INTO station (uuid, initial, name, first_seen_utc, last_seen_utc, active)
               VALUES ('s3','DKI01','Bundaran HI',%s,%s,TRUE)""",
            (sekarang.isoformat(), sekarang.isoformat()),
        )
        store.upsert_observations([
            Observation("s3", "PM25", sekarang - dt.timedelta(minutes=30 * i), 40.0 + i)
            for i in range(96)
        ])
        assert store.metric_gaps() == []
