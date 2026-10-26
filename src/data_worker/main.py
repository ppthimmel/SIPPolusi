"""Titik masuk Data Worker SIPPolusi.

Perintah:

    python main.py cycle                 # satu siklus untuk jam yang baru berakhir, lalu keluar
    python main.py cycle --window-start 2026-09-28T04:00Z
    python main.py serve [--now]         # proses menetap; siklus tiap jam pada menit ke-15
    python main.py check [--skip-db]     # pemeriksaan konfigurasi, impor modul, dan basis data
    python main.py spku collect|snapshot|health|export ...   # perintah kolektor SPKU

Kode keluar: 0 berhasil, 1 ada langkah siklus atau pemeriksaan yang gagal,
2 konfigurasi tidak sah (proses berhenti sebelum menyentuh portal).

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
from log_config import configure_logging
from preflight import config_problems, database_problems, import_problems

log = logging.getLogger("data_worker")

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
    _init_cache_schemas()
    log.info("siklus akuisisi untuk %s dimulai", window)
    report = schedule_acquisition(window)
    print(json.dumps(report, indent=2, ensure_ascii=False, default=str))
    return 1 if report["failed"] else 0


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
    configure_logging()
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
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
