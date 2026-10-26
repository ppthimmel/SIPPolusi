"""Konfigurasi kolektor."""

from __future__ import annotations

import dataclasses
import os
import pathlib
import re
from typing import Mapping

try:                       # Python 3.11+
    import tomllib
except ModuleNotFoundError:
    try:                   # backport, bila dipasang
        import tomli as tomllib  # type: ignore
    except ModuleNotFoundError:
        tomllib = None     # type: ignore


def _parse_simple_toml(text: str) -> dict:
    """Pembaca TOML seadanya untuk berkas konfigurasi yang datar.

    Dipakai hanya bila ``tomllib`` maupun ``tomli`` tidak tersedia, yaitu pada
    Python 3.10 ke bawah. Cukup untuk bentuk ``kunci = nilai`` dengan satu
    tingkat tabel; tidak dimaksudkan sebagai TOML yang lengkap.
    """
    result: dict = {}
    table = result
    for raw in text.splitlines():
        line = raw.split("#", 1)[0].strip()
        if not line:
            continue
        if line.startswith("[") and line.endswith("]"):
            table = result.setdefault(line[1:-1].strip(), {})
            continue
        if "=" not in line:
            continue
        key, _, value = line.partition("=")
        key, value = key.strip(), value.strip()
        if re.fullmatch(r'".*"|\'.*\'', value):
            table[key] = value[1:-1]
        elif value in ("true", "false"):
            table[key] = value == "true"
        else:
            try:
                table[key] = int(value)
            except ValueError:
                try:
                    table[key] = float(value)
                except ValueError:
                    table[key] = value
    return result


#: Variabel lingkungan yang menimpa isi berkas konfigurasi. Railway dan
#: CronJob menyuntikkan konfigurasi lewat lingkungan, bukan lewat berkas.
ENV_OVERRIDES = {
    "DATABASE_URL": ("database", str),
    "SPKU_DB_SCHEMA": ("db_schema", str),
    "SPKU_ARCHIVE_DIR": ("archive_dir", str),
    "SPKU_CONTACT": ("contact", str),
    "SPKU_DELAY_SECONDS": ("delay_seconds", float),
    "SPKU_TIMEOUT_SECONDS": ("timeout_seconds", int),
    "SPKU_RETRIES": ("retries", int),
}


@dataclasses.dataclass(slots=True)
class Config:
    #: DSN PostgreSQL. Biasanya diisi dari variabel lingkungan DATABASE_URL.
    database: str = ""
    #: Skema PostgreSQL tempat seluruh tabel ground truth SPKU.
    db_schema: str = "ground_truth"
    #: Arsip muatan mentah terkompresi. Kosong berarti tidak mengarsipkan;
    #: pada Railway isi hanya bila service memasang Railway Volume.
    archive_dir: str | None = None
    #: Pilihan. Kosongkan bila tidak ingin mencantumkan alamat apa pun;
    #: User-Agent tetap menyebut nama dan tujuan kolektor.
    contact: str = ""
    delay_seconds: float = 1.0
    timeout_seconds: int = 45
    retries: int = 3
    #: Lintasan dibatalkan bila stasiun yang berhasil diurai kurang dari
    #: proporsi ini terhadap daftar terakhir yang diketahui. Penjaga muatan
    #: kosong: pada 8 September 2026 portal mengembalikan HTTP 200 dengan nol
    #: stasiun selama lebih dari sehari.
    min_roster_ratio: float = 0.5
    #: Ambang kebasian sebelum stasiun dilaporkan bermasalah.
    stale_alert_hours: float = 6.0
    #: Proporsi stasiun basi yang memicu peringatan tingkat lintasan.
    stale_alert_ratio: float = 0.2

    @classmethod
    def load(cls, path: str | pathlib.Path | None = None,
             environ: Mapping[str, str] | None = None) -> "Config":
        """Baca berkas TOML bila ada, lalu terapkan variabel lingkungan."""
        values: dict = {}
        if path is not None:
            text = pathlib.Path(path).read_text(encoding="utf-8")
            data = tomllib.loads(text) if tomllib else _parse_simple_toml(text)
            section = data.get("collector", data)
            known = {f.name for f in dataclasses.fields(cls)}
            values = {k: v for k, v in section.items() if k in known}
        env = os.environ if environ is None else environ
        for var, (field, cast) in ENV_OVERRIDES.items():
            raw = env.get(var)
            if raw not in (None, ""):
                values[field] = cast(raw)
        return cls(**values)
