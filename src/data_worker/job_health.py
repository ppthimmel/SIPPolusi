"""Kesehatan job Data Worker (TI-DO-08): apakah siklus per jam tertinggal.

Data Worker berjalan sebagai cron job tanpa endpoint HTTP, sehingga kesehatannya
dibedakan menjadi tiga tingkat:

    hidup  (liveness)    python main.py check --skip-db   proses dapat dimulai: konfigurasi dan impor modul
    siap   (readiness)   python main.py check             ditambah koneksi basis data dan skema
    segar  (job lag)     python main.py health            hasil job terakhir tidak tertinggal

Siklus untuk jam H berjalan pada H+1:15, sehingga dalam keadaan normal hasil
terbaru berumur 15 sampai 75 menit. Batas bawaan 2 jam berarti paling sedikit
satu siklus terlewat atau gagal. Yang diperiksa:

    ground_truth     run kolektor SPKU terakhir yang selesai ok/partial (skema ground_truth)
    pollution_cache  akhir time window complete terbaru di pollution.pollution_window,
                     hanya bila DOWNSCALE_WRITE_CACHE=1 (selain itu "disabled")
"""

from __future__ import annotations

import datetime as dt
import logging

log = logging.getLogger("data_worker.health")

DEFAULT_MAX_LAG = dt.timedelta(hours=2)
WINDOW_LENGTH = dt.timedelta(hours=1)
HEALTHY_STATUSES = ("ok", "disabled")


def _freshness(now: dt.datetime, moment: dt.datetime | None, max_lag: dt.timedelta) -> dict:
    if moment is None:
        return {"status": "missing"}
    lag = now - moment
    return {
        "status": "ok" if lag <= max_lag else "lagging",
        "last": moment.isoformat(),
        "lag_minutes": round(lag.total_seconds() / 60, 1),
    }


def assess(
    now: dt.datetime,
    *,
    last_ground_truth: dt.datetime | None,
    latest_window_start: dt.datetime | None,
    downscale_enabled: bool,
    max_lag: dt.timedelta = DEFAULT_MAX_LAG,
) -> dict:
    """Nilai kesegaran dari waktu hasil terakhir; tanpa I/O agar mudah diuji."""
    checks = {"database": {"status": "ok"}, "ground_truth": _freshness(now, last_ground_truth, max_lag)}
    if downscale_enabled:
        window_end = latest_window_start + WINDOW_LENGTH if latest_window_start else None
        checks["pollution_cache"] = _freshness(now, window_end, max_lag)
    else:
        checks["pollution_cache"] = {"status": "disabled"}
    return report(now, checks, max_lag)


def report(now: dt.datetime, checks: dict, max_lag: dt.timedelta = DEFAULT_MAX_LAG) -> dict:
    healthy = all(check["status"] in HEALTHY_STATUSES for check in checks.values())
    return {
        "status": "healthy" if healthy else "unhealthy",
        "checked_at": now.isoformat(timespec="seconds"),
        "max_lag_minutes": round(max_lag.total_seconds() / 60),
        "checks": checks,
    }


def read_state(dsn: str, ground_truth_schema: str = "ground_truth") -> tuple[dt.datetime | None, dt.datetime | None]:
    """(selesainya run ground truth terakhir yang berhasil, awal time window complete terbaru).

    Tabel yang belum ada (cache belum pernah diisi) dibaca sebagai None, bukan galat.
    """
    import psycopg
    from psycopg import sql

    from spatial_model import cache

    with psycopg.connect(dsn, connect_timeout=10, autocommit=True) as conn:
        try:
            last_ground_truth = conn.execute(
                sql.SQL("SELECT max(finished_utc) FROM {}.run WHERE status IN ('ok', 'partial')")
                .format(sql.Identifier(ground_truth_schema))
            ).fetchone()[0]
        except psycopg.errors.UndefinedTable:
            last_ground_truth = None
        try:
            latest_window_start = cache.latest_complete_window(conn)
        except psycopg.errors.UndefinedTable:
            latest_window_start = None
    return last_ground_truth, latest_window_start


def check(
    dsn: str,
    *,
    ground_truth_schema: str = "ground_truth",
    downscale_enabled: bool,
    max_lag: dt.timedelta = DEFAULT_MAX_LAG,
    now: dt.datetime | None = None,
) -> dict:
    now = now or dt.datetime.now(dt.timezone.utc)
    try:
        last_ground_truth, latest_window_start = read_state(dsn, ground_truth_schema)
    except Exception as exc:  # noqa: BLE001 - dilaporkan sebagai database unavailable
        log.error("pemeriksaan kesehatan job: basis data tidak dapat dibaca: %s: %s", type(exc).__name__, exc)
        return report(now, {"database": {"status": "unavailable"}}, max_lag)
    return assess(now, last_ground_truth=last_ground_truth, latest_window_start=latest_window_start,
                  downscale_enabled=downscale_enabled, max_lag=max_lag)
