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


def init_db(engine: Engine | None = None) -> None:
    """Create the PostGIS extension, schemas and tables if missing. Idempotent.

    The Data Worker owns the cache schemas, so it is the only service that runs this.
    """
    engine = engine or get_engine()
    with engine.begin() as conn:
        conn.execute(text("CREATE EXTENSION IF NOT EXISTS postgis"))
        for schema in SCHEMAS:
            conn.execute(text(f'CREATE SCHEMA IF NOT EXISTS "{schema}"'))
    PollutionBase.metadata.create_all(engine)
    OsmBase.metadata.create_all(engine)
