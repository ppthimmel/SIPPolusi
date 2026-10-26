"""Pemeriksaan konfigurasi awal Data Worker.

``python main.py check`` menjalankan ketiganya; ``cycle`` dan ``serve``
menjalankan pemeriksaan konfigurasi lebih dulu dan berhenti dengan kode 2
bila gagal, sebelum menyentuh portal atau basis data. Setiap masalah
menyebut variabel dan cara memperbaikinya tanpa mengulang nilai yang dapat
memuat kredensial.
"""

from __future__ import annotations

import importlib
import os
import re
from typing import Mapping
from urllib.parse import urlsplit

ENVIRONMENTS = ("development", "staging", "production")
_ALIASES = {"dev": "development", "local": "development", "stage": "staging", "prod": "production"}
_LOG_LEVELS = ("DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL")
_SCHEMA_NAME_RE = re.compile(r"^[a-z_][a-z0-9_]*$")
_DSN_EXAMPLE = "postgresql://USER:PASSWORD@HOST:5432/DBNAME"

#: Modul yang baru diimpor di tengah siklus. Dependensi yang hilang harus
#: ketahuan saat build/cek, bukan pada langkah ke-8 sebuah cron.
RUNTIME_MODULES = ("acquisition", "database", "spatial_model.inference", "spku.store", "psycopg", "psycopg2")

#: (variabel, tipe, nilai minimum)
_NUMERIC = (
    ("SPKU_DELAY_SECONDS", float, 0),
    ("SPKU_TIMEOUT_SECONDS", int, 1),
    ("SPKU_RETRIES", int, 0),
)


def _dsn_problem(dsn: str) -> str | None:
    try:
        parts = urlsplit(dsn)
        parts.port  # noqa: B018 - ValueError bila port bukan angka
    except ValueError:
        return f"DATABASE_URL tidak dapat diurai; bentuk yang diharapkan {_DSN_EXAMPLE}."
    if parts.scheme not in ("postgresql", "postgres"):
        return (
            f"DATABASE_URL memakai skema {parts.scheme!r}; bentuk yang diharapkan {_DSN_EXAMPLE} "
            "(di Railway: referensi ${{database.DATABASE_URL}})."
        )
    if not parts.hostname or parts.path in ("", "/"):
        return f"DATABASE_URL tidak memuat host atau nama basis data; bentuk yang diharapkan {_DSN_EXAMPLE}."
    return None


def config_problems(environ: Mapping[str, str] | None = None) -> list[str]:
    env = os.environ if environ is None else environ
    problems: list[str] = []

    dsn = env.get("DATABASE_URL", "").strip()
    if not dsn:
        problems.append(
            "DATABASE_URL belum diatur. Lokal: salin src/data_worker/.env.example ke .env; "
            "Railway: isi dengan referensi ${{database.DATABASE_URL}} pada Variables service data_worker."
        )
    else:
        problem = _dsn_problem(dsn)
        if problem:
            problems.append(problem)

    schema = env.get("SPKU_DB_SCHEMA", "").strip()
    if schema and not _SCHEMA_NAME_RE.match(schema):
        problems.append(f"SPKU_DB_SCHEMA={schema!r} bukan nama skema yang sah (huruf kecil, angka, garis bawah).")

    for name, cast, minimum in _NUMERIC:
        raw = env.get(name, "").strip()
        if not raw:
            continue
        try:
            value = cast(raw)
        except ValueError:
            problems.append(f"{name}={raw!r} harus berupa angka{' bulat' if cast is int else ''}.")
            continue
        if value < minimum:
            problems.append(f"{name}={raw!r} harus >= {minimum}.")

    level = env.get("LOG_LEVEL", "").strip().upper()
    if level and level not in _LOG_LEVELS:
        problems.append(f"LOG_LEVEL={level!r} tidak dikenal; pakai salah satu dari {', '.join(_LOG_LEVELS)}.")

    environment = env.get("ENVIRONMENT", "").strip().lower()
    if environment and _ALIASES.get(environment, environment) not in ENVIRONMENTS:
        problems.append(f"ENVIRONMENT={environment!r} tidak dikenal; pakai salah satu dari {', '.join(ENVIRONMENTS)}.")
    return problems


def import_problems(modules: tuple[str, ...] = RUNTIME_MODULES) -> list[str]:
    problems = []
    for name in modules:
        try:
            importlib.import_module(name)
        except Exception as exc:  # noqa: BLE001 - dilaporkan, bukan dilempar
            problems.append(
                f"modul {name!r} gagal diimpor ({type(exc).__name__}: {exc}); "
                "periksa requirements.txt lalu build ulang image."
            )
    return problems


def database_problems(dsn: str, timeout_s: int = 10) -> list[str]:
    """Koneksi, lalu ketersediaan PostGIS. Tidak menulis apa pun."""
    import psycopg

    parts = urlsplit(dsn)
    target = f"{parts.hostname}:{parts.port or 5432}{parts.path}"
    try:
        with psycopg.connect(dsn, connect_timeout=timeout_s) as conn:
            row = conn.execute("SELECT 1 FROM pg_available_extensions WHERE name = 'postgis'").fetchone()
    except Exception as exc:  # noqa: BLE001
        hint = ""
        if parts.hostname and parts.hostname.endswith(".railway.internal"):
            hint = (
                " Alamat *.railway.internal hanya terjangkau dari dalam Railway; "
                "dari luar pakai DATABASE_PUBLIC_URL."
            )
        return [f"tidak dapat terhubung ke basis data {target} ({type(exc).__name__}: {exc}).{hint}"]
    if row is None:
        return [f"basis data {target} tidak menyediakan ekstensi PostGIS; pakai image/template PostGIS."]
    return []
