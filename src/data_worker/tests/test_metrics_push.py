"""Issue #112: the Data Worker pushes each finished cycle to the Pushgateway, and a lost push never fails it."""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import metrics_push  # noqa: E402


class FakeResponse:
    def __init__(self, status=200):
        self.status_code = status

    def raise_for_status(self):
        if self.status_code >= 400:
            raise RuntimeError(f"HTTP {self.status_code}")


class FakeSession:
    def __init__(self, status=200, error=None):
        self.status, self.error, self.calls = status, error, []

    def post(self, url, data, headers, timeout):
        self.calls.append((url, data.decode(), headers, timeout))
        if self.error:
            raise self.error
        return FakeResponse(self.status)


INFERENCE = {
    "step": "trigger_downscale_inference", "status": "ok", "duration_s": 120.5, "coverage_ratio": 0.98,
    "rows_written": 372000,
    "model_metadata": {"pm25": {"source": "idw", "fallback_reason": "resolve_registry:RestException"},
                       "no2": {"source": "xgboost", "fallback_reason": None}},
    "confidence_distribution": {"n": 10, "low_lt_0_4": 7, "medium_0_4_to_0_7": 2, "high_ge_0_7": 1},
}


def test_degraded_cycle_is_pushed_with_steps_and_inference_summary():
    session = FakeSession()
    steps = [{"step": "fetch_ground_truth", "status": "degraded", "duration_s": 3.2}, INFERENCE]
    assert metrics_push.push_cycle("degraded", 412.6, 1_800_000_000, steps, url="https://pg.example/",
                                   session=session)
    [(url, body, headers, timeout)] = session.calls
    assert url == "https://pg.example/metrics/job/data_worker"
    assert headers["Content-Type"].startswith("text/plain")
    assert timeout == metrics_push.PUSH_TIMEOUT_S
    for line in (
        'sippolusi_job_status{status="degraded"} 1',
        'sippolusi_job_status{status="ok"} 0',
        'sippolusi_job_status{status="failed"} 0',
        "sippolusi_job_duration_seconds 412.6",
        "sippolusi_job_time_window_end_timestamp_seconds 1.8e+09",
        'sippolusi_job_step_status{status="degraded",step="fetch_ground_truth"} 1',
        'sippolusi_job_step_duration_seconds{step="trigger_downscale_inference"} 120.5',
        "sippolusi_downscale_coverage_ratio 0.98",
        'sippolusi_downscale_fallback{pollutant="pm25"} 1',
        'sippolusi_downscale_fallback{pollutant="no2"} 0',
        'sippolusi_downscale_model_source{pollutant="no2",source="xgboost"} 1',
        'sippolusi_downscale_confidence_edges{category="low"} 7',
        "# TYPE sippolusi_job_last_success_timestamp_seconds gauge",
    ):
        assert line in body.splitlines(), line
    assert body.endswith("\n")


def test_failed_cycle_keeps_the_last_success_and_inference_result():
    """POST replaces only the families sent: a failed cycle must not send last_success or downscale families."""
    session = FakeSession()
    metrics_push.push_cycle("failed", 5.0, None, [{"step": "fetch_ground_truth", "status": "failed"}],
                            url="https://pg.example", session=session)
    body = session.calls[0][1]
    assert 'sippolusi_job_status{status="failed"} 1' in body
    assert "sippolusi_job_last_finished_timestamp_seconds" in body
    assert "sippolusi_job_last_success_timestamp_seconds" not in body
    assert "sippolusi_downscale_" not in body


def test_disabled_inference_step_sends_no_downscale_families():
    session = FakeSession()
    metrics_push.push_cycle("ok", 1.0, None, [{"step": "trigger_downscale_inference", "status": "disabled"}],
                            url="https://pg.example", session=session)
    assert "sippolusi_downscale_" not in session.calls[0][1]


def test_without_url_nothing_is_sent(monkeypatch):
    monkeypatch.delenv("PUSHGATEWAY_URL", raising=False)
    session = FakeSession()
    assert metrics_push.push_cycle("ok", 1.0, None, [], session=session) is False
    assert session.calls == []


def test_push_failure_is_a_warning_without_the_url(caplog):
    for session in (FakeSession(status=503), FakeSession(error=ConnectionError("https://secret-host/x"))):
        caplog.clear()
        assert metrics_push.push_cycle("ok", 1.0, None, [], url="https://secret-host", session=session) is False
        [record] = [r for r in caplog.records if r.name == "data_worker.metrics"]
        assert record.levelname == "WARNING"
        assert "secret-host" not in record.getMessage()


def test_job_finished_pushes_the_same_status(monkeypatch):
    import datetime as dt
    import time

    import main
    from contracts import TimeWindow

    pushed = []
    monkeypatch.setattr(main.metrics_push, "push_cycle", lambda *args: pushed.append(args))
    window = TimeWindow(dt.datetime(2026, 11, 26, 8, tzinfo=dt.timezone.utc),
                        dt.datetime(2026, 11, 26, 9, tzinfo=dt.timezone.utc))
    steps = [{"step": "fetch_meteo", "status": "failed"}]
    main._job_finished(window, "schedule", "failed", time.monotonic(), steps)
    [(status, duration_s, window_end_s, sent_steps)] = pushed
    assert (status, window_end_s, sent_steps) == ("failed", window.end.timestamp(), steps)
    assert duration_s >= 0
