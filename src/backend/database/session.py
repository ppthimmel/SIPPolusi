import asyncio
from collections.abc import AsyncIterator

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession, async_sessionmaker, create_async_engine

_engine: AsyncEngine | None = None
_sessionmaker: async_sessionmaker[AsyncSession] | None = None


def init_engine(url: str) -> None:
    global _engine, _sessionmaker
    # Railway and Heroku-style DSNs may use either scheme name.
    for scheme in ("postgresql://", "postgres://"):
        if url.startswith(scheme):
            url = url.replace(scheme, "postgresql+asyncpg://", 1)
            break
    _engine = create_async_engine(url, pool_pre_ping=True)
    _sessionmaker = async_sessionmaker(_engine, expire_on_commit=False)


async def dispose_engine() -> None:
    global _engine, _sessionmaker
    if _engine is not None:
        await _engine.dispose()
    _engine = _sessionmaker = None


def is_configured() -> bool:
    return _engine is not None


async def ping(timeout_s: float) -> None:
    """Run SELECT 1 within ``timeout_s`` seconds; raises on failure or timeout."""
    if _engine is None:
        raise RuntimeError("Database is not configured (DATABASE_URL unset)")
    async with asyncio.timeout(timeout_s):
        async with _engine.connect() as conn:
            await conn.execute(text("SELECT 1"))


async def get_session() -> AsyncIterator[AsyncSession]:
    """FastAPI dependency. The Backend only reads the cache schemas (PNF-05)."""
    if _sessionmaker is None:
        raise RuntimeError("Database is not configured (DATABASE_URL unset or unreachable)")
    async with _sessionmaker() as session:
        yield session
