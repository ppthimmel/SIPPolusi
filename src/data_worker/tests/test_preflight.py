"""Pemeriksaan konfigurasi awal dan kode keluar Data Worker (TI-DO-02)."""

from __future__ import annotations

import json
import logging

import pytest

import main
import preflight
from log_config import configure_logging

RAHASIA = "s3cr3t-pa55word"
DSN = f"postgresql://sippolusi:{RAHASIA}@postgres.railway.internal:5432/railway"


def test_konfigurasi_railway_saat_ini_lolos():
    """Variabel yang terpasang pada service data_worker di Railway (development)."""
    env = {"DATABASE_URL": DSN, "MLFLOW_TRACKING_URI": "http://model_registry:5000",
           "RAILWAY_ENVIRONMENT_NAME": "development"}
    assert preflight.config_problems(env) == []


def test_env_example_lolos():
    env = {"DATABASE_URL": "postgresql://sippolusi:change-me@postgres:5432/sippolusi",
           "ENVIRONMENT": "DEV", "SPKU_DB_SCHEMA": "ground_truth", "SPKU_CONTACT": ""}
    assert preflight.config_problems(env) == []


def test_database_url_kosong_menyebut_cara_memperbaiki():
    (masalah,) = preflight.config_problems({})
    assert "DATABASE_URL belum diatur" in masalah
    assert "${{database.DATABASE_URL}}" in masalah


@pytest.mark.parametrize(
    "dsn, pesan",
    [
        (f"mysql://u:{RAHASIA}@h/db", "skema 'mysql'"),
        (f"postgresql://u:{RAHASIA}@h:5432/", "tidak memuat host atau nama basis data"),
        (f"postgresql://u:{RAHASIA}@h:port/db", "tidak dapat diurai"),
    ],
)
def test_dsn_salah_dijelaskan_tanpa_mengulang_kredensial(dsn, pesan):
    (masalah,) = preflight.config_problems({"DATABASE_URL": dsn})
    assert pesan in masalah
    assert RAHASIA not in masalah


def test_variabel_angka_dan_skema_diperiksa_sekaligus():
    masalah = preflight.config_problems({
        "DATABASE_URL": DSN, "SPKU_DB_SCHEMA": "Ground-Truth", "SPKU_DELAY_SECONDS": "satu",
        "SPKU_TIMEOUT_SECONDS": "0", "SPKU_RETRIES": "1.5", "LOG_LEVEL": "berisik", "ENVIRONMENT": "uat",
    })
    teks = "\n".join(masalah)
    for nama in ("SPKU_DB_SCHEMA", "SPKU_DELAY_SECONDS", "SPKU_TIMEOUT_SECONDS", "SPKU_RETRIES",
                 "LOG_LEVEL", "ENVIRONMENT"):
        assert nama in teks
    assert len(masalah) == 6


def test_modul_runtime_dapat_diimpor():
    assert preflight.import_problems() == []


def test_modul_hilang_dilaporkan_dengan_saran():
    (masalah,) = preflight.import_problems(("modul_yang_tidak_ada",))
    assert "ModuleNotFoundError" in masalah and "requirements.txt" in masalah


def test_basis_data_tak_terjangkau_dilaporkan_tanpa_kata_sandi():
    (masalah,) = preflight.database_problems(f"postgresql://u:{RAHASIA}@127.0.0.1:1/db", timeout_s=2)
    assert "tidak dapat terhubung ke basis data 127.0.0.1:1/db" in masalah
    assert RAHASIA not in masalah


def test_alamat_internal_railway_diberi_petunjuk(monkeypatch):
    import psycopg

    def gagal(*args, **kwargs):
        raise psycopg.OperationalError("could not translate host name")

    monkeypatch.setattr(psycopg, "connect", gagal)
    (masalah,) = preflight.database_problems(DSN)
    assert "DATABASE_PUBLIC_URL" in masalah


def test_cycle_berhenti_dengan_kode_2_sebelum_menyentuh_portal(monkeypatch, capsys):
    monkeypatch.delenv("DATABASE_URL", raising=False)
    monkeypatch.setenv("LOG_FORMAT", "json")

    def tidak_boleh_dipanggil(*args, **kwargs):
        raise AssertionError("siklus tidak boleh berjalan dengan konfigurasi tidak sah")

    monkeypatch.setattr(main, "schedule_acquisition", tidak_boleh_dipanggil)
    try:
        assert main.main(["cycle"]) == main.EXIT_CONFIG
        out, err = capsys.readouterr()
    finally:
        logging.getLogger().handlers.clear()
    baris = json.loads(out.strip().splitlines()[-1])
    assert baris["level"] == "error" and "DATABASE_URL belum diatur" in baris["message"]
    assert err == ""


def test_check_skip_db_hanya_gagal_karena_konfigurasi(monkeypatch, capsys):
    monkeypatch.setenv("DATABASE_URL", DSN)
    monkeypatch.setenv("LOG_FORMAT", "text")
    try:
        assert main.main(["check", "--skip-db"]) == 0
        out, _ = capsys.readouterr()
    finally:
        logging.getLogger().handlers.clear()
    assert "cek konfigurasi: ok" in out and "cek basis data: dilewati" in out
    assert RAHASIA not in out


def test_log_railway_berformat_json_di_stdout_dan_tersamarkan(capsys):
    configure_logging({"RAILWAY_ENVIRONMENT_NAME": "development", "DATABASE_URL": DSN})
    try:
        logging.getLogger("spku.store").error("gagal: %s", DSN)
        out, err = capsys.readouterr()
    finally:
        logging.getLogger().handlers.clear()
    baris = json.loads(out)
    assert baris["level"] == "error" and err == ""
    assert RAHASIA not in out and "postgres.railway.internal" in baris["message"]
