"""/v1/pollution endpoints with fake repositories: status codes, reason, validation."""
from __future__ import annotations

from datetime import datetime, timezone

import pytest
from fastapi.testclient import TestClient

from osm.app import create_app
from osm.config import OsmSettings
from osm.errors import CacheUnavailable, NotFound
from osm.tests.conftest import FakeRepository
from pollution.models import EdgeWeight, PollutionRead, TimeWindow, WindowInfo

UTC = timezone.utc
START = datetime(2026, 10, 13, 5, tzinfo=UTC)
BBOX = "106.825,-6.177,106.829,-6.173"
WINDOW = WindowInfo(START, "complete", "pm25,no2", 0.98, "idw-p2-k8", datetime(2026, 10, 13, 6, 40, tzinfo=UTC))


class FakePollutionRepository:
    def __init__(self):
        self.fail: Exception | None = None
        self.calls: list[tuple] = []
        self.windows = [WINDOW]

    def staleness(self, window):
        return False, 1800.0

    async def list_windows(self, limit=24):
        return self.windows[:limit]

    async def latest_complete_window(self):
        return next((w for w in self.windows if w.status == "complete"), None)

    async def read_pollution_weight(self, bbox, time_window="latest", *, mode=None, graph_version="active"):
        self.calls.append((bbox.as_list(), time_window, mode, graph_version))
        if self.fail is not None:
            raise self.fail
        weight = EdgeWeight(1, START, 20.0, 40.0, 1.47, 0.3, None, "idw", "jakarta-20261005", "idw-p2-k8")
        return PollutionRead(WINDOW, graph_version, [weight], False, 1800.0, {"other_graph_version": 2})


@pytest.fixture
def repo():
    return FakePollutionRepository()


@pytest.fixture
def client(repo):
    settings = OsmSettings(_env_file=None, DATABASE_URL="postgresql://unused@localhost/unused")
    app = create_app(repository=FakeRepository(), settings=settings, pollution_repository=repo)
    with TestClient(app) as c:
        yield c


def assert_error(resp, status, code, reason=None):
    assert resp.status_code == status, resp.text
    error = resp.json()["error"]
    assert error["code"] == code and error["request_id"] == resp.headers["X-Request-ID"]
    assert error.get("reason") == reason


def test_edge_weights(client, repo):
    resp = client.get("/v1/pollution/edge-weights", params={"bbox": BBOX})
    assert resp.status_code == 200
    body = resp.json()
    assert body["time_window_start"] == "2026-10-13T05:00:00Z"
    assert body["time_window_end"] == "2026-10-13T06:00:00Z"
    assert body["count"] == 1 and body["data_stale"] is False and body["age_minutes"] == 30.0
    assert body["excluded"] == {"other_graph_version": 2}
    assert body["weights"][0]["exposure_index"] == pytest.approx(1.47)
    assert repo.calls == [([106.825, -6.177, 106.829, -6.173], "latest", None, "active")]


def test_edge_weights_passes_window_mode_and_version(client, repo):
    params = {"bbox": BBOX, "time_window_start": "2026-10-13T12:00:00+07:00", "mode": "bike", "graph_version": "all"}
    assert client.get("/v1/pollution/edge-weights", params=params).status_code == 200
    _, window, mode, version = repo.calls[0]
    assert window == TimeWindow(START) and mode.value == "bike" and version is None


@pytest.mark.parametrize("value", ["2026-10-13T05:30:00Z", "2026-10-13T05:00:00", "kemarin"])
def test_bad_time_window_is_400(client, repo, value):
    resp = client.get("/v1/pollution/edge-weights", params={"bbox": BBOX, "time_window_start": value})
    assert_error(resp, 400, "invalid_request")
    assert repo.calls == []


def test_bad_bbox_is_400(client):
    assert_error(client.get("/v1/pollution/edge-weights", params={"bbox": "106.68,-6.38,106.98,-6.08"}),
                 400, "invalid_request")


@pytest.mark.parametrize("reason", [CacheUnavailable.CACHE_EMPTY, CacheUnavailable.DATABASE_UNAVAILABLE,
                                    CacheUnavailable.QUERY_TIMEOUT])
def test_cache_unavailable_reasons(client, repo, reason):
    repo.fail = CacheUnavailable("tidak dapat dibaca", reason)
    assert_error(client.get("/v1/pollution/edge-weights", params={"bbox": BBOX}), 503, "cache_unavailable", reason)


def test_unknown_window_is_404(client, repo):
    repo.fail = NotFound("Time window tidak ada")
    params = {"bbox": BBOX, "time_window_start": "2020-01-01T00:00:00Z"}
    assert_error(client.get("/v1/pollution/edge-weights", params=params), 404, "not_found")


def test_windows(client):
    body = client.get("/v1/pollution/windows").json()
    assert body["latest_complete"]["model_version"] == "idw-p2-k8"
    assert body["data_stale"] is False and len(body["windows"]) == 1


def test_windows_when_cache_is_empty(client, repo):
    repo.windows = []
    body = client.get("/v1/pollution/windows").json()
    assert body == {"latest_complete": None, "data_stale": None, "age_minutes": None, "windows": []}


def test_health_reports_pollution_window(client):
    body = client.get("/health").json()
    assert body["pollution_time_window"] == "2026-10-13T05:00:00Z"
    assert body["pollution_data_stale"] is False
