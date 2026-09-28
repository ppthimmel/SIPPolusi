"""Titik masuk Data Worker SIPPolusi.

Perintah:

    python main.py cycle                 # satu siklus untuk jam yang baru berakhir, lalu keluar
    python main.py cycle --window-start 2026-09-28T04:00Z
    python main.py serve [--now]         # proses menetap; siklus tiap jam pada menit ke-15
    python main.py spku collect|snapshot|health|export ...   # perintah kolektor SPKU

Di Railway, service ini dijalankan sebagai cron job ``15 * * * *`` dengan
perintah ``python main.py cycle`` (lihat railway.json), sesuai Tabel 3.9:
schedule_acquisition dipicu CronJob pada menit ke-15 setiap jam.
"""

from __future__ import annotations

import argparse
import datetime as dt
import json
import logging
import sys
import time

from acquisition import schedule_acquisition
from contracts import TimeWindow

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)-7s %(name)s: %(message)s",
    datefmt="%Y-%m-%dT%H:%M:%S%z",
)
log = logging.getLogger("data_worker")


def _parse_start(text: str) -> TimeWindow:
    value = text.strip()
    if value.endswith("Z"):
        value = value[:-1] + "+00:00"
    moment = dt.datetime.fromisoformat(value)
    if moment.tzinfo is None:
        raise SystemExit("--window-start wajib menyertakan zona, misalnya 2026-09-28T04:00Z")
    return TimeWindow.starting_at(moment)


def run_cycle(window: TimeWindow | None = None) -> int:
    window = window or TimeWindow.just_ended()
    log.info("siklus akuisisi untuk %s dimulai", window)
    report = schedule_acquisition(window)
    print(json.dumps(report, indent=2, ensure_ascii=False, default=str))
    return 1 if report["failed"] else 0


def serve(run_now: bool) -> int:
    """Proses menetap untuk lingkungan tanpa cron (pengembangan lokal)."""
    import schedule

    schedule.every().hour.at(":15").do(run_cycle)
    log.info("data worker berjalan; siklus tiap jam pada menit ke-15 (UTC waktu mesin)")
    if run_now:
        run_cycle()
    while True:
        schedule.run_pending()
        time.sleep(30)


def main(argv: list[str] | None = None) -> int:
    argv = sys.argv[1:] if argv is None else argv
    if argv and argv[0] == "spku":
        from spku.cli import main as spku_main

        return spku_main(argv[1:])

    parser = argparse.ArgumentParser(prog="data_worker", description="Data Worker SIPPolusi")
    sub = parser.add_subparsers(dest="command", required=True)
    p_cycle = sub.add_parser("cycle", help="satu siklus akuisisi lalu keluar (untuk cron)")
    p_cycle.add_argument("--window-start", help="awal time window dalam UTC; bawaan: jam yang baru berakhir")
    p_serve = sub.add_parser("serve", help="proses menetap dengan penjadwal internal")
    p_serve.add_argument("--now", action="store_true", help="jalankan satu siklus segera")
    sub.add_parser("spku", help="perintah kolektor SPKU (collect, snapshot, health, export)")
    args = parser.parse_args(argv)

    if args.command == "cycle":
        return run_cycle(_parse_start(args.window_start) if args.window_start else None)
    if args.command == "serve":
        return serve(args.now)
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
