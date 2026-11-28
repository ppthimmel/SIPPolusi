"""Heatmap queries cannot starve route planning: a few slots, a short wait, their own statement_timeout."""
from __future__ import annotations

import asyncio
import time
from contextlib import asynccontextmanager
from datetime import datetime, timezone

import pytest

from osm.errors import CacheUnavailable
from osm.models import BBox
from pollution import repository as repo_module
from pollution.models import WindowInfo
from pollution.repository import PollutionRepository

UTC = timezone.utc
W = datetime(2026, 10, 27, 9, tzinfo=UTC)
BBOX = BBox(106.8, -6.2, 106.81, -6.19)
WINDOW = WindowInfo(time_window_start=W, status="complete", pollutant="pm25,no2", coverage_ratio=1.0,
                    model_version="spatial-x", completed_at=W)


class FakeConnection:
    def __init__(self, db):
        self.db = db

    @asynccontextmanager
    async def transaction(self, **options):
        self.db.log.append(("begin", options))
        yield
        self.db.log.append(("end", None))

    async def execute(self, sql, *args):
        self.db.log.append(("execute", sql, args))

    async def fetch(self, sql, *args):
        self.db.log.append(("fetch", sql))
        if self.db.release is not None:
            await self.db.release.wait()
        if self.db.fail:
            raise CacheUnavailable("statement_timeout", CacheUnavailable.QUERY_TIMEOUT)
        return [{"edge_id": 1}]


class FakeDb:
    def __init__(self, release: asyncio.Event | None = None, fail: bool = False):
        self.log: list = []
        self.release = release
        self.fail = fail
        self.acquired = 0

    @asynccontextmanager
    async def connection(self):
        self.acquired += 1
        yield FakeConnection(self)


def _repo(db, **kw):
    return PollutionRepository(db, osm=None, **kw)


async def test_surface_query_runs_under_its_own_local_statement_timeout():
    db = FakeDb()
    rows = await _repo(db, surface_statement_timeout_ms=4321).read_surface_edges(WINDOW, "v1", BBOX)
    assert rows == [{"edge_id": 1}]
    assert [e[0] for e in db.log] == ["begin", "execute", "fetch", "end"]
    assert db.log[0][1] == {"readonly": True}
    assert db.log[1][1] == "SELECT set_config('statement_timeout', $1, true)" and db.log[1][2] == ("4321",)
    assert db.log[2][1] == repo_module._SQL_SURFACE_EDGES


async def test_cells_use_the_same_limited_path():
    db = FakeDb()
    await _repo(db).read_surface_cells(WINDOW, "v1", BBOX, (0.01, 0.01), (0, 1, 0, 1))
    assert [e[0] for e in db.log] == ["begin", "execute", "fetch", "end"]
    assert db.log[2][1] == repo_module._SQL_SURFACE_CELLS


async def test_extra_surface_requests_are_refused_without_taking_a_connection():
    release = asyncio.Event()
    db = FakeDb(release)
    repo = _repo(db, surface_max_concurrency=2, surface_wait_s=0.05)
    running = [asyncio.create_task(repo.read_surface_edges(WINDOW, "v1", BBOX)) for _ in range(2)]
    await asyncio.sleep(0.01)
    started = time.perf_counter()
    with pytest.raises(CacheUnavailable) as exc:
        await repo.read_surface_edges(WINDOW, "v1", BBOX)
    assert exc.value.reason == CacheUnavailable.SURFACE_BUSY
    assert time.perf_counter() - started < 0.5
    assert db.acquired == 2  # the refused request never reached the pool
    release.set()
    assert [len(r) for r in await asyncio.gather(*running)] == [1, 1]


async def test_a_waiting_request_gets_the_slot_once_one_frees():
    release = asyncio.Event()
    db = FakeDb(release)
    repo = _repo(db, surface_max_concurrency=1, surface_wait_s=1.0)
    first = asyncio.create_task(repo.read_surface_edges(WINDOW, "v1", BBOX))
    await asyncio.sleep(0.01)
    second = asyncio.create_task(repo.read_surface_edges(WINDOW, "v1", BBOX))
    await asyncio.sleep(0.01)
    release.set()
    assert len(await first) == 1 and len(await second) == 1


async def test_a_failed_query_gives_its_slot_back():
    repo = _repo(FakeDb(fail=True), surface_max_concurrency=1, surface_wait_s=0.05)
    for _ in range(3):  # would be surface_busy from the second call if the slot leaked
        with pytest.raises(CacheUnavailable) as exc:
            await repo.read_surface_edges(WINDOW, "v1", BBOX)
        assert exc.value.reason == CacheUnavailable.QUERY_TIMEOUT
