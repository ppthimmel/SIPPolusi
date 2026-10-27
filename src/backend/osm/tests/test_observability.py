"""TI-DO-08: liveness vs readiness, correlation ID in logs, request metrics."""
from __future__ import annotations

import json
import logging

import pytest
from fastapi.testclient import TestClient

from log_config import RedactingFormatter
from osm.app import create_app
from osm.errors import CacheUnavailable
from pollution.tests.test_api import FakePollutionRepository
from telemetry import METRICS

from .conftest import FakeRepository


class NoGraphRepository(FakeRepository):
    """Database reachable, but no active graph_version yet."""

    async def get_active_version(self, *, refresh: bool = False):
        raise CacheUnavailable("belum ada graf aktif", CacheUnavailable.CACHE_EMPTY)


class StalePollutionRepository(FakePollutionRepository):
    def staleness(self, window):
        return True, 4 * 3600.0


def make_client(settings, repo=None, pollution=None) -> TestClient:
    return TestClient(create_app(repository=repo or FakeRepository(), settings=settings,
                                 pollution_repository=pollution))


def test_liveness_does_not_touch_the_database(settings):
    repo = FakeRepository()
    repo.fail = CacheUnavailable("down")
    with make_client(settings, repo) as client:
        resp = client.get("/health/live")
    assert resp.status_code == 200 and resp.json() == {"status": "ok", "service": "cache-api"}
    assert repo.calls == []


def test_ready_when_database_and_graph_are_there(settings):
    with make_client(settings, pollution=FakePollutionRepository()) as client:
        resp = client.get("/health/ready")
    assert resp.status_code == 200
    assert resp.json()["checks"] == {"database": "ok", "graph": "ok", "pollution": "ok"}


def test_not_ready_when_database_is_down(settings):
    repo = FakeRepository()
    repo.fail = CacheUnavailable("down")
    with make_client(settings, repo) as client:
        live, ready = client.get("/health/live"), client.get("/health/ready")
    assert live.status_code == 200
    assert ready.status_code == 503
    assert ready.json() == {"status": "not_ready", "service": "cache-api",
                            "checks": {"database": "unavailable", "graph": "unknown", "pollution": "unknown"}}


def test_not_ready_without_an_active_graph(settings):
    with make_client(settings, NoGraphRepository()) as client:
        resp = client.get("/health/ready")
    assert resp.status_code == 503 and resp.json()["checks"]["graph"] == "empty"


def test_stale_pollution_is_reported_but_keeps_the_api_ready(settings):
    with make_client(settings, pollution=StalePollutionRepository()) as client:
        resp = client.get("/health/ready")
    assert resp.status_code == 200 and resp.json()["checks"]["pollution"] == "stale"


@pytest.fixture
def json_log():
    """Collect records formatted exactly as on Railway (JSON lines)."""
    lines: list[str] = []
    formatter = RedactingFormatter(as_json=True, service="cache-api")

    class Collect(logging.Handler):
        def emit(self, record):
            if not record.name.startswith("httpx"):  # the test client's own log, not the service's
                lines.append(formatter.format(record))

    handler = Collect(level=logging.DEBUG)
    root = logging.getLogger()
    old_level = root.level
    root.addHandler(handler)
    root.setLevel(logging.DEBUG)
    yield lambda: [json.loads(line) for line in lines]
    root.removeHandler(handler)
    root.setLevel(old_level)


def test_request_log_carries_the_gateway_request_id(settings, json_log):
    with make_client(settings) as client:
        resp = client.get("/v1/osm/nearest-node", params={"lat": -6.2123, "lon": 106.8123, "mode": "walk"},
                          headers={"X-Request-ID": "gw-abc-123"})
        client.get("/v1/osm/nodes/98765")
    assert resp.headers["X-Request-ID"] == "gw-abc-123"
    first, second = [e for e in json_log() if e.get("event") == "http_request"]
    assert first["correlation_id"] == "gw-abc-123" and first["service"] == "cache-api"
    assert first["route"] == "/v1/osm/nearest-node" and first["status"] == resp.status_code
    assert isinstance(first["duration_ms"], float)
    assert second["route"] == "/v1/osm/nodes/{node_id}" and second["correlation_id"] != "gw-abc-123"
    # Neither the query string (coordinates) nor path parameters reach the log.
    logged = json.dumps(json_log())
    assert "6.2123" not in logged and "106.8123" not in logged and "98765" not in logged


def test_unsafe_request_id_is_replaced(settings, json_log):
    with make_client(settings) as client:
        resp = client.get("/v1/osm/version", headers={"X-Request-ID": "x\nlevel=error fake"})
    request_id = resp.headers["X-Request-ID"]
    assert "\n" not in request_id and request_id != "x\nlevel=error fake"
    [entry] = [e for e in json_log() if e.get("event") == "http_request"]
    assert entry["correlation_id"] == request_id


def test_server_errors_are_logged_as_errors_and_counted(settings, json_log):
    METRICS.reset()
    repo = FakeRepository()
    repo.fail = CacheUnavailable("down")
    with make_client(settings, repo) as client:
        assert client.get("/v1/osm/version").status_code == 503
        metrics = client.get("/metrics")
    [entry] = [e for e in json_log() if e.get("event") == "http_request" and e["route"] == "/v1/osm/version"]
    assert entry["level"] == "error" and entry["status"] == 503
    assert metrics.headers["content-type"].startswith("text/plain")
    assert ('sippolusi_http_requests_total{method="GET",route="/v1/osm/version",service="cache-api",status="503"} 1'
            in metrics.text)
