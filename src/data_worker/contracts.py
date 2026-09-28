"""Struktur data bersama Data Worker (Dokumen Desain, Tabel 3.3)."""

from __future__ import annotations

import dataclasses
import datetime as dt

UTC = dt.timezone.utc


@dataclasses.dataclass(frozen=True, slots=True)
class BBox:
    """min_lon, min_lat, max_lon, max_lat dalam WGS 84 (EPSG:4326)."""

    min_lon: float
    min_lat: float
    max_lon: float
    max_lat: float

    def as_tuple(self) -> tuple[float, float, float, float]:
        return (self.min_lon, self.min_lat, self.max_lon, self.max_lat)


#: Wilayah studi: daratan DKI Jakarta (±662 km², Dokumen Desain subbab 4.3.3).
#: Kepulauan Seribu sengaja di luar bbox; stasiunnya (Pulau Panggang) tetap
#: tersimpan di basis data, hanya tidak dikembalikan untuk wilayah studi.
JAKARTA_BBOX = BBox(min_lon=106.68, min_lat=-6.38, max_lon=106.98, max_lat=-6.08)


@dataclasses.dataclass(frozen=True, slots=True)
class TimeWindow:
    """start dan end dalam UTC, durasi satu jam, start dibulatkan ke awal jam."""

    start: dt.datetime
    end: dt.datetime

    def __post_init__(self):
        for name in ("start", "end"):
            value = getattr(self, name)
            if value.tzinfo is None:
                raise ValueError(f"TimeWindow.{name} wajib memiliki zona waktu")
        if self.start.astimezone(UTC) != _floor_hour(self.start):
            raise ValueError("TimeWindow.start wajib tepat di awal jam")
        if self.end - self.start != dt.timedelta(hours=1):
            raise ValueError("TimeWindow wajib berdurasi satu jam")

    @classmethod
    def starting_at(cls, start: dt.datetime) -> "TimeWindow":
        start = _floor_hour(start)
        return cls(start, start + dt.timedelta(hours=1))

    @classmethod
    def just_ended(cls, now: dt.datetime | None = None) -> "TimeWindow":
        """Time window satu jam yang baru berakhir.

        Dijalankan pada 05.15 UTC menghasilkan 04.00-05.00 UTC (UT-DW-01a).
        """
        now = now or dt.datetime.now(UTC)
        end = _floor_hour(now)
        return cls(end - dt.timedelta(hours=1), end)

    def __str__(self) -> str:
        return f"{self.start:%Y-%m-%dT%H:%MZ}/{self.end:%H:%MZ}"


def _floor_hour(value: dt.datetime) -> dt.datetime:
    if value.tzinfo is None:
        raise ValueError("datetime wajib memiliki zona waktu")
    return value.astimezone(UTC).replace(minute=0, second=0, microsecond=0)


@dataclasses.dataclass(frozen=True, slots=True)
class GroundTruthMeasurement:
    """Satu pengukuran sensor darat beserta koordinat stasiun dan kode mutu."""

    provider: str
    station_id: str
    station_code: str | None
    station_name: str | None
    station_type: str | None      # 'Reference' atau 'Sensor' untuk SPKU
    lat: float | None
    lon: float | None
    pollutant: str                # 'pm25', 'no2', ... (penamaan Tabel 3.3)
    ts_utc: dt.datetime
    value_ugm3: float
    qc: str                       # bendera kendali mutu penyedia; '' berarti bersih


@dataclasses.dataclass(slots=True)
class GroundTruthBatch:
    """Keluaran fetch_ground_truth untuk satu penyedia dan satu time window."""

    provider: str
    time_window: TimeWindow
    available: bool
    measurements: list[GroundTruthMeasurement]
    #: Stasiun aktif di dalam bbox yang tidak punya pengukuran pada time window.
    unavailable_stations: list[str] = dataclasses.field(default_factory=list)
    #: Ringkasan lintasan pengambilan (untuk SPKU: keluaran run_full).
    run: dict = dataclasses.field(default_factory=dict)
    note: str | None = None
