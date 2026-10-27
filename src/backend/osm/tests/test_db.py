"""OsmDatabase error mapping, with a fake pool (no database needed)."""
from __future__ import annotations

from contextlib import asynccontextmanager

import asyncpg
import pytest

from osm.config import OsmSettings
from osm.db import OsmDatabase
from osm.errors import CacheUnavailable


class _FailingConn:
    def __init__(self, exc: Exception):
        self._exc = exc

    async def fetchrow(self, *args, **kwargs):
        raise self._exc


class _FakePool:
    def __init__(self, exc: Exception):
        self._exc = exc

    @asynccontextmanager
    async def acquire(self, timeout=None):
        yield _FailingConn(self._exc)


def _database(exc: Exception) -> OsmDatabase:
    db = OsmDatabase(OsmSettings(DATABASE_URL="postgresql://u:p@db:5432/x"))
    db._pool = _FakePool(exc)
    return db


@pytest.mark.parametrize("exc", [
    asyncpg.UndefinedTableError('relation "osm.graph_version" does not exist'),
    asyncpg.InvalidSchemaNameError('schema "pollution" does not exist'),
])
async def test_missing_schema_or_table_is_cache_empty(exc, caplog):
    # A fresh database before the first import: 503 cache_empty, not 500.
    with pytest.raises(CacheUnavailable) as info:
        async with _database(exc).connection() as conn:
            await conn.fetchrow("SELECT 1")
    assert info.value.status_code == 503
    assert info.value.reason == CacheUnavailable.CACHE_EMPTY
    # The original error stays visible in the log.
    assert any(r.levelname == "WARNING" and str(exc) in r.getMessage() for r in caplog.records)


@pytest.mark.parametrize("exc", [
    asyncpg.UndefinedTableError('relation "osm_graph_version" does not exist'),  # typo, no schema
    asyncpg.UndefinedTableError('relation "public.road_edge" does not exist'),
    asyncpg.InvalidSchemaNameError('schema "osmm" does not exist'),
])
async def test_missing_object_outside_cache_schemas_stays_an_error(exc):
    # Only the osm and pollution schemas mean "cache not filled yet".
    with pytest.raises(type(exc)):
        async with _database(exc).connection() as conn:
            await conn.fetchrow("SELECT 1")


async def test_other_postgres_errors_are_not_masked():
    with pytest.raises(asyncpg.UndefinedColumnError):
        async with _database(asyncpg.UndefinedColumnError("column x does not exist")).connection() as conn:
            await conn.fetchrow("SELECT x")
