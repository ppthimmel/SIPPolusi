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


@pytest.fixture
def db(pg_dsn):
    import psycopg
    from psycopg import sql

    schema = f"t_{uuid.uuid4().hex[:12]}"
    yield Db(pg_dsn, schema)
    with psycopg.connect(pg_dsn, autocommit=True) as conn:
        conn.execute(sql.SQL("DROP SCHEMA IF EXISTS {} CASCADE").format(sql.Identifier(schema)))
