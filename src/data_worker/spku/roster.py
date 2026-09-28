"""Penyusunan daftar stasiun dari dua sumber yang saling memeriksa.

Halaman peta hanya memuat stasiun yang sedang melaporkan (106 dari 119 pada
13 September 2026), sehingga tidak boleh dipakai sebagai daftar induk.
Sumber yang dipakai:

* ``/lokasi-spku`` - seluruh 119 stasiun terdaftar, UUID berada pada atribut
  ``data-redirect``; seluruh baris ada di DOM, penomoran halaman dilakukan di
  sisi peramban sehingga tidak perlu ditelusuri.
* ``/sitemap.xml`` - daftar 119 URL ``/spku/<uuid>``; dipakai sebagai
  pembanding bila struktur tabel berubah.
"""

from __future__ import annotations

import re

ROW_RE = re.compile(
    r'data-redirect="/spku/([0-9a-f-]{36})"(?P<rest>.*?)</tr>', re.DOTALL | re.IGNORECASE
)
LABEL_RE = re.compile(r"<td[^>]*>\s*([A-Z]{3}\d+\s[^<]*?)\s*<", re.IGNORECASE)
SITEMAP_RE = re.compile(r"<loc>\s*([^<\s]*?/spku/([0-9a-f-]{36}))\s*</loc>", re.IGNORECASE)
TAG_RE = re.compile(r"<[^>]+>")


def parse_list_page(html: str) -> dict[str, str]:
    """Kembalikan pemetaan UUID -> label ringkas dari ``/lokasi-spku``."""
    out: dict[str, str] = {}
    for match in ROW_RE.finditer(html):
        station_uuid = match.group(1).lower()
        rest = match.group("rest")
        label_match = LABEL_RE.search(rest)
        label = label_match.group(1).strip() if label_match else ""
        if not label:
            text = TAG_RE.sub(" ", rest)
            label = " ".join(text.split())[:80]
        out[station_uuid] = label
    return out


def parse_sitemap(xml: str) -> set[str]:
    """Kembalikan himpunan UUID stasiun dari peta situs."""
    return {m.group(2).lower() for m in SITEMAP_RE.finditer(xml)}


def reconcile(from_list: dict[str, str], from_sitemap: set[str]) -> tuple[dict[str, str], list[str]]:
    """Gabungkan kedua sumber dan laporkan ketidakcocokan."""
    merged = dict(from_list)
    warnings: list[str] = []
    only_sitemap = from_sitemap - set(from_list)
    only_list = set(from_list) - from_sitemap
    for station_uuid in sorted(only_sitemap):
        merged.setdefault(station_uuid, "")
        warnings.append(f"hanya ada di peta situs: {station_uuid}")
    for station_uuid in sorted(only_list):
        warnings.append(f"hanya ada di halaman daftar: {station_uuid}")
    return merged, warnings


def initial_of(label: str) -> str | None:
    """Ambil kode ringkas stasiun (DKI01, LCS25) dari label daftar."""
    match = re.match(r"\s*([A-Z]{3}\d+)", label or "")
    return match.group(1) if match else None
