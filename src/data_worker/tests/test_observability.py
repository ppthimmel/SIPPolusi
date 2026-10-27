"""TI-DO-08: correlation ID per siklus, event job terstruktur, dan deteksi job tertinggal."""

from __future__ import annotations

import datetime as dt
import json
import logging

import pytest

import job_health
import main
from acquisition import schedule_acquisition
from contracts import TimeWindow
from log_config import configure_logging

UTC = dt.timezone.utc
RAHASIA = "s3cr3t-pa55word"
DSN = f"postgresql://sippolusi:{RAHASIA}@postgres.railway.internal:5432/railway"
NOW = dt.datetime(2026, 10, 27, 9, 30, tzinfo=UTC)
WINDOW = TimeWindow.starting_at(dt.datetime(2026, 10, 27, 8, 0, tzinfo=UTC))


def log_json(out: str) -> list[dict]:
    """Baris log JSON pada stdout; laporan siklus (JSON berindentasi) dilewati."""
    return [json.loads(line) for line in out.splitlines() if line.startswith('{"time"')]


@pytest.fixture
def json_stdout(monkeypatch):
    monkeypatch.setenv("LOG_FORMAT", "json")
    monkeypatch.setenv("DATABASE_URL", DSN)
    yield
    logging.getLogger().handlers.clear()


# kesegaran job -------------------------------------------------------------
def test_hasil_segar_sehat_dan_cache_nonaktif_tidak_dinilai():
    result = job_health.assess(NOW, last_ground_truth=NOW - dt.timedelta(minutes=20),
                               latest_window_start=None, downscale_enabled=False)
    assert result["status"] == "healthy"
    assert result["checks"]["ground_truth"]["status"] == "ok"
    assert result["checks"]["pollution_cache"] == {"status": "disabled"}


def test_ground_truth_lebih_dari_dua_jam_berarti_tertinggal():
    result = job_health.assess(NOW, last_ground_truth=NOW - dt.timedelta(hours=3),
                               latest_window_start=NOW - dt.timedelta(minutes=90), downscale_enabled=True)
    assert result["status"] == "unhealthy"
    assert result["checks"]["ground_truth"] == {
        "status": "lagging", "last": (NOW - dt.timedelta(hours=3)).isoformat(), "lag_minutes": 180.0}
    # Time window 08:00 berakhir 09:00: umur 30 menit, masih segar.
    assert result["checks"]["pollution_cache"]["status"] == "ok"


def test_cache_aktif_tanpa_time_window_complete_tidak_sehat():
    result = job_health.assess(NOW, last_ground_truth=NOW, latest_window_start=None, downscale_enabled=True)
    assert result["status"] == "unhealthy" and result["checks"]["pollution_cache"] == {"status": "missing"}


def test_basis_data_tak_terbaca_dilaporkan_tanpa_kredensial(monkeypatch, caplog):
    def gagal(dsn, schema):
        raise OSError(f"connection to {dsn} refused")

    monkeypatch.setattr(job_health, "read_state", gagal)
    result = job_health.check(DSN, downscale_enabled=False, now=NOW)
    assert result["status"] == "unhealthy" and result["checks"] == {"database": {"status": "unavailable"}}
    assert RAHASIA not in json.dumps(result)


def test_perintah_health_keluar_1_dan_mencatat_event_saat_tertinggal(monkeypatch, capsys, json_stdout):
    five_hours_ago = dt.datetime.now(UTC) - dt.timedelta(hours=5)
    monkeypatch.setattr(job_health, "read_state", lambda dsn, schema: (five_hours_ago, None))
    assert main.main(["health"]) == 1
    out, err = capsys.readouterr()
    [event] = [e for e in log_json(out) if e.get("event") == "job_health"]
    assert event["level"] == "error" and event["status"] == "unhealthy"
    assert event["ground_truth"] == "lagging" and event["service"] == "data_worker"
    assert RAHASIA not in out and err == ""


def test_perintah_health_keluar_0_saat_segar(monkeypatch, capsys, json_stdout):
    monkeypatch.setattr(job_health, "read_state", lambda dsn, schema: (dt.datetime.now(UTC), None))
    assert main.main(["health", "--max-lag-hours", "1"]) == 0
    assert '"status": "healthy"' in capsys.readouterr().out


# correlation ID dan event siklus ------------------------------------------
class FakeRecorder:
    """CycleRecorder di memori: kunci, baris cycle_run, dan status akhirnya."""

    def __init__(self, lock_free: bool = True):
        self.lock_free = lock_free
        self.rows: dict[str, dict] = {}

    def ensure_schema(self):
        pass

    def acquire_lock(self, wait_seconds=0.0):
        return self.lock_free

    def release_lock(self):
        pass

    def close_abandoned(self):
        return []

    def start(self, window, trigger, note=None, status="running"):
        run_id = f"20261027T091500Z-{len(self.rows):06x}"
        self.rows[run_id] = {"status": status, "note": note, "steps": []}
        return run_id

    def update_steps(self, run_id, steps):
        self.rows[run_id]["steps"] = list(steps)

    def finish(self, run_id, status, steps, note=None):
        self.rows[run_id].update(status=status, steps=list(steps), note=note)


def run_logged(capsys, recorder, steps) -> tuple[int, list[dict]]:
    configure_logging({"LOG_FORMAT": "json", "DATABASE_URL": DSN}, service="data_worker")
    try:
        code = main.run_cycle(WINDOW, trigger="manual", recorder=recorder, steps=steps, config=object(),
                              lock_wait_seconds=0)
        out, _ = capsys.readouterr()
    finally:
        logging.getLogger().handlers.clear()
    return code, log_json(out)


def events(entries, name):
    return [e for e in entries if e.get("event") == name]


def test_run_id_siklus_menjadi_correlation_id_semua_log(capsys):
    def langkah(window, config):
        logging.getLogger("spatial_model.inference").info("menghitung grid")
        return {"status": "ok", "run_id": "dsi-1", "fallback_reason": '{"pm25": "model tidak terdaftar"}'}

    recorder = FakeRecorder()
    code, entries = run_logged(capsys, recorder, [("trigger_downscale_inference", langkah)])
    (run_id,) = recorder.rows
    assert code == 0
    assert {e.get("correlation_id") for e in entries} == {run_id}
    [step] = events(entries, "job_step")
    # run_id milik langkah tidak bertabrakan dengan run_id siklus.
    assert step["step_run_id"] == "dsi-1" and step["level"] == "warn" and "run_id" not in step
    [finished] = events(entries, "job_finished")
    assert finished["status"] == recorder.rows[run_id]["status"] == "ok"
    assert finished["fallback_steps"] == "trigger_downscale_inference"
    assert events(entries, "job_started")[0]["trigger"] == "manual"


def test_status_job_finished_sama_dengan_cycle_run_untuk_langkah_degraded(capsys):
    recorder = FakeRecorder()
    steps = [("fetch_ground_truth", lambda w, c: {"status": "degraded", "note": "3 stasiun gagal"}),
             ("fetch_meteo", lambda w, c: {"status": "unavailable", "note": "sumber kosong"})]
    code, entries = run_logged(capsys, recorder, steps)
    (row,) = recorder.rows.values()
    [finished] = events(entries, "job_finished")
    assert code == 0 and row["status"] == finished["status"] == "degraded"
    assert finished["level"] == "warn"
    assert finished["degraded_steps"] == "fetch_ground_truth,fetch_meteo"
    # degraded dan unavailable tidak lagi tercatat sebagai INFO.
    assert [(e["step"], e["level"], e["note"]) for e in events(entries, "job_step")] == [
        ("fetch_ground_truth", "warn", "3 stasiun gagal"), ("fetch_meteo", "warn", "sumber kosong")]


def test_siklus_dilewati_karena_kunci_tercatat_skipped(capsys):
    recorder = FakeRecorder(lock_free=False)
    code, entries = run_logged(capsys, recorder, [("a", lambda w, c: {"status": "ok"})])
    (run_id,) = recorder.rows
    [finished] = events(entries, "job_finished")
    assert code == 1 and recorder.rows[run_id]["status"] == finished["status"] == "skipped"
    assert finished["level"] == "error" and finished["correlation_id"] == run_id
    assert not events(entries, "job_started")


def test_siklus_terhenti_sigterm_tercatat_failed_interrupted(capsys):
    def dihentikan(window, config):
        raise main.CycleInterrupted("dihentikan oleh sinyal SIGTERM")

    recorder = FakeRecorder()
    steps = [("a", lambda w, c: {"status": "ok"}), ("b", dihentikan)]
    code, entries = run_logged(capsys, recorder, steps)
    (row,) = recorder.rows.values()
    [finished] = events(entries, "job_finished")
    assert code == 1 and row["status"] == finished["status"] == "failed"
    assert finished["interrupted"] is True and "SIGTERM" in finished["note"]


def test_langkah_gagal_tercatat_sebagai_error(caplog):
    def gagal(window, config):
        raise ConnectionError("sumber tidak terjangkau")

    with caplog.at_level(logging.INFO):
        report = schedule_acquisition(WINDOW, config=object(), steps=[("fetch_ground_truth", gagal), ("x", None)])
    assert report["failed"] == ["fetch_ground_truth"]
    steps = [r for r in caplog.records if getattr(r, "event", None) == "job_step"]
    assert [(r.event_fields["step"], r.levelname) for r in steps] == [
        ("fetch_ground_truth", "ERROR"), ("x", "INFO")]
    assert steps[0].event_fields["error"] == "ConnectionError: sumber tidak terjangkau"
