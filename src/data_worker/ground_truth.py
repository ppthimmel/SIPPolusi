"""Konektor ground truth (IGroundTruthFeed, Dokumen Desain Tabel 3.9 dan 3.18)."""

from __future__ import annotations

import logging
from typing import Sequence

from contracts import BBox, GroundTruthBatch, GroundTruthMeasurement, TimeWindow
from spku.collect import run_full
from spku.config import Config
from spku.store import Store

log = logging.getLogger("data_worker.ground_truth")

KNOWN_PROVIDERS = ("spku", "openaq", "bmkg", "iqair")

#: Kode parameter SPKU -> nama polutan pada struktur data desain.
SPKU_POLLUTANTS = {"PM25": "pm25", "NO2": "no2", "PM10": "pm10",
                   "SO2": "so2", "CO": "co", "O3": "o3"}


def fetch_ground_truth(
    provider: str,
    bbox: BBox,
    time_window: TimeWindow,
    *,
    config: Config | None = None,
    pollutants: Sequence[str] = ("pm25", "no2"),
) -> GroundTruthBatch:
    """Ambil pengukuran sensor darat per stasiun untuk satu time window.

    Untuk ``provider="spku"`` langkahnya sama persis dengan kolektor historis:
    satu lintasan penuh (daftar stasiun + seluruh halaman rinci, jendela 48
    jam) ditulis ke PostgreSQL secara idempoten, lalu pengukuran pada
    ``time_window`` dibaca kembali dari basis data. Karena setiap lintasan
    membawa riwayat 48 jam, lintasan yang gagal atau dibatalkan pun biasanya
    tetap menyisakan data time window tersebut dari lintasan sebelumnya.
    """
    if provider not in KNOWN_PROVIDERS:
        raise ValueError(f"penyedia ground truth tidak dikenal: {provider!r}")
    if provider != "spku":
        raise NotImplementedError(f"konektor {provider!r} belum diimplementasikan")

    config = config or Config.load()
    wanted = {code: name for code, name in SPKU_POLLUTANTS.items() if name in pollutants}

    summary = run_full(config)
    if summary.get("status") not in ("ok", "partial"):
        log.error("lintasan SPKU %s berstatus %s: %s",
                  summary.get("run_id"), summary.get("status"), summary.get("note"))

    with Store(config.database, config.db_schema) as store:
        rows = store.read_measurements(
            time_window.start, time_window.end, list(wanted), bbox.as_tuple()
        )
        in_bbox = store.conn.execute(
            """SELECT uuid, initial FROM station
               WHERE active AND ST_Intersects(geom, ST_MakeEnvelope(%s, %s, %s, %s, 4326))""",
            bbox.as_tuple(),
        ).fetchall()

    measurements = [
        GroundTruthMeasurement(
            provider="spku",
            station_id=r["station_uuid"],
            station_code=r["initial"],
            station_name=r["name"],
            station_type=r["type"],
            lat=r["lat"],
            lon=r["lng"],
            pollutant=wanted[r["metric"]],
            ts_utc=r["ts_utc"],
            value_ugm3=r["value"],
            qc=r["qc"],
        )
        for r in rows
        if r["value"] is not None
    ]
    reporting = {m.station_id for m in measurements}
    unavailable = sorted(r["initial"] or r["uuid"] for r in in_bbox if r["uuid"] not in reporting)
    available = bool(measurements)
    note = None if available else (
        f"tidak ada pengukuran SPKU pada {time_window}; lintasan {summary.get('status')}"
    )
    return GroundTruthBatch(
        provider="spku",
        time_window=time_window,
        available=available,
        measurements=measurements,
        unavailable_stations=unavailable,
        run=summary,
        note=note,
    )
