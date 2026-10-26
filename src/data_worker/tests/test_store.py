import datetime as dt
import pathlib
import sys

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent))

from spku.extract import extract_json  # noqa: E402
from spku.normalize import UTC, Observation, parse_detail  # noqa: E402

FIX = pathlib.Path(__file__).resolve().parent / "fixtures"


def load(slug):
    html = (FIX / f"detail_{slug}.html").read_text(encoding="utf-8")
    return parse_detail(extract_json(html, "SPKU_DETAIL_DATA"),
                        today_utc=dt.date(2026, 9, 13))


def test_penulisan_ulang_bersifat_idempoten(db):
    parsed = load("referensi_lengkap")
    with db.store() as store:
        store.upsert_station(parsed, initial="DKI01")
        first_new, first_rev = store.upsert_observations(parsed.observations)
        second_new, second_rev = store.upsert_observations(parsed.observations)
        total = store.conn.execute("SELECT COUNT(*) AS n FROM observation").fetchone()["n"]
    assert first_new == 6 * 96
    assert (second_new, second_rev) == (0, 0)
    assert total == 6 * 96


def test_perubahan_nilai_dicatat_sebagai_revisi_bukan_ditimpa_diam_diam(db):
    moment = dt.datetime(2026, 9, 13, 6, 0, tzinfo=UTC)
    with db.store() as store:
        store.upsert_observations([Observation("s1", "PM25", moment, 40.0)])
        new, revised = store.upsert_observations([Observation("s1", "PM25", moment, 41.5)])
        row = store.conn.execute("SELECT * FROM observation_revision").fetchone()
        current = store.conn.execute("SELECT value FROM observation").fetchone()["value"]
    assert (new, revised) == (0, 1)
    assert (row["old_value"], row["new_value"]) == (40.0, 41.5)
    assert current == 41.5


def test_baris_harian_final_tidak_ditimpa_oleh_baris_sementara(db):
    parsed = load("referensi_lengkap")
    with db.store() as store:
        store.upsert_station(parsed, initial="DKI01")
        store.upsert_daily(parsed.daily)
        final_before = store.conn.execute(
            "SELECT concentration FROM daily_ispu WHERE date_utc='2026-09-12'"
        ).fetchone()["concentration"]

        # Pengambilan berikutnya membawa nilai berbeda untuk hari yang sudah final.
        for row in parsed.daily:
            row.concentration = -1.0
        store.upsert_daily(parsed.daily)

        final_after = store.conn.execute(
            "SELECT concentration FROM daily_ispu WHERE date_utc='2026-09-12'"
        ).fetchone()["concentration"]
        provisional_after = store.conn.execute(
            "SELECT concentration FROM daily_ispu WHERE date_utc='2026-09-13'"
        ).fetchone()["concentration"]
    assert final_after == final_before
    assert provisional_after == -1.0


def test_stasiun_yang_hilang_ditandai_tidak_aktif(db):
    parsed = load("referensi_lengkap")
    with db.store() as store:
        store.upsert_station(parsed, initial="DKI01")
        events = store.record_roster({})
        active = store.conn.execute(
            "SELECT active FROM station WHERE uuid=%s", (parsed.station.uuid,)
        ).fetchone()["active"]
    assert [e[2] for e in events] == ["disappeared"]
    assert active is False


def test_kebasian_dihitung_dari_observasi_bukan_dari_last_update(db):
    parsed = load("lcs_per_jam")   # lastUpdate pada fixture ini tertinggal dua bulan
    with db.store() as store:
        store.upsert_station(parsed, initial="LCS01")
        store.upsert_observations(parsed.observations)
        coverage = store.coverage()
    assert coverage["n"] == 48
    assert coverage["t1"] == dt.datetime(2026, 9, 12, 23, 0, tzinfo=UTC)  # label 06.00 WIB


def test_koordinat_berulang_dalam_satu_muatan_tidak_menabrak_indeks_unik(db):
    """Regresi: portal pernah menyajikan entri tumpang tindih dalam satu
    respons, membuat dua baris dengan (station_uuid, metric, ts_utc) yang
    sama muncul dalam satu pemanggilan upsert_observations. Sebelum
    diperbaiki, keduanya lolos pemeriksaan 'existing' (yang hanya
    mencerminkan keadaan basis data sebelum lintasan ini) dan sama-sama
    masuk ke 'inserts', lalu INSERT kedua menabrak UNIQUE constraint dan
    seluruh lintasan gagal."""
    moment = dt.datetime(2026, 9, 13, 6, 0, tzinfo=UTC)
    with db.store() as store:
        new, revised = store.upsert_observations([
            Observation("s1", "PM25", moment, 40.0),
            Observation("s1", "PM25", moment, 41.5),
        ])
        total = store.conn.execute("SELECT COUNT(*) AS n FROM observation").fetchone()["n"]
        current = store.conn.execute("SELECT value FROM observation").fetchone()["value"]
    assert new == 1
    assert total == 1
    assert current == 41.5  # kemunculan terakhir dalam muatan yang menang
