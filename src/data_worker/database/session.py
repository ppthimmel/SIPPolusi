import os

from sqlalchemy import Engine, create_engine, text
from sqlalchemy.orm import Session, sessionmaker

from .models import SCHEMAS, OsmBase, PollutionBase


def get_engine() -> Engine:
    url = os.getenv("DATABASE_URL")
    if not url:
        raise RuntimeError("DATABASE_URL environment variable is not set")
    if url.startswith("postgresql://"):
        url = url.replace("postgresql://", "postgresql+psycopg2://", 1)
    return create_engine(url, pool_pre_ping=True)


def get_sessionmaker(engine: Engine | None = None) -> sessionmaker[Session]:
    return sessionmaker(engine or get_engine(), expire_on_commit=False)


#: Longest wait for a table lock during init_db before giving up.
DDL_LOCK_TIMEOUT = "30s"


def init_db(engine: Engine | None = None, lock_timeout: str = DDL_LOCK_TIMEOUT) -> None:
    """Create the PostGIS extension, schemas and tables if missing. Idempotent.

    The Data Worker owns the cache schemas, so it is the only service that runs this.
    It runs at the start of every cycle, and the migration's ALTER TABLE and CREATE
    INDEX need locks that conflict with open writes and reads on the cache tables.
    Each statement therefore waits at most ``lock_timeout``: a session left by a
    killed run, or a long cache read, makes this raise instead of stalling the cycle
    (and every cache read queued behind the ALTER) indefinitely.
    """
    engine = engine or get_engine()
    with engine.begin() as conn:
        _limit_lock_wait(conn, lock_timeout)
        conn.execute(text("CREATE EXTENSION IF NOT EXISTS postgis"))
        for schema in SCHEMAS:
            conn.execute(text(f'CREATE SCHEMA IF NOT EXISTS "{schema}"'))
    with engine.begin() as conn:
        _limit_lock_wait(conn, lock_timeout)
        PollutionBase.metadata.create_all(conn)
        OsmBase.metadata.create_all(conn)
    from pathlib import Path
    with engine.begin() as conn:
        _limit_lock_wait(conn, lock_timeout)
        for statement in Path(__file__).with_name("spatial_migration.sql").read_text().split(";"):
            if statement.strip():
                conn.execute(text(statement))


def _limit_lock_wait(conn, lock_timeout: str) -> None:
    conn.execute(text("SELECT set_config('lock_timeout', :value, true)"), {"value": lock_timeout})
