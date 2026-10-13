"""Fixtures for the pollution tests.

The cache-flow tests write with Data Worker code (src/data_worker) and read with
the Backend. They need TEST_DATABASE_URL pointing at a LOCAL PostgreSQL + PostGIS
(src/backend/testdb); any other host is refused so tests never write to a shared
database. Each test gets a fresh database created from osm_cache/schema.sql.
"""
from __future__ import annotations

import os
import pathlib
import sys
import uuid
from urllib.parse import urlparse, urlunparse

import pytest

DATA_WORKER = pathlib.Path(__file__).resolve().parents[3] / "data_worker"
LOCAL_HOSTS = {"localhost", "127.0.0.1", "::1"}


def _with_database(dsn: str, name: str) -> str:
    return urlunparse(urlparse(dsn)._replace(path=f"/{name}"))


@pytest.fixture(scope="session")
def admin_dsn() -> str:
    dsn = os.environ.get("TEST_DATABASE_URL")
    if not dsn:
        pytest.skip("TEST_DATABASE_URL tidak diatur (lihat src/backend/testdb)")
    if urlparse(dsn).hostname not in LOCAL_HOSTS:
        pytest.skip("uji tulis hanya boleh ke basis data lokal")
    pytest.importorskip("psycopg")
    if not DATA_WORKER.is_dir():
        pytest.skip("src/data_worker tidak tersedia")
    if str(DATA_WORKER) not in sys.path:
        sys.path.append(str(DATA_WORKER))  # after src/backend, so backend packages win
    return dsn


@pytest.fixture
def cache_dsn(admin_dsn):
    """A new database with schemas osm and pollution, dropped after the test."""
    import psycopg

    from osm_cache import schema_sql

    name = f"cache_flow_{uuid.uuid4().hex[:10]}"
    with psycopg.connect(admin_dsn, autocommit=True) as admin:
        admin.execute(f'CREATE DATABASE "{name}"')
    dsn = _with_database(admin_dsn, name)
    with psycopg.connect(dsn, autocommit=True) as conn:
        conn.execute(schema_sql())
    yield dsn
    with psycopg.connect(admin_dsn, autocommit=True) as admin:
        admin.execute(f'DROP DATABASE IF EXISTS "{name}" WITH (FORCE)')


@pytest.fixture
def writer(cache_dsn):
    """Data Worker connection (psycopg 3, autocommit) to the test database."""
    import psycopg

    conn = psycopg.connect(cache_dsn, autocommit=True)
    yield conn
    conn.close()
