"""Normalisasi muatan portal menjadi baris siap simpan.

Seluruh keputusan di modul ini berasal dari inventarisasi muatan nyata pada
13 September 2026 terhadap 119 stasiun (udara-collector/docs/TEMUAN_DATA.md, repositori kolektor asal). Empat jebakan
yang ditangani secara eksplisit:

1. **Cap waktu tanpa zona pada kedua halaman adalah WIB.** ``time`` pada
   ``rawHistory``, ``ispuHistory``, dan ``rawMeteoHistory`` di halaman rinci,
   seperti ``dominantMetricTime`` di beranda, memakai WIB tanpa penanda zona.
   Inventarisasi 13 September 2026 menyimpulkan halaman rinci memakai UTC;
   kesimpulan itu keliru dan dikoreksi pada 28 September 2026. Buktinya:
   27.932 dari 32.642 nilai beranda (WIB) pada 13-27 September cocok persis
   dengan observasi tersimpan pada label = waktu sebenarnya + 7 jam, dan hanya
   10 cocok pada waktu sebenarnya; ``lastUpdate`` bertanda ``Z`` juga berselisih
   tujuh jam dari label terbaru. Kekeliruan itu tersamarkan karena sampai
   27 September portal memotong jendela 48 jam pada "sekarang" dalam UTC,
   sehingga tujuh jam terbaru tersembunyi dan data tampak segar. Cap waktu
   yang membawa ``Z`` (``dailyIspu30Days.date``, ``lastUpdate``) dihormati apa
   adanya.
2. **``metrics[].history`` tidak terurut.** Isinya identik dengan
   ``rawHistory`` tetapi urutannya teracak, sehingga ``history[-1]``
   mengembalikan titik yang keliru. ``rawHistory`` terurut menaik.
3. **Nol sebagai penanda kosong.** Sebagian stasiun mengembalikan
   ``temperature = 0`` dan ``humidity = 0`` untuk seluruh 96 titik. Nol derajat
   celsius dan kelembapan nol persen mustahil di Jakarta. Sebaliknya
   ``windSpeed = 0`` sah (udara tenang) dan tidak boleh dibuang.
4. **``lastUpdate`` tidak dapat dipercaya.** Pada sebagian stasiun nilainya
   tertinggal dua bulan dari titik terbaru pada ``rawHistory``, dan formatnya
   tidak konsisten (kadang berakhiran ``Z``, kadang tidak).
"""

from __future__ import annotations

import datetime as dt
from dataclasses import dataclass, field
from typing import Any, Iterable

WIB = dt.timezone(dt.timedelta(hours=7))
UTC = dt.timezone.utc

#: Parameter pencemar yang masuk perhitungan ISPU (Permen LHK 14/2020).
POLLUTANTS = ("PM25", "PM10", "SO2", "NO2", "CO", "O3")

#: Parameter meteorologi yang muncul sebagai kanal "metrics" pada tiga stasiun LCS.
METEO_METRICS = ("AT", "RH", "AIR_HUMID")

#: Satuan yang DIASUMSIKAN. Portal tidak menyatakan satuan di mana pun; nilai CO
#: berkisar 500-3100 sehingga konsisten dengan mikrogram per meter kubik, bukan
#: ppm. Konfirmasi satuan termasuk butir yang diminta melalui PPID.
ASSUMED_UNITS = {
    "PM25": "ug/m3",
    "PM10": "ug/m3",
    "SO2": "ug/m3",
    "NO2": "ug/m3",
    "CO": "ug/m3",
    "O3": "ug/m3",
    "AT": "degC",
    "RH": "percent",
    "AIR_HUMID": "percent",
}

#: Rentang fisis yang wajar. Nilai di luar rentang tidak dibuang, hanya ditandai.
PLAUSIBLE_RANGE = {
    "PM25": (0.0, 2000.0),
    "PM10": (0.0, 5000.0),
    "SO2": (0.0, 3000.0),
    "NO2": (0.0, 3000.0),
    "CO": (0.0, 60000.0),
    "O3": (0.0, 2000.0),
}

#: Panjang deret identik berturut-turut yang dianggap sensor macet.
STUCK_RUN_LENGTH = 12


class QC:
    """Bendera kendali mutu. Disimpan, bukan dipakai untuk membuang baris."""

    OK = ""
    ZERO_PLACEHOLDER = "Z"   # nol dipakai sebagai penanda kosong
    NEGATIVE = "N"           # konsentrasi negatif
    OUT_OF_RANGE = "R"       # di luar rentang fisis
    STUCK = "S"              # nilai identik terlalu lama


def parse_naive(value: str | None, assume: dt.tzinfo) -> dt.datetime | None:
    """Urai cap waktu tanpa zona dan lekatkan zona ``assume``.

    Cap waktu yang sudah membawa ``Z`` atau offset dihormati apa adanya.
    """
    if not value:
        return None
    text = value.strip()
    if text.endswith("Z"):
        text = text[:-1] + "+00:00"
    try:
        parsed = dt.datetime.fromisoformat(text)
    except (ValueError, TypeError):
        # Satu cap waktu rusak pada satu dari 119 halaman tidak boleh
        # menggagalkan seluruh lintasan.
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=assume)
    return parsed.astimezone(UTC)


def parse_detail_time(value: str | None) -> dt.datetime | None:
    """Cap waktu halaman rinci: WIB tanpa penanda zona (lihat jebakan 1)."""
    return parse_naive(value, WIB)


def parse_home_time(value: str | None) -> dt.datetime | None:
    """Cap waktu halaman beranda: WIB tanpa penanda zona."""
    return parse_naive(value, WIB)


def clean_meteo_value(name: str, value: Any) -> tuple[float | None, str]:
    """Terapkan aturan nol-sebagai-kosong khusus suhu dan kelembapan."""
    if value is None:
        return None, QC.OK
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None, QC.OK
    if name in ("temperature", "humidity") and number == 0.0:
        return None, QC.ZERO_PLACEHOLDER
    if name == "humidity" and not 0.0 < number <= 100.0:
        return number, QC.OUT_OF_RANGE
    if name == "temperature" and not -10.0 < number < 60.0:
        return number, QC.OUT_OF_RANGE
    if name == "windSpeed" and number < 0.0:
        return number, QC.NEGATIVE
    return number, QC.OK


def clean_concentration(metric: str, value: Any) -> tuple[float | None, str]:
    """Validasi satu nilai mentah dari ``rawHistory`` tanpa membuangnya.

    Tiga stasiun LCS mengalirkan suhu dan kelembapan lewat kanal parameter,
    bukan lewat ``rawMeteoHistory``. Aturan nol-sebagai-penanda-kosong karena
    itu harus berlaku di kedua jalur, bukan hanya di jalur meteorologi.
    """
    if value is None:
        return None, QC.OK
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None, QC.OK
    if metric in METEO_METRICS and number == 0.0:
        return None, QC.ZERO_PLACEHOLDER
    if number < 0.0:
        return number, QC.NEGATIVE
    if metric in PLAUSIBLE_RANGE:
        low, high = PLAUSIBLE_RANGE[metric]
        if not low <= number <= high:
            return number, QC.OUT_OF_RANGE
    return number, QC.OK


def mark_stuck(rows: list["Observation"], run_length: int = STUCK_RUN_LENGTH) -> None:
    """Tandai deret nilai identik yang terlalu panjang sebagai sensor macet."""
    by_metric: dict[str, list[Observation]] = {}
    for row in rows:
        by_metric.setdefault(row.metric, []).append(row)
    for series in by_metric.values():
        series.sort(key=lambda r: r.ts_utc)
        start = 0
        for i in range(1, len(series) + 1):
            same = i < len(series) and series[i].value == series[start].value
            if same:
                continue
            if i - start >= run_length and series[start].value is not None:
                for row in series[start:i]:
                    row.qc = (row.qc + QC.STUCK) if QC.STUCK not in row.qc else row.qc
            start = i


@dataclass(slots=True)
class Observation:
    station_uuid: str
    metric: str
    ts_utc: dt.datetime
    value: float | None
    qc: str = QC.OK


@dataclass(slots=True)
class IspuPoint:
    station_uuid: str
    metric: str
    ts_utc: dt.datetime
    ispu: float | None


@dataclass(slots=True)
class MeteoPoint:
    """Bendera disimpan per lapangan.

    Satu bendera untuk seluruh baris tidak cukup: ketika nilai suhu diambil
    dari lintasan sebelumnya sedangkan kelembapan tetap kosong, bendera yang
    tersimpan harus menerangkan lapangan yang memang masih kosong saja.
    """

    station_uuid: str
    ts_utc: dt.datetime
    temp_c: float | None
    rh_pct: float | None
    wind_ms: float | None
    qc_temp: str = QC.OK
    qc_rh: str = QC.OK
    qc_wind: str = QC.OK

    @property
    def qc(self) -> str:
        return "".join(sorted(set(self.qc_temp + self.qc_rh + self.qc_wind)))


@dataclass(slots=True)
class DailyIspu:
    station_uuid: str
    date_utc: dt.date
    max_ispu: int | None
    concentration: float | None
    dominant_metric: str | None
    provisional: bool


@dataclass(slots=True)
class StationRecord:
    uuid: str
    kode: str | None
    name: str | None
    type: str | None
    lat: float | None
    lng: float | None
    kecamatan: str | None
    kota: str | None
    alamat: str | None
    dioperasikan: str | None


@dataclass(slots=True)
class ParsedStation:
    """Hasil normalisasi satu halaman rinci."""

    station: StationRecord
    observations: list[Observation] = field(default_factory=list)
    ispu_points: list[IspuPoint] = field(default_factory=list)
    meteo: list[MeteoPoint] = field(default_factory=list)
    daily: list[DailyIspu] = field(default_factory=list)
    metrics: list[str] = field(default_factory=list)
    newest_raw_ts: dt.datetime | None = None
    ispu_value: int | None = None
    ispu_category: str | None = None
    ispu_parameter: str | None = None
    ispu_concentration: float | None = None
    last_update_raw: str | None = None
    cadence_minutes: int | None = None

    @property
    def is_reporting(self) -> bool:
        return bool(self.observations)


def _median_gap_minutes(stamps: Iterable[dt.datetime]) -> int | None:
    ordered = sorted(set(stamps))
    if len(ordered) < 2:
        return None
    gaps = [
        (b - a).total_seconds() / 60.0 for a, b in zip(ordered, ordered[1:])
    ]
    gaps.sort()
    return int(round(gaps[len(gaps) // 2]))


def parse_detail(payload: dict[str, Any], today_utc: dt.date | None = None) -> ParsedStation:
    """Ubah ``SPKU_DETAIL_DATA`` menjadi baris-baris siap simpan."""
    uuid = payload.get("datasourceID") or ""
    station = StationRecord(
        uuid=uuid,
        kode=payload.get("datasourceKode"),
        name=payload.get("datasourceName"),
        type=payload.get("type"),
        lat=payload.get("latitude"),
        lng=payload.get("longitude"),
        kecamatan=payload.get("kecamatanNama"),
        kota=payload.get("kotaKabupatenNama"),
        alamat=payload.get("alamat"),
        dioperasikan=payload.get("dioperasikan"),
    )
    result = ParsedStation(station=station)
    result.ispu_value = payload.get("ispuValue")
    result.ispu_category = payload.get("ispuCategory")
    result.ispu_parameter = payload.get("ispuParameter")
    result.ispu_concentration = payload.get("ispuConcentration")
    result.last_update_raw = payload.get("lastUpdate")
    result.metrics = [
        m.get("metricName") for m in payload.get("metrics") or [] if m.get("metricName")
    ]

    # rawHistory adalah sumber utama: terurut, 48 jam, seluruh parameter,
    # konsentrasi mentah. metrics[].history sengaja tidak dipakai.
    for point in payload.get("rawHistory") or []:
        ts = parse_detail_time(point.get("time"))
        metric = point.get("metricName")
        if ts is None or not metric:
            continue
        value, qc = clean_concentration(metric, point.get("value"))
        result.observations.append(Observation(uuid, metric, ts, value, qc))
    mark_stuck(result.observations)

    for point in payload.get("ispuHistory") or []:
        ts = parse_detail_time(point.get("time"))
        metric = point.get("metricName")
        if ts is None or not metric:
            continue
        result.ispu_points.append(IspuPoint(uuid, metric, ts, point.get("value")))

    for point in payload.get("rawMeteoHistory") or []:
        ts = parse_detail_time(point.get("time"))
        if ts is None:
            continue
        temp, qc_t = clean_meteo_value("temperature", point.get("temperature"))
        rh, qc_h = clean_meteo_value("humidity", point.get("humidity"))
        wind, qc_w = clean_meteo_value("windSpeed", point.get("windSpeed"))
        result.meteo.append(MeteoPoint(uuid, ts, temp, rh, wind, qc_t, qc_h, qc_w))

    today = today_utc or dt.datetime.now(UTC).date()
    for point in payload.get("dailyIspu30Days") or []:
        ts = parse_detail_time(point.get("date"))
        if ts is None:
            continue
        day = ts.date()
        result.daily.append(
            DailyIspu(
                station_uuid=uuid,
                date_utc=day,
                max_ispu=point.get("maxIspu"),
                concentration=point.get("concentration"),
                dominant_metric=point.get("dominantMetric"),
                # Baris hari berjalan masih parsial dan pernah teramati tidak
                # konsisten dengan rawHistory; tandai agar ditimpa esok hari.
                provisional=day >= today,
            )
        )

    if result.observations:
        result.newest_raw_ts = max(o.ts_utc for o in result.observations)
        result.cadence_minutes = _median_gap_minutes(
            o.ts_utc for o in result.observations if o.metric == result.observations[0].metric
        )
    return result


def parse_home(payload: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Normalisasi ``__SPKU_DATA__``; cap waktunya WIB, bukan UTC."""
    rows = []
    for item in payload or []:
        rows.append(
            {
                "station_uuid": item.get("id"),
                "initial": item.get("initial"),
                "ispu": item.get("ispu"),
                "status": item.get("status"),
                "dominant_metric": item.get("dominantMetric"),
                "dominant_raw_value": item.get("dominantRawValue"),
                "observed_ts_utc": parse_home_time(item.get("dominantMetricTime")),
                "lat": item.get("lat"),
                "lng": item.get("lng"),
            }
        )
    return rows
