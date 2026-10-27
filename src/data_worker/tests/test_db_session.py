"""Sesi yatim dari proses yang mati tidak boleh menahan siklus berikutnya.

Kontainer Railway yang dihentikan paksa meninggalkan sesi PostgreSQL yang masih
memegang kunci tabel cache. Keepalive sisi server menutup sesi itu, dan
``init_db`` tidak menunggu kuncinya tanpa batas.
"""

from __future__ import annotations

import time
import uuid

import pytest

from db_session import KEEPALIVE_OPTIONS, apply_session_defaults, session_options


def test_keepalive_ditambahkan_pada_pgoptions_kosong():
    env = {}
    apply_session_defaults(env)
    assert env["PGOPTIONS"] == "-c tcp_keepalives_idle=60 -c tcp_keepalives_interval=10 -c tcp_keepalives_count=6"


def test_pgoptions_yang_sudah_ada_dipertahankan_dan_tidak_ditimpa():
    options = session_options("-c statement_timeout=5s --tcp_keepalives_idle=300")
    assert options.startswith("-c statement_timeout=5s --tcp_keepalives_idle=300")
    assert "tcp_keepalives_idle=60" not in options
    assert "-c tcp_keepalives_interval=10" in options and "-c tcp_keepalives_count=6" in options
    # Dipanggil dua kali (mis. serve memanggil main lagi) tidak menggandakan opsi.
    assert session_options(options) == options


def test_sesi_baru_memakai_keepalive(pg_dsn, monkeypatch):
    import psycopg

    env = {}
    apply_session_defaults(env)
    monkeypatch.setenv("PGOPTIONS", env["PGOPTIONS"])
    with psycopg.connect(pg_dsn) as conn:
        if conn.info.host.startswith("/"):
            pytest.skip("keepalive TCP tidak berlaku pada soket Unix")
        for name, value in KEEPALIVE_OPTIONS.items():
            assert conn.execute(f"SHOW {name}").fetchone()[0] == value


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


def test_init_db_tidak_menunggu_kunci_sesi_yatim_tanpa_batas(cache_db):
    import psycopg
    from sqlalchemy.exc import OperationalError

    from database import init_db

    engine, dsn = cache_db
    init_db(engine)
    # Seperti COPY ke cache dari siklus yang dibunuh: transaksi terbuka yang menulis tabel cache.
    with psycopg.connect(dsn) as orphan:
        orphan.execute("LOCK TABLE pollution.inference_run IN ROW EXCLUSIVE MODE")
        started = time.monotonic()
        with pytest.raises(OperationalError, match="lock timeout"):
            init_db(engine, lock_timeout="1s")
        assert time.monotonic() - started < 15
    # Setelah sesi itu hilang, init_db kembali berhasil dan tetap idempoten.
    init_db(engine)
