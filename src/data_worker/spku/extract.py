"""Ekstraksi objek JSON yang ditanam langsung pada HTML portal Udara Jakarta.

Portal tidak menyediakan API JSON publik (lihat udara-collector/docs/TEMUAN_DATA.md). Seluruh
muatan dirender di sisi peladen sebagai penetapan variabel JavaScript, misalnya::

    window.__SPKU_DATA__ = [ ... ];
    const SPKU_DETAIL_DATA = { ... };

Pencocokan dengan ekspresi reguler TIDAK aman di sini: nilai string pada muatan
dapat memuat ``];`` atau ``};`` sehingga pola non-greedy maupun greedy sama-sama
salah potong. Modul ini memakai pemindai pembatas berimbang yang sadar string
dan sadar escape.
"""

from __future__ import annotations

import json
import re
from typing import Any

_OPENERS = {"[": "]", "{": "}"}


class ExtractionError(RuntimeError):
    """Variabel ditemukan tetapi muatannya tidak dapat diurai."""


def find_assignment(html: str, var_name: str) -> int | None:
    """Kembalikan indeks karakter pembuka muatan, atau None bila tidak ada."""
    pattern = re.compile(
        r"(?:window\s*\.\s*)?" + re.escape(var_name) + r"\s*=\s*(?=[\[{])"
    )
    match = pattern.search(html)
    return match.end() if match else None


def scan_balanced(text: str, start: int) -> str:
    """Potong satu literal JSON berimbang yang dimulai pada ``start``."""
    opening = text[start]
    if opening not in _OPENERS:
        raise ExtractionError(f"karakter pembuka tidak sah: {opening!r}")
    closing = _OPENERS[opening]

    depth = 0
    in_string = False
    escaped = False

    for i in range(start, len(text)):
        ch = text[i]
        if in_string:
            if escaped:
                escaped = False
            elif ch == "\\":
                escaped = True
            elif ch == '"':
                in_string = False
            continue
        if ch == '"':
            in_string = True
        elif ch == opening:
            depth += 1
        elif ch == closing:
            depth -= 1
            if depth == 0:
                return text[start : i + 1]
    raise ExtractionError("pembatas tidak pernah tertutup sampai akhir dokumen")


def extract_json(html: str, var_name: str) -> Any | None:
    """Kembalikan objek Python dari variabel ``var_name``, atau None bila absen."""
    start = find_assignment(html, var_name)
    if start is None:
        return None
    payload = scan_balanced(html, start)
    try:
        return json.loads(payload)
    except json.JSONDecodeError as exc:  # pragma: no cover - dipicu bila situs berubah
        raise ExtractionError(f"{var_name}: {exc}") from exc


_STRING_D = r'"(?:[^"\\]|\\.)*"'
_STRING_S = r"'(?:[^'\\]|\\.)*'"
_NUMBER = r"-?\d+(?:\.\d+)?(?:[eE][+-]?\d+)?"
_LITERAL = r"null|true|false"


def _scalar_pattern(var_name: str) -> re.Pattern[str]:
    return re.compile(
        r"(?:window\s*\.\s*)?"
        + re.escape(var_name)
        + r"\s*=\s*("
        + "|".join((_STRING_D, _STRING_S, _LITERAL, _NUMBER))
        + r")"
    )


def extract_scalar(html: str, var_name: str) -> Any | None:
    """Baca variabel yang nilainya bukan objek, misalnya ``__SPKU_UPDATE_TIME__``.

    Nilai seperti ``window.__SPKU_UPDATE_TIME__ = "2026-09-13 13:30:00";`` tidak
    dapat dibaca oleh :func:`extract_json` karena pemindai pembatas berimbang
    hanya menerima ``[`` atau ``{`` sebagai pembuka.
    """
    match = _scalar_pattern(var_name).search(html)
    if not match:
        return None
    literal = match.group(1)
    if literal.startswith("'"):
        inner = literal[1:-1].replace("\\'", "'").replace('"', '\\"')
        literal = f'"{inner}"'
    try:
        return json.loads(literal)
    except json.JSONDecodeError:
        return None


def extract_many(html: str, *var_names: str) -> dict[str, Any]:
    """Ekstrak beberapa variabel sekaligus; kunci yang absen dilewati."""
    out: dict[str, Any] = {}
    for name in var_names:
        value = extract_json(html, name)
        if value is not None:
            out[name] = value
    return out
