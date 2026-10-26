"""Fixtures for planner tests: the 5 x 5 grid from osm tests and a fake pollution cache."""
from __future__ import annotations

from datetime import datetime, timezone

import pytest

from osm.config import OsmSettings
from osm.errors import CacheUnavailable
from osm.tests.conftest import ORIGIN_LAT, ORIGIN_LON, STEP_DEG, VERSION, FakeRepository, node_id  # noqa: F401
from pollution.models import EdgeWeight, PollutionRead, WindowInfo

UTC = timezone.utc
WINDOW_START = datetime(2026, 10, 13, 5, tzinfo=UTC)
WINDOW = WindowInfo(WINDOW_START, "complete", "pm25,no2", 0.95, "idw-p2-k8", datetime(2026, 10, 13, 6, 40, tzinfo=UTC))


def weight(edge_id, exposure=1.0, pm25=20.0, no2=40.0, confidence=0.3, source="idw", graph_version=VERSION):
    return EdgeWeight(edge_id, WINDOW_START, pm25, no2, exposure, confidence, None, source, graph_version, "idw-p2-k8")


def pollution_read(weights, stale=False, age_s=1800.0, excluded=None):
    return PollutionRead(WINDOW, VERSION, list(weights), stale, age_s, dict(excluded or {}))


class FakePollutionRepository:
    def __init__(self, weights=None, fail: Exception | None = None, stale=False):
        self.weights = list(weights) if weights is not None else [weight(i) for i in range(1, 41)]
        self.fail = fail
        self.stale = stale
        self.calls: list[dict] = []

    async def read_pollution_weight(self, bbox, time_window="latest", *, mode=None, graph_version="active"):
        self.calls.append({"bbox": bbox, "time_window": time_window, "mode": mode, "graph_version": graph_version})
        if self.fail is not None:
            raise self.fail
        return pollution_read(self.weights, stale=self.stale)

    async def latest_complete_window(self):
        return WINDOW

    def staleness(self, window):
        return self.stale, 1800.0


@pytest.fixture
def settings() -> OsmSettings:
    return OsmSettings(_env_file=None, DATABASE_URL="postgresql://unused@localhost/unused")


@pytest.fixture
def osm_repo() -> FakeRepository:
    return FakeRepository()


@pytest.fixture
def pollution_repo() -> FakePollutionRepository:
    return FakePollutionRepository()


def request_body(origin=(0, 0), destination=(4, 4), mode="walk", alpha=0.5, beta=0.5, pollutant="pm25"):
    """RouteRequest between grid nodes (row, col)."""
    def point(rc):
        r, c = rc
        return {"lat": ORIGIN_LAT + r * STEP_DEG, "lon": ORIGIN_LON + c * STEP_DEG}
    return {"origin": point(origin), "destination": point(destination), "mode": mode, "pollutant": pollutant,
            "preference": {"alpha": alpha, "beta": beta}}


EMPTY = CacheUnavailable("Spatial Pollution Cache DB belum memiliki time window berstatus complete.",
                         CacheUnavailable.CACHE_EMPTY)
