import datetime as dt
import pathlib
import sys

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent))

from spku.extract import extract_json  # noqa: E402
from spku.normalize import (  # noqa: E402
    UTC,
    QC,
    parse_detail,
    parse_detail_time,
    parse_home,
    parse_home_time,
)

FIX = pathlib.Path(__file__).resolve().parent / "fixtures"


def load(slug):
    html = (FIX / f"detail_{slug}.html").read_text(encoding="utf-8")
    return parse_detail(extract_json(html, "SPKU_DETAIL_DATA"),
                        today_utc=dt.date(2026, 9, 13))


# --------------------------------------------------------------- zona waktu

def test_cap_waktu_beranda_ditafsirkan_sebagai_wib():
    moment = parse_home_time("2026-09-13T13:30:00")
    assert moment == dt.datetime(2026, 9, 13, 6, 30, tzinfo=UTC)


def test_cap_waktu_rinci_ditafsirkan_sebagai_wib():
    """Dikoreksi 28 September 2026: halaman rinci memakai WIB, bukan UTC."""
    moment = parse_detail_time("2026-09-13T06:30:00")
    assert moment == dt.datetime(2026, 9, 12, 23, 30, tzinfo=UTC)


def test_penanda_z_dihormati_apa_adanya():
    assert parse_detail_time("2026-09-13T06:00:00Z") == dt.datetime(
        2026, 9, 13, 6, 0, tzinfo=UTC
    )


def test_label_sama_pada_kedua_halaman_menunjuk_saat_yang_sama():
    """Nilai beranda pukul 16.00 WIB muncul pada label 16.00 di halaman rinci."""
    beranda = parse_home_time("2026-09-13T13:30:00")
    rinci = parse_detail_time("2026-09-13T13:30:00")
    assert rinci == beranda


# ------------------------------------------------- urutan riwayat per metrik

def test_menggunakan_raw_history_bukan_metrics_history():
    """Titik terbaru harus berasal dari rawHistory yang terurut."""
    parsed = load("referensi_lengkap")
    assert parsed.newest_raw_ts == dt.datetime(2026, 9, 12, 23, 30, tzinfo=UTC)


def test_metrics_history_memang_teracak_pada_fixture():
    html = (FIX / "detail_referensi_lengkap.html").read_text(encoding="utf-8")
    payload = extract_json(html, "SPKU_DETAIL_DATA")
    times = [p["time"] for p in payload["metrics"][0]["history"]]
    assert times != sorted(times), "fixture harus meniru riwayat yang tidak terurut"


def test_jumlah_observasi_sesuai_kanal_kali_titik():
    parsed = load("referensi_lengkap")
    assert len(parsed.observations) == 6 * 96
    assert sorted(set(o.metric for o in parsed.observations)) == [
        "CO", "NO2", "O3", "PM10", "PM25", "SO2"
    ]


# ------------------------------------------------------ nol sebagai kosong

def test_suhu_dan_kelembapan_nol_menjadi_kosong():
    parsed = load("sensor_meteo_nol")
    assert all(p.temp_c is None for p in parsed.meteo)
    assert all(p.rh_pct is None for p in parsed.meteo)
    assert all(QC.ZERO_PLACEHOLDER in p.qc for p in parsed.meteo)


def test_kecepatan_angin_tetap_dipertahankan():
    parsed = load("sensor_meteo_nol")
    assert all(p.wind_ms == 2.25 for p in parsed.meteo)


def test_stasiun_lcs_tidak_punya_kecepatan_angin():
    parsed = load("lcs_per_jam")
    assert all(p.wind_ms is None for p in parsed.meteo)
    assert all(p.temp_c is not None for p in parsed.meteo)


# ------------------------------------------------------------ kendali mutu

def test_sensor_macet_ditandai():
    parsed = load("sensor_meteo_nol")
    so2 = [o for o in parsed.observations if o.metric == "SO2"]
    assert so2 and all(QC.STUCK in o.qc for o in so2)


def test_parameter_lain_tidak_ikut_ditandai_macet():
    parsed = load("sensor_meteo_nol")
    pm25 = [o for o in parsed.observations if o.metric == "PM25"]
    assert not any(QC.STUCK in o.qc for o in pm25)


# ------------------------------------------------------------- irama ukur

def test_irama_stasiun_referensi_tiga_puluh_menit():
    assert load("referensi_lengkap").cadence_minutes == 30


def test_irama_stasiun_lcs_enam_puluh_menit():
    assert load("lcs_per_jam").cadence_minutes == 60


# ------------------------------------------------------- agregat harian

def test_baris_hari_berjalan_ditandai_sementara():
    parsed = load("referensi_lengkap")
    hari_ini = [d for d in parsed.daily if d.date_utc == dt.date(2026, 9, 13)]
    assert hari_ini and hari_ini[0].provisional
    lampau = [d for d in parsed.daily if d.date_utc == dt.date(2026, 9, 12)]
    assert lampau and not lampau[0].provisional


# --------------------------------------------------------------- beranda

def test_beranda_dinormalisasi_ke_utc():
    html = (FIX / "home_ok.html").read_text(encoding="utf-8")
    rows = parse_home(extract_json(html, "__SPKU_DATA__"))
    assert rows[0]["observed_ts_utc"] == dt.datetime(2026, 9, 13, 6, 30, tzinfo=UTC)
