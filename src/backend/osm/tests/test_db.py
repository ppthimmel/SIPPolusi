"""OsmDatabase maps database errors to CacheUnavailable reasons, with a fake pool (no database needed)."""
from __future__ import annotations

from contextlib import asynccontextmanager

import asyncpg
import pytest

from osm.config import OsmSettings
from osm.db import OsmDatabase
from osm.errors import CacheUnavailable
from osm.repository import OsmRepository


class _FakeConn:
    """Fails the query like Postgres would; answers the to_regnamespace check from `schemas`."""

    def __init__(self, exc: Exception, schemas: set[str], check_error: Exception | None):
        self._exc = exc
        self._schemas = schemas
        self._check_error = check_error
        self.checked: list[str] = []

    async def fetchrow(self, *args, **kwargs):
        raise self._exc

    async def fetchval(self, query, *args):
        assert "to_regnamespace" in query, query
        if self._check_error is not None:
            raise self._check_error
        self.checked.append(args[0])
        return args[0] in self._schemas


class _FakePool:
    def __init__(self, conn: _FakeConn):
        self.conn = conn

    @asynccontextmanager
    async def acquire(self, timeout=None):
        yield self.conn


def _database(exc: Exception, schemas: set[str] = frozenset(), check_error: Exception | None = None):
    """(OsmDatabase, connection) whose queries fail with exc; `schemas` exist in the fake database."""
    db = OsmDatabase(OsmSettings(_env_file=None, DATABASE_URL="postgresql://u:p@db:5432/x"))
    conn = _FakeConn(exc, set(schemas), check_error)
    db._pool = _FakePool(conn)
    return db, conn


async def _query(db: OsmDatabase) -> None:
    async with db.connection() as conn:
        await conn.fetchrow("SELECT 1")


@pytest.mark.parametrize("exc, schemas", [
    (asyncpg.UndefinedTableError('relation "osm.graph_version" does not exist'), set()),
    (asyncpg.UndefinedTableError('relation "pollution.pollution_window" does not exist'), {"osm"}),
    (asyncpg.InvalidSchemaNameError('schema "pollution" does not exist'), {"osm"}),
])
async def test_absent_cache_schema_is_cache_empty(exc, schemas, caplog):
    # A fresh database before the Data Worker's first cycle: 503 cache_empty, not 500.
    db, conn = _database(exc, schemas)
    with pytest.raises(CacheUnavailable) as info:
        await _query(db)
    assert info.value.status_code == 503
    assert info.value.reason == CacheUnavailable.CACHE_EMPTY
    assert str(exc) in info.value.message  # the cause stays visible
    assert any(r.levelname == "WARNING" and str(exc) in r.getMessage() for r in caplog.records)


async def test_repository_reports_cache_empty_before_the_worker_ran():
    db, _ = _database(asyncpg.UndefinedTableError('relation "osm.graph_version" does not exist'))
    with pytest.raises(CacheUnavailable) as info:
        await OsmRepository(db).get_active_version()
    assert info.value.reason == CacheUnavailable.CACHE_EMPTY


@pytest.mark.parametrize("exc, schemas", [
    # The schema exists but a table is missing: renamed table, partial migration.
    (asyncpg.UndefinedTableError('relation "pollution.pollution_window" does not exist'), {"osm", "pollution"}),
    (asyncpg.UndefinedTableError('relation "osm.graph_version" does not exist'), {"osm"}),
])
async def test_missing_table_in_an_existing_schema_is_not_cache_empty(exc, schemas):
    # A deployment bug: it must stay a 500 with the original error, not look like an empty cache.
    db, conn = _database(exc, schemas)
    with pytest.raises(asyncpg.UndefinedTableError) as info:
        await _query(db)
    assert info.value is exc
    assert conn.checked == [("osm" if "osm." in str(exc) else "pollution")]


@pytest.mark.parametrize("exc", [
    asyncpg.UndefinedTableError('relation "osm_graph_version" does not exist'),  # typo, no schema
    asyncpg.UndefinedTableError('relation "public.road_edge" does not exist'),
    asyncpg.InvalidSchemaNameError('schema "osmm" does not exist'),
])
async def test_missing_object_outside_cache_schemas_stays_an_error(exc):
    db, conn = _database(exc)
    with pytest.raises(type(exc)):
        await _query(db)
    assert conn.checked == []  # not even looked up


async def test_failed_schema_check_keeps_the_original_error():
    exc = asyncpg.UndefinedTableError('relation "osm.graph_version" does not exist')
    db, _ = _database(exc, check_error=ConnectionResetError("connection lost"))
    with pytest.raises(asyncpg.UndefinedTableError) as info:
        await _query(db)
    assert info.value is exc


async def test_query_timeout_keeps_its_reason():
    db, _ = _database(asyncpg.QueryCanceledError("canceling statement due to statement timeout"))
    with pytest.raises(CacheUnavailable) as info:
        await _query(db)
    assert info.value.reason == CacheUnavailable.QUERY_TIMEOUT


async def test_other_postgres_errors_are_not_masked():
    db, _ = _database(asyncpg.UndefinedColumnError('column "built_at" does not exist'))
    with pytest.raises(asyncpg.UndefinedColumnError):
        await _query(db)
