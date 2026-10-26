"""Fixtures for the public API tests: the planner's fake caches plus surface rows."""
from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from osm.app import create_app
from planner.tests.conftest import (  # noqa: F401  (fixtures and helpers)
    EMPTY,
    WINDOW,
    FakePollutionRepository,
    node_id,
    osm_repo,
    request_body,
    settings,
    weight,
)
from public_api.config import ApiSettings
from solver.config import SolverSettings


class FakeSurfaceRepository(FakePollutionRepository):
    """FakePollutionRepository plus the exposure-surface reads."""

    def __init__(self, edge_rows=(), cell_rows=(), **kw):
        super().__init__(**kw)
        self.edge_rows, self.cell_rows = list(edge_rows), list(cell_rows)
        self.surface_calls: list[tuple] = []

    async def resolve_window(self, time_window="latest"):
        if self.fail is not None:
            raise self.fail
        return WINDOW

    async def read_surface_edges(self, window, graph_version, bbox):
        self.surface_calls.append(("edges", graph_version, bbox))
        return self.edge_rows

    async def read_surface_cells(self, window, graph_version, envelope, cell_deg, index_range):
        self.surface_calls.append(("cells", graph_version, envelope, cell_deg, index_range))
        return self.cell_rows


@pytest.fixture
def pollution_repo() -> FakeSurfaceRepository:
    return FakeSurfaceRepository()


@pytest.fixture
def make_client(settings, osm_repo, pollution_repo):
    def make(pollution=None, solver=None, solver_settings=None, raise_server_exceptions=True, **api):
        app = create_app(repository=osm_repo, settings=settings, pollution_repository=pollution or pollution_repo,
                         solver=solver, solver_settings=solver_settings or SolverSettings(_env_file=None),
                         api_settings=ApiSettings(_env_file=None, **api))
        return TestClient(app, raise_server_exceptions=raise_server_exceptions)
    return make


def error(resp, status: int, code: str) -> dict:
    assert resp.status_code == status, resp.text
    assert resp.headers["content-type"].startswith("application/json")
    body = resp.json()["error"]
    assert body["code"] == code and body["message"] and body["request_id"] == resp.headers["x-request-id"]
    return body
