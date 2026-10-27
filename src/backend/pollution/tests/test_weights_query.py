"""EdgeWeight read without a database: lineage columns and the per-read work_mem."""
from __future__ import annotations

from contextlib import asynccontextmanager
from datetime import datetime, timezone

from osm.models import BBox, GraphVersion
from pollution import repository as repo_module
from pollution.repository import PollutionRepository

UTC = timezone.utc
W = datetime(2026, 10, 27, 9, tzinfo=UTC)
BBOX = BBox(106.8, -6.2, 106.81, -6.19)
WINDOW_ROW = dict(time_window_start=W, status="complete", pollutant="pm25,no2", coverage_ratio=1.0,
                  model_version="spatial-x", completed_at=W)


def _row(lineage: bool) -> dict:
    row = dict(edge_id=1, time_window_start=W, pm25_ugm3=20.0, no2_ugm3=40.0, exposure_index=2.9,
               confidence_score=0.3, background_source=None, estimation_source="idw", graph_version="v1",
               model_version="spatial-x", walk_allowed=True, bike_allowed=True)
    if lineage:
        row.update(written_at=W, model_metadata='{"pm25": {"source": "idw"}}', trace_run_id="run-1")
    return row


class FakeConnection:
    def __init__(self, log):
        self.log = log

    @asynccontextmanager
    async def transaction(self, **options):
        self.log.append(("begin", options))
        yield
        self.log.append(("end", None))

    async def execute(self, sql, *args):
        self.log.append(("execute", sql, args))

    async def fetchrow(self, sql, *args):
        return WINDOW_ROW

    async def fetch(self, sql, *args):
        self.log.append(("fetch", sql))
        return [_row(lineage="model_metadata" in sql)]


class FakeDb:
    def __init__(self):
        self.log: list = []

    @asynccontextmanager
    async def connection(self):
        yield FakeConnection(self.log)


class FakeOsm:
    async def get_active_version(self):
        return GraphVersion("v1", "active", None, None, None, None)


def test_query_without_lineage_differs_only_by_lineage_columns():
    full, lean = repo_module._SQL_WEIGHTS, repo_module._SQL_WEIGHTS_WITHOUT_LINEAGE
    assert "p.written_at, p.model_metadata, p.trace_run_id" in full
    assert all(column not in lean for column in repo_module._LINEAGE_COLUMNS)
    assert full.replace(", p.written_at, p.model_metadata, p.trace_run_id", "") == lean


async def test_route_read_leaves_lineage_out_and_keeps_every_other_field():
    db = FakeDb()
    repo = PollutionRepository(db, FakeOsm())
    full = (await repo.read_pollution_weight(BBOX)).weights[0]
    lean = (await repo.read_pollution_weight(BBOX, lineage=False)).weights[0]
    assert full.model_metadata == {"pm25": {"source": "idw"}} and full.trace_run_id == "run-1"
    assert (lean.written_at, lean.model_metadata, lean.trace_run_id) == (None, {}, None)
    assert (lean.edge_id, lean.pm25_ugm3, lean.exposure_index, lean.graph_version, lean.model_version) == \
        (full.edge_id, full.pm25_ugm3, full.exposure_index, full.graph_version, full.model_version)
    fetched = [entry[1] for entry in db.log if entry[0] == "fetch"]
    assert fetched == [repo_module._SQL_WEIGHTS, repo_module._SQL_WEIGHTS_WITHOUT_LINEAGE]
    # Without weights_work_mem nothing changes: no transaction, no SET.
    assert not any(entry[0] in ("begin", "execute") for entry in db.log)


async def test_work_mem_is_set_locally_around_the_read():
    db = FakeDb()
    repo = PollutionRepository(db, FakeOsm(), weights_work_mem="32MB")
    await repo.read_pollution_weight(BBOX, lineage=False)
    kinds = [entry[0] for entry in db.log]
    assert kinds == ["begin", "execute", "fetch", "end"]
    assert db.log[0][1] == {"readonly": True}
    assert db.log[1][1] == "SELECT set_config('work_mem', $1, true)" and db.log[1][2] == ("32MB",)
