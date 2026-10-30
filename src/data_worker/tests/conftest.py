"""Instans PostgreSQL + PostGIS untuk pengujian penyimpanan.

Sesuai Dokumen Desain subbab 5.2 (Tabel 5.10), operasi penyimpanan diuji
terhadap instans sungguhan, bukan stub. Urutan sumber instans:

1. ``TEST_DATABASE_URL`` bila diatur (misalnya PostgreSQL lokal atau CI);
2. Testcontainers dengan image ``postgis/postgis:16-3.4`` bila pustaka dan
   Docker tersedia;
3. selain itu, uji yang membutuhkan basis data dilewati (skip).

Setiap uji mendapat skema baru yang dihapus setelah uji selesai, sehingga
uji tidak saling bergantung tanpa perlu membuat basis data baru.
"""

from __future__ import annotations

import os
import pathlib
import sys
import uuid

import pytest

ROOT = pathlib.Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "tests"))


@pytest.fixture(autouse=True)
def _tanpa_sumber_sekunder(monkeypatch):
    """Uji tidak menghubungi sumber sekunder sungguhan; uji konektor memakai respons rekaman."""
    monkeypatch.setenv("FEATURES_SOURCES", "none")
    monkeypatch.delenv("EARTHDATA_TOKEN", raising=False)
    monkeypatch.delenv("FEATURE_STORE_DIR", raising=False)


@pytest.fixture(scope="session")
def pg_dsn():
    dsn = os.environ.get("TEST_DATABASE_URL")
    if dsn:
        yield dsn
        return
    try:
        from testcontainers.community.postgres import PostgresContainer
    except ImportError:
        try:  # testcontainers < 4.14
            from testcontainers.postgres import PostgresContainer
        except ImportError:
            pytest.skip("TEST_DATABASE_URL tidak diatur dan testcontainers tidak terpasang")
    try:
        container = PostgresContainer("postgis/postgis:16-3.4", driver=None)
        container.start()
    except Exception as exc:  # noqa: BLE001 - Docker tidak tersedia
        pytest.skip(f"Docker/Testcontainers tidak tersedia: {exc}")
    try:
        yield container.get_connection_url()
    finally:
        container.stop()


class Db:
    """Satu skema sementara pada instans uji."""

    def __init__(self, dsn: str, schema: str):
        self.dsn = dsn
        self.schema = schema

    def store(self):
        from spku.store import Store

        return Store(self.dsn, self.schema)

    def features(self):
        from feature_store.store import FeatureStore

        store = FeatureStore.connect(self.dsn, f"{self.schema}_f")
        store.ensure_schema()
        return store


@pytest.fixture
def db(pg_dsn, monkeypatch):
    import psycopg
    from psycopg import sql

    schema = f"t_{uuid.uuid4().hex[:12]}"
    # Skema features terpisah: ground_truth juga memiliki tabel fetch_log.
    monkeypatch.setenv("FEATURES_DB_SCHEMA", f"{schema}_f")
    yield Db(pg_dsn, schema)
    with psycopg.connect(pg_dsn, autocommit=True) as conn:
        for name in (schema, f"{schema}_f"):
            conn.execute(sql.SQL("DROP SCHEMA IF EXISTS {} CASCADE").format(sql.Identifier(name)))


@pytest.fixture
def cache_db(pg_dsn):
    """Basis data sementara: init_db memakai skema tetap ``pollution`` dan ``osm``."""
    import psycopg
    from sqlalchemy import create_engine
    from sqlalchemy.engine import make_url

    name = f"t_init_{uuid.uuid4().hex[:10]}"
    try:
        with psycopg.connect(pg_dsn, autocommit=True) as conn:
            conn.execute(f'CREATE DATABASE "{name}"')
    except psycopg.Error as exc:
        pytest.skip(f"tidak dapat membuat basis data uji: {exc}")
    url = make_url(pg_dsn).set(drivername="postgresql+psycopg2", database=name)
    engine = create_engine(url)
    try:
        yield engine, psycopg.conninfo.make_conninfo(pg_dsn, dbname=name)
    finally:
        engine.dispose()
        with psycopg.connect(pg_dsn, autocommit=True) as conn:
            conn.execute(f'DROP DATABASE IF EXISTS "{name}" WITH (FORCE)')
