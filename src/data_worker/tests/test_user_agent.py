"""Uji penyusunan User-Agent.

Kontak bersifat pilihan. Yang dijaga di sini: tanpa kontak, kolektor tetap
memperkenalkan diri; dan tidak ada alamat contoh yang terlanjur terkirim ke
peladen pihak ketiga.
"""

import pathlib
import sys

import pytest

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent))

from spku.fetch import USER_AGENT_BASE, Fetcher, build_user_agent  # noqa: E402


@pytest.mark.parametrize("nilai", ["", "   ", None])
def test_tanpa_kontak_tetap_memperkenalkan_diri(nilai):
    ua = build_user_agent(nilai)
    assert ua == USER_AGENT_BASE + ")"
    assert "riset akademik" in ua
    assert "@" not in ua


@pytest.mark.parametrize(
    "nilai",
    [
        "ISI-SUREL-ANDA@students.itb.ac.id",
        "nama.anda@students.itb.ac.id",
        "ganti-dengan-surel-anda@example.ac.id",
        "uji@example.com",
        "your-email@itb.ac.id",
        "bukan alamat sama sekali",
        "masih@kosong",          # tanpa titik pada ranah
    ],
)
def test_teks_contoh_tidak_pernah_terkirim(nilai):
    assert "@" not in build_user_agent(nilai)


@pytest.mark.parametrize(
    "nilai",
    ["spku-riset@itb.ac.id", "budisiswanto@itb.ac.id", "a.b+tag@mail.itb.ac.id"],
)
def test_alamat_sungguhan_dicantumkan(nilai):
    assert f"kontak: {nilai}" in build_user_agent(nilai)


def test_nama_yang_kebetulan_memuat_potongan_kata_isian_tidak_tersaring():
    """'budisiswanto' memuat potongan 'isi'; penyaringan berbasis substring
    akan salah membuang alamat yang sah."""
    assert "budisiswanto@itb.ac.id" in build_user_agent("budisiswanto@itb.ac.id")


def test_kontak_boleh_tidak_disebut_sama_sekali():
    """Fetcher dapat dibuat tanpa argumen kontak."""
    fetcher = Fetcher()
    try:
        ua = fetcher.session.headers["User-Agent"]
    finally:
        fetcher.close()
    assert ua == USER_AGENT_BASE + ")"
