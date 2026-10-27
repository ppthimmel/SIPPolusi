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
def test_semua_log_siklus_membawa_correlation_id_yang_sama(monkeypatch, capsys, json_stdout):
    def langkah_fallback(window, config):
        logging.getLogger("spatial_model.inference").info("menghitung grid")
        return {"status": "ok", "run_id": "run-1", "fallback_reason": "ModelUnavailableError: belum ada ST-GNN"}

    def siklus(window):
        return schedule_acquisition(window, config=object(), steps=[("trigger_downscale_inference", langkah_fallback)])

    monkeypatch.setattr(main, "schedule_acquisition", siklus)
    monkeypatch.setattr(main, "_init_cache_schemas", lambda: None)
    assert main.main(["cycle", "--window-start", "2026-10-27T08:00Z"]) == 0
    out, _ = capsys.readouterr()
    entries = log_json(out)
    ids = {e.get("correlation_id") for e in entries}
    assert len(ids) == 1 and ids.pop().startswith("cycle-")
    report = json.loads(out[out.index("\n{\n") + 1: out.index("\n}\n") + 2])
    assert report["correlation_id"] == entries[0]["correlation_id"]

    by_event = {e["event"]: e for e in entries if "event" in e}
    assert by_event["job_started"]["time_window"].startswith("2026-10-27T08:00")
    step = by_event["job_step"]
    assert step["level"] == "warn" and step["fallback_reason"].startswith("ModelUnavailableError")
    finished = by_event["job_finished"]
    assert finished["level"] == "warn" and finished["status"] == "degraded"
    assert finished["fallback_steps"] == "trigger_downscale_inference" and "failed_steps" not in finished


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
