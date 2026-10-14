"""asyncpg connection pool for the OSM Cache DB.

Every session is opened with default_transaction_read_only = on and the
statement_timeout from Tabel 3.15. That stops this code from writing by accident,
but it is only a session default, not a security boundary: write protection needs
a database role with SELECT only (backend_reader). Connection failures and
timeouts surface as CacheUnavailable (503 cache_unavailable).
"""
from __future__ import annotations

import asyncio
import json
import logging
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

import asyncpg

from .config import OsmSettings
from .errors import CacheUnavailable

logger = logging.getLogger("osm")

APPLICATION_NAME = "sippolusi-backend-osm"

# Errors meaning "the cache cannot be read right now", as opposed to bugs.
_UNAVAILABLE_ERRORS = (
    OSError,
    TimeoutError,
    asyncio.TimeoutError,
    asyncpg.InterfaceError,
    asyncpg.PostgresConnectionError,
    asyncpg.InsufficientResourcesError,
    asyncpg.OperatorInterventionError,  # includes CannotConnectNow, AdminShutdown
)


async def _init_connection(conn: asyncpg.Connection) -> None:
    for typ in ("json", "jsonb"):
        await conn.set_type_codec(typ, encoder=json.dumps, decoder=json.loads, schema="pg_catalog")


class OsmDatabase:
    def __init__(self, settings: OsmSettings):
        self._settings = settings
        self._pool: asyncpg.Pool | None = None
        self._lock = asyncio.Lock()

    @property
    def is_connected(self) -> bool:
        return self._pool is not None

    async def start(self) -> None:
        """Open the pool. A failure is logged, not raised, so the API still
        starts and answers 503 until the database is reachable."""
        try:
            await self._ensure_pool()
        except CacheUnavailable as exc:
            logger.warning("OSM Cache DB not reachable at startup: %s", exc.message)

    async def close(self) -> None:
        if self._pool is not None:
            await self._pool.close()
            self._pool = None

    async def _ensure_pool(self) -> asyncpg.Pool:
        if self._pool is not None:
            return self._pool
        async with self._lock:
            if self._pool is None:
                s = self._settings
                try:
                    self._pool = await asyncpg.create_pool(
                        dsn=s.database_url,
                        min_size=min(s.pool_min_size, s.pool_max_size),
                        max_size=s.pool_max_size,
                        timeout=s.connect_timeout_s,
                        command_timeout=s.command_timeout_s,
                        server_settings={
                            "statement_timeout": str(s.statement_timeout_ms),
                            "default_transaction_read_only": "on",
                            "application_name": APPLICATION_NAME,
                            "TimeZone": "UTC",
                        },
                        init=_init_connection,
                    )
                except (*_UNAVAILABLE_ERRORS, asyncpg.PostgresError) as exc:
                    raise CacheUnavailable(f"Basis data cache tidak dapat dihubungi ({type(exc).__name__}).",
                                           CacheUnavailable.DATABASE_UNAVAILABLE) from exc
                logger.info("OSM Cache DB pool opened")
        return self._pool

    @asynccontextmanager
    async def connection(self) -> AsyncIterator[asyncpg.Connection]:
        pool = await self._ensure_pool()
        try:
            async with pool.acquire(timeout=self._settings.connect_timeout_s) as conn:
                yield conn
        except asyncpg.QueryCanceledError as exc:
            raise CacheUnavailable("Kueri basis data cache melewati statement_timeout.",
                                   CacheUnavailable.QUERY_TIMEOUT) from exc
        except (TimeoutError, asyncio.TimeoutError) as exc:
            raise CacheUnavailable("Kueri basis data cache melewati batas waktu klien (OSM_COMMAND_TIMEOUT_S).",
                                   CacheUnavailable.QUERY_TIMEOUT) from exc
        except _UNAVAILABLE_ERRORS as exc:
            raise CacheUnavailable(f"Basis data cache tidak tersedia ({type(exc).__name__}).",
                                   CacheUnavailable.DATABASE_UNAVAILABLE) from exc

    async def ping(self) -> bool:
        try:
            async with self.connection() as conn:
                return await conn.fetchval("SELECT 1") == 1
        except CacheUnavailable:
            return False
