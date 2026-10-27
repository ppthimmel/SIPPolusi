"""Catatan eksekusi siklus Data Worker terjadwal (TI-DO-07).

Setiap eksekusi ``python main.py cycle`` atau siklus ``serve`` mencatat satu
baris pada ``<WORKER_DB_SCHEMA>.cycle_run`` (bawaan ``worker``): identitas run,
``TimeWindow``, pemicu, waktu terjadwal, waktu mulai dan selesai, status, dan
status setiap langkah. Baris ditulis saat siklus dimulai (``running``) dan
diperbarui setelah setiap langkah, sehingga siklus yang terhenti di tengah
tetap meninggalkan jejak.

Status siklus:

- ``ok``: seluruh langkah yang diimplementasikan berhasil.
- ``degraded``: tidak ada langkah yang gagal, tetapi sebagian sumber hanya
  tersedia sebagian (``degraded``) atau tidak tersedia (``unavailable``).
- ``failed``: sekurang-kurangnya satu langkah gagal, atau proses dihentikan
  sebelum siklus selesai.
- ``skipped``: siklus lain masih memegang kunci siklus sampai batas tunggu
  habis, sehingga time window ini tidak diproses.
- ``running``: siklus sedang berjalan; baris ``running`` yang ditinggalkan
  proses yang mati ditutup sebagai ``failed`` oleh siklus berikutnya.

Satu kunci advisory sesi (``data-worker-cycle``) mencegah dua siklus berjalan
bersamaan, termasuk siklus manual yang tumpang tindih dengan jadwal.
"""

from __future__ import annotations

import dataclasses
import datetime as dt
import json
import os
import re
import socket
import time
import uuid as uuidlib
from typing import Iterable, Sequence

from contracts import TimeWindow

UTC = dt.timezone.utc

#: Cron ``15 * * * *``: siklus untuk time window [H-1, H) dijadwalkan pada H:15.
SCHEDULE_OFFSET = dt.timedelta(minutes=15)
LOCK_KEY = "data-worker-cycle"
DEFAULT_SCHEMA = "worker"

#: Status langkah yang tidak menurunkan status siklus.
NEUTRAL_STEP_STATUSES = {"ok", "disabled", "not_implemented", "skipped"}
#: Status langkah yang menurunkan siklus menjadi ``degraded``.
DEGRADED_STEP_STATUSES = {"degraded", "unavailable"}
#: Status siklus yang dihitung berhasil (EDO-04): data diproses, walau mungkin sebagian.
SUCCESS_STATUSES = {"ok", "degraded"}
#: Pemicu yang berasal dari jadwal (cron Railway atau penjadwal internal ``serve``).
SCHEDULED_TRIGGERS = ("schedule", "serve")

_SCHEMA_NAME_RE = re.compile(r"^[a-z_][a-z0-9_]{0,62}$")

DDL = """
CREATE SCHEMA IF NOT EXISTS {schema};
CREATE TABLE IF NOT EXISTS {schema}.cycle_run (
    run_id             TEXT PRIMARY KEY,
    time_window_start  TIMESTAMPTZ NOT NULL,
    time_window_end    TIMESTAMPTZ NOT NULL,
    trigger            TEXT NOT NULL,          -- 'schedule' | 'serve' | 'manual'
    scheduled_utc      TIMESTAMPTZ NOT NULL,   -- akhir time window + 15 menit
    started_utc        TIMESTAMPTZ NOT NULL,
    finished_utc       TIMESTAMPTZ,
    status             TEXT NOT NULL,          -- running | ok | degraded | failed | skipped
    failed_steps       TEXT[] NOT NULL DEFAULT '{{}}',
    steps              JSONB NOT NULL DEFAULT '[]'::jsonb,
    host               TEXT,
    deployment_id      TEXT,
    note               TEXT
);
CREATE INDEX IF NOT EXISTS cycle_run_window_ix ON {schema}.cycle_run (time_window_start, started_utc);
CREATE INDEX IF NOT EXISTS cycle_run_started_ix ON {schema}.cycle_run (started_utc);
"""


def now_utc() -> dt.datetime:
    return dt.datetime.now(UTC)


def scheduled_for(window: TimeWindow) -> dt.datetime:
    return window.end + SCHEDULE_OFFSET


def cycle_status(steps: Sequence[dict]) -> str:
    """Status satu siklus dari status langkah-langkahnya."""
    statuses = [s.get("status") for s in steps]
    if any(s == "failed" for s in statuses):
        return "failed"
    if any(s in DEGRADED_STEP_STATUSES for s in statuses):
        return "degraded"
    unknown = [s for s in statuses if s not in NEUTRAL_STEP_STATUSES | DEGRADED_STEP_STATUSES]
    # Status langkah yang tidak dikenal tidak boleh diklaim sukses.
    return "degraded" if unknown else "ok"


def schema_name(environ=None) -> str:
    env = os.environ if environ is None else environ
    name = env.get("WORKER_DB_SCHEMA") or DEFAULT_SCHEMA
    if not _SCHEMA_NAME_RE.match(name):
        raise ValueError(f"nama skema WORKER_DB_SCHEMA tidak sah: {name!r}")
    return name


class CycleRecorder:
    """Pencatat siklus dan pemegang kunci siklus pada satu koneksi PostgreSQL."""

    def __init__(self, conn, schema: str = DEFAULT_SCHEMA):
        if not _SCHEMA_NAME_RE.match(schema):
            raise ValueError(f"nama skema tidak sah: {schema!r}")
        self.conn = conn
        self.schema = schema
        self._locked = False

    @classmethod
    def connect(cls, dsn: str, schema: str | None = None) -> "CycleRecorder":
        import psycopg
        from psycopg.rows import dict_row

        conn = psycopg.connect(dsn, autocommit=True, row_factory=dict_row)
        conn.execute("SET TIME ZONE 'UTC'")
        return cls(conn, schema or schema_name())

    # ------------------------------------------------------------ siklus hidup

    def close(self) -> None:
        try:
            self.release_lock()
        finally:
            self.conn.close()

    def __enter__(self) -> "CycleRecorder":
        return self

    def __exit__(self, *exc) -> None:
        self.close()

    def _table(self):
        from psycopg import sql

        return sql.SQL("{}.cycle_run").format(sql.Identifier(self.schema))

    def ensure_schema(self) -> None:
        """Buat skema dan tabel bila belum ada. Aman dijalankan berulang dan bersamaan."""
        with self.conn.transaction():
            self.conn.execute("SELECT pg_advisory_xact_lock(hashtext('worker-migrate'))")
            self.conn.execute(DDL.format(schema=f'"{self.schema}"'))

    def table_exists(self) -> bool:
        row = self.conn.execute("SELECT to_regclass(%s) AS t", (f'"{self.schema}".cycle_run',)).fetchone()
        return row["t"] is not None

    # ------------------------------------------------------------------ kunci

    def acquire_lock(self, wait_seconds: float = 0.0, poll_seconds: float = 5.0) -> bool:
        """Ambil kunci siklus; tunggu paling lama ``wait_seconds`` bila dipegang proses lain."""
        deadline = time.monotonic() + max(0.0, wait_seconds)
        while True:
            row = self.conn.execute("SELECT pg_try_advisory_lock(hashtext(%s)) AS ok", (LOCK_KEY,)).fetchone()
            if row["ok"]:
                self._locked = True
                return True
            if time.monotonic() >= deadline:
                return False
            time.sleep(min(poll_seconds, max(0.0, deadline - time.monotonic())))

    def release_lock(self) -> None:
        if self._locked and not self.conn.closed:
            self.conn.execute("SELECT pg_advisory_unlock(hashtext(%s))", (LOCK_KEY,))
        self._locked = False

    # ------------------------------------------------------------- penulisan

    def close_abandoned(self) -> list[str]:
        """Tutup baris ``running`` milik proses yang sudah mati.

        Hanya dipanggil oleh pemegang kunci siklus, sehingga tidak ada siklus lain
        yang benar-benar masih berjalan.
        """
        from psycopg import sql

        rows = self.conn.execute(
            sql.SQL("""UPDATE {} SET status = 'failed', finished_utc = %s,
                         note = COALESCE(note || '; ', '') || 'proses berhenti sebelum siklus selesai'
                       WHERE status = 'running' RETURNING run_id""").format(self._table()),
            (now_utc(),),
        ).fetchall()
        return [r["run_id"] for r in rows]

    def start(self, window: TimeWindow, trigger: str, note: str | None = None,
              status: str = "running") -> str:
        from psycopg import sql

        started = now_utc()
        run_id = f"{started:%Y%m%dT%H%M%SZ}-{uuidlib.uuid4().hex[:6]}"
        self.conn.execute(
            sql.SQL("""INSERT INTO {} (run_id, time_window_start, time_window_end, trigger,
                         scheduled_utc, started_utc, finished_utc, status, host, deployment_id, note)
                       VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)""").format(self._table()),
            (run_id, window.start, window.end, trigger, scheduled_for(window), started,
             started if status != "running" else None, status, socket.gethostname(),
             os.environ.get("RAILWAY_DEPLOYMENT_ID"), note),
        )
        return run_id

    def update_steps(self, run_id: str, steps: Sequence[dict]) -> None:
        from psycopg import sql

        self.conn.execute(
            sql.SQL("UPDATE {} SET steps = %s::jsonb WHERE run_id = %s").format(self._table()),
            (json.dumps(list(steps), default=str), run_id),
        )

    def finish(self, run_id: str, status: str, steps: Sequence[dict], note: str | None = None) -> None:
        from psycopg import sql

        failed = [s["step"] for s in steps if s.get("status") == "failed"]
        self.conn.execute(
            sql.SQL("""UPDATE {} SET status = %s, finished_utc = %s, steps = %s::jsonb,
                         failed_steps = %s, note = COALESCE(%s, note)
                       WHERE run_id = %s""").format(self._table()),
            (status, now_utc(), json.dumps(list(steps), default=str), failed, note, run_id),
        )

    # --------------------------------------------------------------- pembacaan

    def runs_since(self, since: dt.datetime) -> list["RunRow"]:
        from psycopg import sql

        if not self.table_exists():
            return []
        rows = self.conn.execute(
            sql.SQL("""SELECT run_id, time_window_start, trigger, scheduled_utc, started_utc,
                              finished_utc, status, failed_steps, note
                       FROM {} WHERE time_window_start >= %s OR started_utc >= %s
                       ORDER BY started_utc""").format(self._table()),
            (since, since),
        ).fetchall()
        return [RunRow(**r) for r in rows]


@dataclasses.dataclass(slots=True)
class RunRow:
    run_id: str
    time_window_start: dt.datetime
    trigger: str
    scheduled_utc: dt.datetime
    started_utc: dt.datetime
    status: str
    finished_utc: dt.datetime | None = None
    failed_steps: list[str] = dataclasses.field(default_factory=list)
    note: str | None = None


# ---------------------------------------------------------------- status jadwal

#: Kode keluar ``main.py status``.
STATUS_HEALTHY, STATUS_FAILING, STATUS_MISSING = 0, 1, 3

WINDOW_LABELS = {
    "ok": "berhasil",
    "degraded": "berhasil, sebagian sumber terganggu",
    "failed": "gagal",
    "skipped": "dilewati (siklus lain masih berjalan)",
    "running": "sedang berjalan",
    "stuck": "macet (berjalan melewati batas)",
    "pending": "belum dimulai, masih dalam toleransi",
    "missing": "tidak ada eksekusi (jadwal tidak berjalan)",
}


def classify_windows(runs: Iterable[RunRow], now: dt.datetime, hours: int = 24,
                     grace: dt.timedelta = dt.timedelta(minutes=30),
                     stuck_after: dt.timedelta = dt.timedelta(minutes=60),
                     late_after: dt.timedelta = dt.timedelta(minutes=10)) -> dict:
    """Bandingkan eksekusi yang tercatat dengan jadwal ``15 * * * *``.

    Setiap time window yang sudah jatuh tempo (akhir window + 15 menit <= now)
    pada ``hours`` jam terakhir diberi satu keadaan. ``missing`` (tidak ada baris
    sama sekali setelah toleransi) dibedakan dari ``failed``/``skipped``/``stuck``
    (eksekusi ada tetapi gagal) dan dari keterlambatan (``late_minutes``).

    ``state`` adalah hasil eksekusi terakhir window, termasuk eksekusi ulang
    manual. Keadaan jadwal dan putusan hanya memakai eksekusi terjadwal
    (``schedule``/``serve``): window yang hanya dieksekusi manual setelah
    toleransi ditandai ``schedule_missed``, sehingga eksekusi manual tidak
    menyamarkan cron yang tidak berjalan.
    """
    runs = sorted(runs, key=lambda r: r.started_utc)
    by_window: dict[dt.datetime, list[RunRow]] = {}
    for r in runs:
        by_window.setdefault(r.time_window_start.astimezone(UTC), []).append(r)

    last_due = TimeWindow.just_ended(now - SCHEDULE_OFFSET)
    windows = []
    for k in range(hours):
        w = TimeWindow.starting_at(last_due.start - dt.timedelta(hours=k))
        sched = scheduled_for(w)
        attempts = by_window.get(w.start, [])
        scheduled_runs = [r for r in attempts if r.trigger in SCHEDULED_TRIGGERS]
        entry = {"time_window": str(w), "scheduled_utc": sched.isoformat(), "attempts": len(attempts),
                 "scheduled_attempts": len(scheduled_runs),
                 "schedule_missed": not scheduled_runs and now >= sched + grace}
        if not attempts:
            entry["state"] = "pending" if now < sched + grace else "missing"
        else:
            latest = attempts[-1]
            if latest.status == "running":
                entry["state"] = "stuck" if now - latest.started_utc > stuck_after else "running"
            else:
                entry["state"] = latest.status
            entry["run_id"] = latest.run_id
            if latest.failed_steps:
                entry["failed_steps"] = list(latest.failed_steps)
            if latest.note:
                entry["note"] = latest.note
            first = scheduled_runs[0] if scheduled_runs else attempts[0]
            delay = (first.started_utc - sched).total_seconds() / 60
            entry["delay_minutes"] = round(delay, 1)
            entry["late"] = delay > late_after.total_seconds() / 60
            entry["manual_rerun"] = any(r.trigger == "manual" for r in attempts)
            entry["first_scheduled_status"] = scheduled_runs[0].status if scheduled_runs else None
        windows.append(entry)

    counts: dict[str, int] = {}
    for w in windows:
        counts[w["state"]] = counts.get(w["state"], 0) + 1
    due = [w for w in windows if w["state"] != "pending"]
    on_schedule = [w for w in due if w.get("first_scheduled_status") in SUCCESS_STATUSES]
    # Hanya eksekusi terjadwal yang membuktikan jadwal berjalan; eksekusi manual tidak.
    in_range = [r for r in runs if r.trigger in SCHEDULED_TRIGGERS
                and r.started_utc >= now - dt.timedelta(hours=hours) - SCHEDULE_OFFSET]

    latest_due = windows[0] if windows else None
    if not in_range:
        schedule_state = "no_runs"
    elif len(windows) >= 2 and not windows[0]["scheduled_attempts"] and windows[1]["schedule_missed"]:
        schedule_state = "stopped"
    else:
        schedule_state = "active"

    if schedule_state in ("no_runs", "stopped") or (latest_due and latest_due["schedule_missed"]):
        verdict, code = "schedule_missing", STATUS_MISSING
    elif latest_due and latest_due["state"] in ("failed", "skipped", "stuck"):
        verdict, code = "failing", STATUS_FAILING
    else:
        verdict, code = "healthy", STATUS_HEALTHY

    return {
        "now_utc": now.isoformat(),
        "hours": hours,
        "schedule": "15 * * * * (UTC)",
        "schedule_state": schedule_state,
        "verdict": verdict,
        "exit_code": code,
        "last_run_started_utc": runs[-1].started_utc.isoformat() if runs else None,
        "counts": counts,
        # EDO-04: proporsi time window jatuh tempo yang eksekusi terjadwal pertamanya berhasil.
        "scheduled_success_rate": round(len(on_schedule) / len(due), 3) if due else None,
        "windows": windows,
    }


def format_status(summary: dict) -> str:
    lines = [
        f"jadwal {summary['schedule']}; {summary['hours']} jam terakhir hingga {summary['now_utc']}",
        f"keadaan jadwal: {summary['schedule_state']}; putusan: {summary['verdict']} "
        f"(kode keluar {summary['exit_code']})",
        f"eksekusi terakhir dimulai: {summary['last_run_started_utc'] or '-'}",
        f"keberhasilan eksekusi terjadwal: {summary['scheduled_success_rate']}",
        "",
    ]
    for w in summary["windows"]:
        extra = []
        if w.get("delay_minutes") is not None:
            extra.append(f"mulai {w['delay_minutes']:+.0f} mnt" + (" (terlambat)" if w.get("late") else ""))
        if w.get("attempts", 0) > 1:
            extra.append(f"{w['attempts']} eksekusi")
        if w.get("manual_rerun"):
            extra.append("ada eksekusi ulang manual")
        if w.get("schedule_missed") and w.get("attempts"):
            extra.append("tanpa eksekusi terjadwal (hanya manual)")
        if w.get("failed_steps"):
            extra.append("gagal: " + ", ".join(w["failed_steps"]))
        lines.append(f"{w['time_window']}  {w['state']:<8} {WINDOW_LABELS[w['state']]}"
                     + (f"  [{'; '.join(extra)}]" if extra else ""))
    return "\n".join(lines)
