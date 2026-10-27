"""Pengaturan sesi PostgreSQL untuk seluruh koneksi Data Worker.

Kontainer yang dihentikan paksa (kehabisan memori, redeploy) di Railway hilang
tanpa menutup koneksi TCP-nya. PostgreSQL baru menyadari klien itu hilang lewat
TCP keepalive, yang bawaannya baru dikirim setelah 2 jam tanpa lalu lintas.
Selama itu sesi yatim tetap memegang kunci tabel dan baris transaksinya, dan
siklus berikutnya menunggu kunci itu tanpa batas.

``apply_session_defaults()`` menambahkan keepalive sisi server ke ``PGOPTIONS``
sehingga sesi yatim ditutup (dan transaksinya dibatalkan) sekitar
``idle + interval × count`` = 2 menit setelah klien hilang. ``PGOPTIONS`` dibaca
libpq, jadi berlaku untuk psycopg, psycopg2 (SQLAlchemy), dan koneksi pustaka
lain dalam proses yang sama.
"""

from __future__ import annotations

import os
import re

#: Detik tanpa lalu lintas sebelum probe pertama, jeda antarprobe, dan jumlah probe tak terjawab.
KEEPALIVE_OPTIONS = {
    "tcp_keepalives_idle": "60",
    "tcp_keepalives_interval": "10",
    "tcp_keepalives_count": "6",
}


def session_options(existing: str = "") -> str:
    """``existing`` (isi ``PGOPTIONS``) ditambah keepalive yang belum diatur di dalamnya."""
    present = set(re.findall(r"(?:-c\s*|--)([a-z_]+)\s*=", existing))
    added = [f"-c {name}={value}" for name, value in KEEPALIVE_OPTIONS.items() if name not in present]
    return " ".join(part for part in (existing.strip(), *added) if part)


def apply_session_defaults(environ=None) -> None:
    """Pasang keepalive pada ``PGOPTIONS`` sebelum koneksi pertama; nilai yang sudah diatur tidak diubah."""
    env = os.environ if environ is None else environ
    env["PGOPTIONS"] = session_options(env.get("PGOPTIONS", ""))
