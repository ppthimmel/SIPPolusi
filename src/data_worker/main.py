"""Titik masuk Data Worker SIPPolusi.

Perintah:

    python main.py cycle                 # satu siklus untuk jam yang baru berakhir, lalu keluar
    python main.py cycle --window-start 2026-09-28T04:00Z
    python main.py serve [--now]         # proses menetap; siklus tiap jam pada menit ke-15
    python main.py check [--skip-db]     # pemeriksaan konfigurasi, impor modul, dan basis data
    python main.py status [--hours 24] [--json]   # eksekusi terjadwal: berhasil, terlambat, gagal, tidak ada
    python main.py health [--max-lag-hours 2]     # kesegaran data hasil job di basis data (TI-DO-08)
    python main.py spku collect|snapshot|health|export ...   # perintah kolektor SPKU
    python main.py features status|fetch|seed|rebuild ...    # data sumber sekunder dan FeatureMatrix (TI-SE-02)

Kode keluar ``cycle``/``serve``/``check``: 0 berhasil (siklus ``ok`` atau
``degraded``), 1 siklus atau pemeriksaan gagal (termasuk siklus yang dilewati
karena siklus lain masih berjalan, atau dihentikan sebelum selesai),
2 konfigurasi tidak sah (proses berhenti sebelum menyentuh portal).
Kode keluar ``status``: 0 sehat, 1 eksekusi terakhir gagal, 3 jadwal tidak
berjalan (tidak ada eksekusi untuk time window yang sudah jatuh tempo).
Kode keluar ``health``: 0 data segar, 1 data tertinggal atau basis data tidak terbaca.

``status`` menilai jadwal (apakah siklus berjalan dan berhasil); ``health``
menilai hasilnya (apakah ground truth dan cache polusi masih segar). Siklus
dapat berstatus ``ok`` sementara datanya tetap basi, dan sebaliknya; yang
dipantau di Railway ada di docs/devops/observability-TI-DO-08.md.

Setiap siklus dicatat pada tabel ``worker.cycle_run`` (lihat ``worker_runs.py``);
data sumber sekunder dan manifest FeatureMatrix pada skema ``features``
(lihat ``feature_store/``). ``run_id`` siklus juga menjadi ``correlation_id``
semua baris log siklus itu, sehingga baris ``status`` yang gagal langsung
menunjuk ke log-nya. Awal dan akhir siklus dicatat sebagai event
``job_started`` dan ``job_finished``; ``status`` event itu sama dengan status
pada ``cycle_run``.

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
import signal
import sys
import time

from acquisition import schedule_acquisition
from contracts import TimeWindow
from log_config import configure_logging, correlation, log_event
from preflight import config_problems, database_problems, import_problems
import worker_runs

log = logging.getLogger("data_worker")

SERVICE = "data_worker"
EXIT_CONFIG = 2
EXIT_FAILED = 1


class CycleInterrupted(BaseException):
    """SIGTERM diterima (mis. Railway menghentikan kontainer) sebelum siklus selesai."""


def _raise_interrupted(signum, frame):  # noqa: ARG001
    raise CycleInterrupted(f"dihentikan oleh sinyal {signal.Signals(signum).name}")


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


def run_cycle(window: TimeWindow | None = None, *, trigger: str = "schedule",
              recorder: "worker_runs.CycleRecorder | None" = None, steps=None, config=None,
              lock_wait_seconds: float | None = None) -> int:
    """Satu siklus untuk ``window`` (bawaan: jam yang baru berakhir), tercatat dan terkunci.

    Siklus berjalan di bawah kunci siklus sehingga tidak tumpang tindih dengan
    siklus lain. Bila kunci tidak diperoleh dalam ``WORKER_LOCK_WAIT_SECONDS``
    (bawaan 600 detik), siklus dicatat ``skipped`` dan keluar dengan kode 1.
    """
    window = window or TimeWindow.just_ended()
    if lock_wait_seconds is None:
        lock_wait_seconds = float(os.environ.get("WORKER_LOCK_WAIT_SECONDS") or 600)
    started = time.monotonic()
    owns_recorder = recorder is None
    if recorder is None:
        _init_cache_schemas()
        try:
            recorder = worker_runs.CycleRecorder.connect(os.environ["DATABASE_URL"])
        except Exception:  # noqa: BLE001 - tanpa basis data siklus tidak dapat dicatat maupun ditulis
            log.exception("basis data tidak terjangkau; siklus %s tidak dijalankan", window)
            _job_finished(window, trigger, "failed", started, note="basis data tidak terjangkau")
            return EXIT_FAILED
    try:
        recorder.ensure_schema()
        if not recorder.acquire_lock(lock_wait_seconds):
            note = f"siklus lain masih berjalan setelah menunggu {lock_wait_seconds:.0f} detik"
            run_id = recorder.start(window, trigger, status="skipped", note=note)
            with correlation(run_id):
                log.error("siklus %s dilewati (%s): siklus lain masih memegang kunci", window, run_id)
                _job_finished(window, trigger, "skipped", started, note=note)
            print(json.dumps({"run_id": run_id, "time_window": str(window), "status": "skipped"}))
            return EXIT_FAILED
        abandoned = recorder.close_abandoned()
        if abandoned:
            log.warning("baris running dari proses yang berhenti ditutup sebagai failed: %s", ", ".join(abandoned))
        run_id = recorder.start(window, trigger)
        # run_id cycle_run menjadi correlation ID: baris `status` yang gagal menunjuk ke log-nya.
        with correlation(run_id):
            return _run_recorded(recorder, run_id, window, trigger, started, steps=steps, config=config)
    finally:
        if owns_recorder:
            recorder.close()
        else:
            recorder.release_lock()


def _run_recorded(recorder, run_id: str, window: TimeWindow, trigger: str, started: float, *, steps, config) -> int:
    """Badan siklus setelah kunci diperoleh dan baris ``running`` dibuat."""
    log.info("siklus %s dimulai (run %s, pemicu %s)", window, run_id, trigger)
    log_event(log, "job_started", job="acquisition_cycle", time_window=str(window), trigger=trigger)
    steps_so_far: list[dict] = []

    def progress(current):
        steps_so_far[:] = current
        recorder.update_steps(run_id, current)

    previous = signal.getsignal(signal.SIGTERM)
    signal.signal(signal.SIGTERM, _raise_interrupted)
    try:
        report = schedule_acquisition(window, config=config, steps=steps, progress=progress,
                                      context={"run_id": run_id})
    except BaseException as exc:
        interrupted = isinstance(exc, CycleInterrupted)
        note = str(exc) if interrupted else f"{type(exc).__name__}: {exc}"
        recorder.finish(run_id, "failed", steps_so_far, note=note)
        log.error("siklus %s (run %s) tidak selesai: %s", window, run_id, note)
        _job_finished(window, trigger, "failed", started, steps_so_far, note=note, interrupted=interrupted)
        if interrupted:
            return EXIT_FAILED
        raise
    finally:
        signal.signal(signal.SIGTERM, previous)
    outcome = worker_runs.cycle_status(report["steps"])
    recorder.finish(run_id, outcome, report["steps"])
    report.update(run_id=run_id, trigger=trigger, status=outcome)
    print(json.dumps(report, indent=2, ensure_ascii=False, default=str))
    _job_finished(window, trigger, outcome, started, report["steps"])
    return 0 if outcome in worker_runs.SUCCESS_STATUSES else EXIT_FAILED


def _job_finished(window: TimeWindow, trigger: str, status: str, started: float, steps=(), *,
                  note: str | None = None, interrupted: bool = False) -> None:
    """Event ``job_finished``; ``status`` sama dengan yang tercatat pada ``cycle_run``."""
    failed = [s["step"] for s in steps if s.get("status") == "failed"]
    degraded = [s["step"] for s in steps if s.get("status") in worker_runs.DEGRADED_STEP_STATUSES]
    fallbacks = [s["step"] for s in steps if s.get("fallback_reason")]
    level = logging.INFO if status == "ok" else logging.WARNING if status == "degraded" else logging.ERROR
    log_event(log, "job_finished", level, job="acquisition_cycle", time_window=str(window), trigger=trigger,
              status=status, duration_s=round(time.monotonic() - started, 2),
              failed_steps=",".join(failed) or None, degraded_steps=",".join(degraded) or None,
              fallback_steps=",".join(fallbacks) or None, interrupted=interrupted or None, note=note)

def status(hours: int, as_json: bool, grace_minutes: float, stuck_minutes: float) -> int:
    """Bandingkan eksekusi tercatat dengan jadwal; tidak menulis apa pun."""
    now = worker_runs.now_utc()
    with worker_runs.CycleRecorder.connect(os.environ["DATABASE_URL"]) as recorder:
        runs = recorder.runs_since(now - dt.timedelta(hours=hours + 2))
    summary = worker_runs.classify_windows(
        runs, now, hours=hours, grace=dt.timedelta(minutes=grace_minutes),
        stuck_after=dt.timedelta(minutes=stuck_minutes))
    print(json.dumps(summary, indent=2, ensure_ascii=False) if as_json else worker_runs.format_status(summary))
    return summary["exit_code"]


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
    schedule.every().hour.at(":15").do(run_cycle, trigger="serve")
    log.info("data worker berjalan; siklus tiap jam pada menit ke-15 (UTC waktu mesin)")
    if run_now:
        run_cycle(trigger="serve")
    while True:
        schedule.run_pending()
        time.sleep(30)


def main(argv: list[str] | None = None) -> int:
    argv = sys.argv[1:] if argv is None else argv
    configure_logging(service=SERVICE)
    if argv and argv[0] == "spku":
        from spku.cli import main as spku_main

        return spku_main(argv[1:])
    if argv and argv[0] == "features":
        from feature_store.cli import main as features_main

        return features_main(argv[1:])

    parser = argparse.ArgumentParser(prog="data_worker", description="Data Worker SIPPolusi")
    sub = parser.add_subparsers(dest="command", required=True)
    p_cycle = sub.add_parser("cycle", help="satu siklus akuisisi lalu keluar (untuk cron)")
    p_cycle.add_argument("--window-start", help="awal time window dalam UTC; bawaan: jam yang baru berakhir")
    p_serve = sub.add_parser("serve", help="proses menetap dengan penjadwal internal")
    p_serve.add_argument("--now", action="store_true", help="jalankan satu siklus segera")
    p_check = sub.add_parser("check", help="periksa konfigurasi, impor modul, dan koneksi basis data")
    p_check.add_argument("--skip-db", action="store_true", help="tanpa koneksi basis data (mis. saat build)")
    p_status = sub.add_parser("status", help="eksekusi terjadwal: berhasil, terlambat, gagal, atau tidak ada")
    p_status.add_argument("--hours", type=int, default=24, help="rentang pemeriksaan (jam); bawaan 24")
    p_status.add_argument("--json", action="store_true", help="keluaran JSON")
    p_status.add_argument("--grace-minutes", type=float, default=30,
                          help="toleransi setelah waktu terjadwal sebelum dianggap tidak ada eksekusi; bawaan 30")
    p_status.add_argument("--stuck-minutes", type=float, default=60,
                          help="siklus running lebih lama dari ini dianggap macet; bawaan 60")
    p_health = sub.add_parser("health", help="kesegaran data hasil job (exit 1 bila tertinggal)")
    p_health.add_argument("--max-lag-hours", type=float, default=2.0,
                          help="umur maksimum hasil terakhir sebelum dianggap tertinggal (bawaan 2)")
    sub.add_parser("spku", help="perintah kolektor SPKU (collect, snapshot, health, export)")
    sub.add_parser("features", help="data sumber sekunder dan FeatureMatrix (status, fetch, seed, rebuild)")
    args = parser.parse_args(argv)

    if args.command == "check":
        return check(args.skip_db)
    if args.command == "cycle":
        if not _config_ok():
            return EXIT_CONFIG
        if args.window_start:
            return run_cycle(_parse_start(args.window_start), trigger="manual")
        return run_cycle(trigger="schedule")
    if args.command == "status":
        if not os.environ.get("DATABASE_URL"):
            log.error("DATABASE_URL belum diatur")
            return EXIT_CONFIG
        return status(args.hours, args.json, args.grace_minutes, args.stuck_minutes)
    if args.command == "serve":
        return serve(args.now)
    if args.command == "health":
        if not _config_ok():
            return EXIT_CONFIG
        return health(args.max_lag_hours)
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
