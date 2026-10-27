"""OsmDatabase maps database errors to CacheUnavailable reasons (no database needed)."""
from __future__ import annotations

from contextlib import asynccontextmanager

import asyncpg
import pytest

from osm.config import OsmSettings
from osm.db import OsmDatabase
from osm.errors import CacheUnavailable
from osm.repository import OsmRepository


class RaisingConnection:
    def __init__(self, exc: Exception):
        self.exc = exc

    async def fetchrow(self, *args):
        raise self.exc

    async def fetchval(self, *args):
        return 1


class FakePool:
    def __init__(self, conn: RaisingConnection):
        self.conn = conn

    @asynccontextmanager
    async def acquire(self, timeout=None):
        yield self.conn


def database(exc: Exception) -> OsmDatabase:
    db = OsmDatabase(OsmSettings(_env_file=None, DATABASE_URL="postgresql://unused@localhost/unused"))
    db._pool = FakePool(RaisingConnection(exc))
    return db


@pytest.mark.parametrize("exc", [
    asyncpg.UndefinedTableError('relation "osm.graph_version" does not exist'),
    asyncpg.InvalidSchemaNameError('schema "osm" does not exist'),
])
async def test_missing_cache_schema_is_cache_empty(exc):
    """A fresh database the Data Worker has not set up yet: 503 cache_empty, not 500."""
    with pytest.raises(CacheUnavailable) as err:
        await OsmRepository(database(exc)).get_active_version()
    assert err.value.reason == CacheUnavailable.CACHE_EMPTY
    assert type(exc).__name__ in err.value.message


async def test_query_timeout_keeps_its_reason():
    with pytest.raises(CacheUnavailable) as err:
        await OsmRepository(database(asyncpg.QueryCanceledError("canceling statement"))).get_active_version()
    assert err.value.reason == CacheUnavailable.QUERY_TIMEOUT


async def test_other_database_errors_are_not_hidden():
    """A missing column is a schema mismatch (a bug), not an empty cache."""
    with pytest.raises(asyncpg.UndefinedColumnError):
        await OsmRepository(database(asyncpg.UndefinedColumnError('column "built_at" does not exist'))).get_active_version()
