"""Titik masuk Data Worker SIPPolusi.

Perintah:

    python main.py cycle                 # satu siklus untuk jam yang baru berakhir, lalu keluar
    python main.py cycle --window-start 2026-09-28T04:00Z
    python main.py serve [--now]         # proses menetap; siklus tiap jam pada menit ke-15
    python main.py check [--skip-db]     # pemeriksaan konfigurasi, impor modul, dan basis data
    python main.py health [--max-lag-hours 2]   # apakah hasil job tertinggal (TI-DO-08)
    python main.py spku collect|snapshot|health|export ...   # perintah kolektor SPKU

Kode keluar: 0 berhasil, 1 ada langkah siklus atau pemeriksaan yang gagal
(untuk ``health``: job tertinggal atau basis data tidak terbaca), 2 konfigurasi
tidak sah (proses berhenti sebelum menyentuh portal).

Setiap siklus berjalan di bawah satu correlation ID (``cycle-<uuid>``) yang
tercantum pada semua baris log siklus itu dan pada laporannya; awal dan akhir
siklus dicatat sebagai event ``job_started`` dan ``job_finished``.

Di Railway, service ini dijalankan sebagai cron job ``15 * * * *`` dengan
perintah ``python main.py cycle`` (lihat railway.json), sesuai Tabel 3.9:
schedule_acquisition dipicu CronJob pada menit ke-15 setiap jam.
"""

from __future__ import annotations

import argparse
import datetime as dt
import json
import logging
import os
import sys
import time

from acquisition import schedule_acquisition
from contracts import TimeWindow
from log_config import configure_logging, correlation, log_event, new_correlation_id
from preflight import config_problems, database_problems, import_problems

log = logging.getLogger("data_worker")

SERVICE = "data_worker"
EXIT_CONFIG = 2


def _parse_start(text: str) -> TimeWindow:
    value = text.strip()
    if value.endswith("Z"):
        value = value[:-1] + "+00:00"
    moment = dt.datetime.fromisoformat(value)
    if moment.tzinfo is None:
        raise SystemExit("--window-start wajib menyertakan zona, misalnya 2026-09-28T04:00Z")
    return TimeWindow.starting_at(moment)


def _init_cache_schemas() -> None:
    """Skema cache ``pollution`` dan ``osm`` (SQLAlchemy, milik Data Worker).

    Idempoten. Kegagalannya tidak menghentikan siklus: skema ``ground_truth``
    dibuat terpisah oleh ``spku.store.Store`` pada koneksi pertama.
    """
    try:
        from database import init_db

        init_db()
    except Exception:  # noqa: BLE001
        log.exception("init_db gagal; siklus tetap dijalankan")


def _bullets(problems: list[str]) -> str:
    return "\n".join(f"- {p}" for p in problems)


def _config_ok() -> bool:
    problems = config_problems()
    if problems:
        log.error("konfigurasi tidak sah, Data Worker tidak dijalankan:\n%s", _bullets(problems))
    return not problems


def check(skip_db: bool) -> int:
    """Pemeriksaan awal tanpa menulis apa pun; aman dijalankan kapan saja."""
    results = {"konfigurasi": config_problems(), "impor modul": import_problems()}
    if skip_db:
        results["basis data"] = None
    elif results["konfigurasi"]:
        results["basis data"] = ["dilewati karena konfigurasi tidak sah"]
    else:
        results["basis data"] = database_problems(os.environ["DATABASE_URL"])
    for name, problems in results.items():
        if problems is None:
            log.info("cek %s: dilewati (--skip-db)", name)
        elif problems:
            log.error("cek %s: GAGAL\n%s", name, _bullets(problems))
        else:
            log.info("cek %s: ok", name)
    if results["konfigurasi"]:
        return EXIT_CONFIG
    return 1 if any(results.values()) else 0


def run_cycle(window: TimeWindow | None = None) -> int:
    window = window or TimeWindow.just_ended()
    with correlation(new_correlation_id("cycle-")) as cycle_id:
        started = time.monotonic()
        log_event(log, "job_started", job="acquisition_cycle", time_window=str(window))
        _init_cache_schemas()
        report = schedule_acquisition(window)
        report["correlation_id"] = cycle_id
        print(json.dumps(report, indent=2, ensure_ascii=False, default=str))
        fallbacks = [s["step"] for s in report["steps"] if s.get("fallback_reason")]
        status = "failed" if report["failed"] else "degraded" if fallbacks else "ok"
        log_event(
            log, "job_finished", {"ok": logging.INFO, "degraded": logging.WARNING}.get(status, logging.ERROR),
            job="acquisition_cycle", time_window=str(window), status=status,
            duration_s=round(time.monotonic() - started, 2),
            failed_steps=",".join(report["failed"]) or None, fallback_steps=",".join(fallbacks) or None,
        )
    return 1 if report["failed"] else 0


def health(max_lag_hours: float) -> int:
    """Laporan kesegaran hasil job (lihat job_health); 0 sehat, 1 tertinggal atau tidak terbaca."""
    from job_health import check as check_job_health
    from spku.config import Config

    config = Config.load()
    result = check_job_health(
        config.database, ground_truth_schema=config.db_schema,
        downscale_enabled=os.environ.get("DOWNSCALE_WRITE_CACHE") == "1",
        max_lag=dt.timedelta(hours=max_lag_hours),
    )
    print(json.dumps(result, indent=2, ensure_ascii=False))
    healthy = result["status"] == "healthy"
    log_event(log, "job_health", logging.INFO if healthy else logging.ERROR, status=result["status"],
              **{name: c["status"] for name, c in result["checks"].items()})
    return 0 if healthy else 1


def serve(run_now: bool) -> int:
    """Proses menetap untuk lingkungan tanpa cron (pengembangan lokal)."""
    import schedule

    if not _config_ok():
        return EXIT_CONFIG
    schedule.every().hour.at(":15").do(run_cycle)
    log.info("data worker berjalan; siklus tiap jam pada menit ke-15 (UTC waktu mesin)")
    if run_now:
        run_cycle()
    while True:
        schedule.run_pending()
        time.sleep(30)


def main(argv: list[str] | None = None) -> int:
    argv = sys.argv[1:] if argv is None else argv
    configure_logging(service=SERVICE)
    if argv and argv[0] == "spku":
        from spku.cli import main as spku_main

        return spku_main(argv[1:])

    parser = argparse.ArgumentParser(prog="data_worker", description="Data Worker SIPPolusi")
    sub = parser.add_subparsers(dest="command", required=True)
    p_cycle = sub.add_parser("cycle", help="satu siklus akuisisi lalu keluar (untuk cron)")
    p_cycle.add_argument("--window-start", help="awal time window dalam UTC; bawaan: jam yang baru berakhir")
    p_serve = sub.add_parser("serve", help="proses menetap dengan penjadwal internal")
    p_serve.add_argument("--now", action="store_true", help="jalankan satu siklus segera")
    p_check = sub.add_parser("check", help="periksa konfigurasi, impor modul, dan koneksi basis data")
    p_check.add_argument("--skip-db", action="store_true", help="tanpa koneksi basis data (mis. saat build)")
    p_health = sub.add_parser("health", help="periksa apakah hasil job tertinggal (exit 1 bila tidak sehat)")
    p_health.add_argument("--max-lag-hours", type=float, default=2.0,
                          help="umur maksimum hasil terakhir sebelum dianggap tertinggal (bawaan 2)")
    sub.add_parser("spku", help="perintah kolektor SPKU (collect, snapshot, health, export)")
    args = parser.parse_args(argv)

    if args.command == "check":
        return check(args.skip_db)
    if args.command == "cycle":
        if not _config_ok():
            return EXIT_CONFIG
        return run_cycle(_parse_start(args.window_start) if args.window_start else None)
    if args.command == "serve":
        return serve(args.now)
    if args.command == "health":
        if not _config_ok():
            return EXIT_CONFIG
        return health(args.max_lag_hours)
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
