"""Uji Data Worker terjadwal (TI-DO-07): catatan eksekusi, kunci siklus, jalur gagal, status jadwal."""

import datetime as dt
import logging
import os
import signal

import pytest
import requests

import acquisition
import main
import worker_runs
from contracts import TimeWindow
from spku import fetch as fetch_mod
from test_collect import _config, _patch

UTC = dt.timezone.utc
# Label fixture SPKU 2026-09-13T05:00-06:00 WIB = 2026-09-12T22:00-23:00 UTC.
WINDOW = TimeWindow.starting_at(dt.datetime(2026, 9, 12, 22, 0, tzinfo=UTC))


# ------------------------------------------------------------ status siklus

@pytest.mark.parametrize("statuses, expected", [
    (["ok", "not_implemented", "disabled"], "ok"),
    (["ok", "degraded"], "degraded"),
    (["unavailable", "ok"], "degraded"),
    (["ok", "failed", "degraded"], "failed"),
    (["ok", "status_baru"], "degraded"),   # status tak dikenal tidak diklaim sukses
])
def test_status_siklus_dari_status_langkah(statuses, expected):
    assert worker_runs.cycle_status([{"step": str(i), "status": s} for i, s in enumerate(statuses)]) == expected


@pytest.mark.parametrize("run_status, available, expected", [
    ("ok", True, "ok"),
    ("partial", True, "degraded"),
    ("ok", False, "unavailable"),
    ("aborted", True, "failed"),     # data lama masih ada, tetapi sumber gagal pada siklus ini
    ("failed", True, "failed"),
    ("aborted", False, "failed"),
    (None, False, "failed"),
])
def test_kegagalan_sumber_spku_tidak_diklaim_sukses(run_status, available, expected):
    assert acquisition.ground_truth_status(run_status, available) == expected


# ------------------------------------------------- gagal sementara dan permanen

class _Response:
    def __init__(self, status):
        self.status_code = status
        self.text = "<html></html>" if status == 200 else "galat"
        self.content = self.text.encode()


class _Session:
    def __init__(self, script):
        self.script = list(script)
        self.headers = {}

    def get(self, url, timeout):
        item = self.script.pop(0)
        if isinstance(item, Exception):
            raise item
        return _Response(item)

    def close(self):
        pass


def _fetcher(monkeypatch, script, retries=3):
    monkeypatch.setattr(fetch_mod.time, "sleep", lambda s: None)
    f = fetch_mod.Fetcher(delay_seconds=0, retries=retries)
    f.session = _Session(script)
    return f


def test_gagal_sementara_pulih_dan_tercatat(monkeypatch, caplog):
    f = _fetcher(monkeypatch, [503, requests.ConnectionError("putus"), 200])
    with caplog.at_level(logging.WARNING, logger="spku.fetch"):
        result = f.get("/spku/x")
    assert result.ok and result.attempts == 3
    assert (f.retried_requests, f.failed_requests) == (1, 0)
    assert sum("gagal pada percobaan" in r.getMessage() for r in caplog.records) == 2
    assert any("berhasil pada percobaan 3" in r.getMessage() for r in caplog.records)


def test_gagal_permanen_setelah_seluruh_percobaan(monkeypatch, caplog):
    f = _fetcher(monkeypatch, [503, 503, 503])
    with caplog.at_level(logging.WARNING, logger="spku.fetch"):
        result = f.get("/spku/x")
    assert not result.ok and result.status == 503 and result.attempts == 3
    assert (f.retried_requests, f.failed_requests) == (0, 1)
    assert any(r.levelno == logging.ERROR and "tetap gagal setelah 3 percobaan" in r.getMessage()
               for r in caplog.records)


def test_404_tidak_diulang(monkeypatch):
    f = _fetcher(monkeypatch, [404])
    result = f.get("/spku/hilang")
    assert result.status == 404 and result.attempts == 1 and f.failed_requests == 1


# ------------------------------------------------------- status jadwal (tanpa DB)

NOW = dt.datetime(2026, 10, 26, 12, 20, tzinfo=UTC)   # window 11:00-12:00 jatuh tempo pukul 12:15


def _run(start_hour, status="ok", delay_min=1, trigger="schedule", run_id=None):
    w = TimeWindow.starting_at(dt.datetime(2026, 10, 26, start_hour, 0, tzinfo=UTC))
    started = worker_runs.scheduled_for(w) + dt.timedelta(minutes=delay_min)
    return worker_runs.RunRow(run_id=run_id or f"r{start_hour}{trigger}", time_window_start=w.start,
                              trigger=trigger, scheduled_utc=worker_runs.scheduled_for(w),
                              started_utc=started, status=status, failed_steps=["x"] if status == "failed" else [])


def _states(summary):
    return {w["time_window"][11:16]: w["state"] for w in summary["windows"]}


def test_jadwal_sehat_dengan_keterlambatan_dan_gagal_dibedakan():
    runs = [_run(h) for h in range(5, 11)] + [_run(11, delay_min=25)]
    runs[2] = _run(7, "failed")
    summary = worker_runs.classify_windows(runs, NOW, hours=7)
    states = _states(summary)
    assert states["11:00"] == "ok" and states["07:00"] == "failed"
    latest = summary["windows"][0]
    assert latest["late"] and latest["delay_minutes"] == 25
    assert summary["verdict"] == "healthy" and summary["exit_code"] == 0
    assert summary["scheduled_success_rate"] == round(6 / 7, 3)


def test_eksekusi_terakhir_gagal_memberi_kode_1():
    runs = [_run(h) for h in range(5, 11)] + [_run(11, "failed")]
    summary = worker_runs.classify_windows(runs, NOW, hours=7)
    assert summary["verdict"] == "failing" and summary["exit_code"] == worker_runs.STATUS_FAILING


def test_ulang_manual_tidak_dihitung_sebagai_keberhasilan_terjadwal():
    runs = [_run(11, "failed"), _run(11, "ok", delay_min=30, trigger="manual")]
    summary = worker_runs.classify_windows(runs, NOW, hours=1)
    w = summary["windows"][0]
    assert w["state"] == "ok" and w["manual_rerun"] and w["attempts"] == 2
    assert summary["scheduled_success_rate"] == 0.0


def test_tidak_ada_eksekusi_dibedakan_dari_gagal_dan_terlambat():
    runs = [_run(h) for h in range(3, 9)]            # jadwal berhenti setelah window 08:00
    summary = worker_runs.classify_windows(runs, NOW, hours=9)
    states = _states(summary)
    # 11:00 baru jatuh tempo 12:15 (masih dalam toleransi); 09:00 dan 10:00 tidak pernah dieksekusi
    assert [states[h] for h in ("09:00", "10:00", "11:00")] == ["missing", "missing", "pending"]
    assert summary["schedule_state"] == "stopped" and summary["verdict"] == "schedule_missing"
    assert summary["exit_code"] == worker_runs.STATUS_MISSING


def test_hanya_eksekusi_manual_bukan_bukti_jadwal_berjalan():
    """Cron tidak pernah berjalan; satu eksekusi manual tidak boleh membuat jadwal tampak aktif."""
    summary = worker_runs.classify_windows([_run(10, "ok", delay_min=3, trigger="manual")], NOW, hours=6)
    manual = summary["windows"][1]                  # 10:00; window 11:00 masih dalam toleransi
    assert manual["state"] == "ok" and manual["schedule_missed"] and manual["scheduled_attempts"] == 0
    assert not summary["windows"][0]["schedule_missed"]
    assert summary["schedule_state"] == "no_runs"
    assert summary["verdict"] == "schedule_missing" and summary["exit_code"] == worker_runs.STATUS_MISSING
    assert "hanya manual" in worker_runs.format_status(summary)


def test_eksekusi_ulang_manual_tidak_menyamarkan_cron_yang_berhenti():
    runs = [_run(h) for h in range(3, 9)]                           # cron berhenti setelah window 08:00
    runs += [_run(9, "ok", delay_min=90, trigger="manual"), _run(10, "ok", delay_min=30, trigger="manual")]
    summary = worker_runs.classify_windows(runs, NOW, hours=9)
    states = _states(summary)
    assert [states[h] for h in ("09:00", "10:00", "11:00")] == ["ok", "ok", "pending"]
    assert summary["schedule_state"] == "stopped" and summary["verdict"] == "schedule_missing"
    assert summary["windows"][1]["schedule_missed"] and summary["windows"][1]["manual_rerun"]


def test_eksekusi_manual_di_samping_cron_yang_berjalan_tetap_sehat():
    runs = [_run(h) for h in range(5, 12)] + [_run(9, "ok", delay_min=120, trigger="manual")]
    summary = worker_runs.classify_windows(runs, NOW, hours=7)
    assert summary["schedule_state"] == "active" and summary["verdict"] == "healthy"
    assert not any(w["schedule_missed"] for w in summary["windows"])


def test_belum_pernah_berjalan():
    summary = worker_runs.classify_windows([], NOW, hours=6)
    assert summary["schedule_state"] == "no_runs" and summary["exit_code"] == worker_runs.STATUS_MISSING
    assert summary["scheduled_success_rate"] == 0.0


def test_window_jatuh_tempo_dalam_toleransi_dan_siklus_macet():
    runs = [_run(h) for h in range(5, 10)] + [_run(10, "running", delay_min=0)]
    at = dt.datetime(2026, 10, 26, 12, 30, tzinfo=UTC)    # window 11:00 baru 15 menit jatuh tempo
    summary = worker_runs.classify_windows(runs, at, hours=7, stuck_after=dt.timedelta(minutes=60))
    states = _states(summary)
    assert states["11:00"] == "pending" and states["10:00"] == "stuck"
    assert summary["verdict"] == "healthy"   # window terakhir masih dalam toleransi


# ------------------------------------------------------- dengan PostgreSQL

@pytest.fixture
def recorder(db):
    import psycopg
    from psycopg.rows import dict_row

    conn = psycopg.connect(db.dsn, autocommit=True, row_factory=dict_row)
    conn.execute("SET TIME ZONE 'UTC'")
    rec = worker_runs.CycleRecorder(conn, db.schema)
    rec.ensure_schema()
    yield rec
    rec.close()


def _other(db):
    import psycopg
    from psycopg.rows import dict_row

    return worker_runs.CycleRecorder(psycopg.connect(db.dsn, autocommit=True, row_factory=dict_row), db.schema)


def _rows(rec):
    return rec.conn.execute(
        f'SELECT * FROM "{rec.schema}".cycle_run ORDER BY started_utc').fetchall()


def ok_step(window, config):
    return {"status": "ok"}


def test_skema_catatan_idempoten(recorder):
    recorder.ensure_schema()
    recorder.ensure_schema()
    assert recorder.table_exists()


def test_siklus_berhasil_tercatat_dengan_status_tiap_langkah(recorder, capsys):
    code = main.run_cycle(WINDOW, trigger="manual", recorder=recorder, lock_wait_seconds=0,
                          steps=[("a", ok_step), ("belum", None)], config=object())
    assert code == 0
    (row,) = _rows(recorder)
    assert row["status"] == "ok" and row["trigger"] == "manual" and row["finished_utc"] is not None
    assert (row["time_window_start"], row["time_window_end"]) == (WINDOW.start, WINDOW.end)
    assert row["scheduled_utc"] == WINDOW.end + dt.timedelta(minutes=15)
    assert [(s["step"], s["status"]) for s in row["steps"]] == [("a", "ok"), ("belum", "not_implemented")]
    assert '"status": "ok"' in capsys.readouterr().out


def test_langkah_gagal_tercatat_dan_kode_keluar_1(recorder):
    def boom(window, config):
        raise ConnectionError("portal tidak terjangkau")

    code = main.run_cycle(WINDOW, recorder=recorder, lock_wait_seconds=0,
                          steps=[("fetch_ground_truth", boom), ("b", ok_step)], config=object())
    assert code == 1
    (row,) = _rows(recorder)
    assert row["status"] == "failed" and row["failed_steps"] == ["fetch_ground_truth"]
    assert row["steps"][0]["error"].startswith("ConnectionError")
    assert row["steps"][1]["status"] == "ok"      # UT-DW-01b: langkah berikutnya tetap berjalan


def test_siklus_tumpang_tindih_dilewati_dan_tercatat(recorder, db):
    holder = _other(db)
    assert holder.acquire_lock(0)
    try:
        code = main.run_cycle(WINDOW, recorder=recorder, lock_wait_seconds=0,
                              steps=[("a", ok_step)], config=object())
    finally:
        holder.close()
    assert code == 1
    (row,) = _rows(recorder)
    assert row["status"] == "skipped" and row["steps"] == []
    assert recorder.acquire_lock(0)           # kunci lepas setelah pemegangnya selesai


def test_sigterm_mencatat_siklus_gagal_beserta_langkah_yang_sudah_selesai(recorder):
    def terminated(window, config):
        os.kill(os.getpid(), signal.SIGTERM)
        return {"status": "ok"}

    before = signal.getsignal(signal.SIGTERM)
    code = main.run_cycle(WINDOW, recorder=recorder, lock_wait_seconds=0,
                          steps=[("a", ok_step), ("b", terminated), ("c", ok_step)], config=object())
    assert code == 1 and signal.getsignal(signal.SIGTERM) == before
    (row,) = _rows(recorder)
    assert row["status"] == "failed" and "SIGTERM" in row["note"]
    assert [s["step"] for s in row["steps"]] == ["a"]


def test_baris_running_proses_mati_ditutup_oleh_siklus_berikutnya(recorder):
    stale = recorder.start(WINDOW, "schedule")        # proses mati tanpa sempat finish
    main.run_cycle(WINDOW, recorder=recorder, lock_wait_seconds=0, steps=[("a", ok_step)], config=object())
    rows = {r["run_id"]: r for r in _rows(recorder)}
    assert rows[stale]["status"] == "failed" and "berhenti" in rows[stale]["note"]


def test_eksekusi_ulang_window_yang_sama_idempoten(recorder, db, monkeypatch):
    """Ulang siklus untuk time window yang sama: ground truth tidak berduplikasi."""
    _patch(monkeypatch, "ok")
    config = _config(db)
    steps = [("fetch_ground_truth", acquisition._step_ground_truth)]

    def counts():
        with db.store() as store:
            return {t: store.conn.execute(f"SELECT COUNT(*) AS n FROM {t}").fetchone()["n"]
                    for t in ("observation", "observation_ispu", "meteo", "station")}

    assert main.run_cycle(WINDOW, trigger="schedule", recorder=recorder, lock_wait_seconds=0,
                          steps=steps, config=config) == 0
    first = counts()
    assert main.run_cycle(WINDOW, trigger="manual", recorder=recorder, lock_wait_seconds=0,
                          steps=steps, config=config) == 0
    assert counts() == first and first["observation"] > 0
    runs = _rows(recorder)
    assert [r["status"] for r in runs] == ["ok", "ok"]
    assert runs[1]["steps"][0]["measurements"] == runs[0]["steps"][0]["measurements"]
    with db.store() as store:
        second = store.conn.execute("SELECT rows_new, rows_revised FROM run WHERE run_id = %s",
                                    (runs[1]["steps"][0]["run_id"],)).fetchone()
    assert (second["rows_new"], second["rows_revised"]) == (0, 0)


def test_portal_kosong_membuat_siklus_gagal_bukan_sukses(recorder, db, monkeypatch):
    _patch(monkeypatch, "ok")
    steps = [("fetch_ground_truth", acquisition._step_ground_truth)]
    main.run_cycle(WINDOW, recorder=recorder, lock_wait_seconds=0, steps=steps, config=_config(db))
    _patch(monkeypatch, "kosong")     # gangguan 8 September: HTTP 200, nol observasi
    code = main.run_cycle(WINDOW, recorder=recorder, lock_wait_seconds=0, steps=steps, config=_config(db))
    assert code == 1
    row = _rows(recorder)[-1]
    step = row["steps"][0]
    assert row["status"] == "failed" and step["run_status"] == "aborted"
    assert step["available"] is True and "data lintasan sebelumnya" in step["note"]


def test_status_dibaca_dari_catatan(recorder):
    now = worker_runs.now_utc()
    last = TimeWindow.just_ended(now - worker_runs.SCHEDULE_OFFSET)
    main.run_cycle(last, recorder=recorder, lock_wait_seconds=0, steps=[("a", ok_step)], config=object())
    summary = worker_runs.classify_windows(recorder.runs_since(now - dt.timedelta(hours=3)), now, hours=2)
    assert summary["windows"][0]["state"] == "ok"
    assert summary["windows"][1]["state"] in ("missing",)


def test_lintasan_spku_yang_dihentikan_tidak_tertinggal_running(db, monkeypatch):
    from spku import collect as collect_mod

    class Stop(BaseException):
        pass

    fake = _patch(monkeypatch, "ok")
    original = fake.get

    def get(path):
        if path.startswith("/spku/"):
            raise Stop("dihentikan oleh sinyal SIGTERM")
        return original(path)

    fake.get = get
    with pytest.raises(Stop):
        collect_mod.run_full(_config(db))
    with db.store() as store:
        row = store.conn.execute("SELECT status, note FROM run ORDER BY started_utc DESC LIMIT 1").fetchone()
        written = store.conn.execute("SELECT COUNT(*) AS n FROM observation").fetchone()["n"]
    assert row["status"] == "failed" and "dihentikan" in row["note"] and written == 0
