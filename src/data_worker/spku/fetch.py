"""Klien HTTP: santun, tangguh, dan meninggalkan jejak arsip.

Peladen tidak mengirim ETag, Last-Modified, maupun Cache-Control, sehingga
permintaan bersyarat mustahil dan setiap lintasan selalu mengunduh penuh.
Kompresi gzip tersedia dan dipakai.
"""

from __future__ import annotations

import dataclasses
import datetime as dt
import gzip
import hashlib
import json
import pathlib
import random
import re
import time
from typing import Any

import requests

BASE_URL = "https://udara.jakarta.go.id"

#: Identitas dasar: menyebut apa yang berjalan dan untuk apa, tanpa data
#: pribadi siapa pun.
USER_AGENT_BASE = "UdaraJakartaResearchCollector/1.0 (riset akademik kualitas udara Jakarta"

_EMAIL_RE = re.compile(r"^[^@\s]+@[^@\s]+\.[A-Za-z]{2,}$")

#: Bagian lokal yang jelas-jelas teks isian, bukan alamat sungguhan.
_LOCAL_CONTOH = {
    "isi-surel-anda", "ganti-dengan-surel-anda", "nama.anda", "namaanda",
    "your", "youremail", "your-email", "email", "user", "contoh", "example",
}

#: Ranah yang dicadangkan untuk contoh (RFC 2606) dan ranah uji.
_RANAH_CONTOH = {"example.com", "example.org", "example.net", "example.edu",
                 "example.ac.id", "test", "localhost", "invalid"}


def build_user_agent(contact: str | None) -> str:
    """Susun User-Agent, dengan atau tanpa alamat kontak.

    Kontak bersifat pilihan. Mencantumkannya adalah kebiasaan baik ketika
    menarik data dari peladen instansi publik: bila lalu lintasnya dianggap
    mengganggu, pengelola punya jalan menghubungi sebelum memblokir. Tetapi
    alamat itu terkirim ke pihak ketiga pada setiap permintaan, jadi
    keputusannya ada pada pemilik alamat, bukan pada perangkat lunaknya.

    Nilai yang masih berupa teks contoh diperlakukan sama dengan kosong,
    supaya tidak ada alamat palsu yang terlanjur terkirim. Pemeriksaannya
    sengaja berbasis potongan utuh, bukan pencocokan substring: nama orang
    seperti ``budisiswanto`` memuat potongan ``isi`` dan tidak boleh ikut
    tersaring.
    """
    value = (contact or "").strip()
    if not _EMAIL_RE.match(value):
        return USER_AGENT_BASE + ")"
    lokal, _, ranah = value.rpartition("@")
    if lokal.lower() in _LOCAL_CONTOH or ranah.lower() in _RANAH_CONTOH:
        return USER_AGENT_BASE + ")"
    return f"{USER_AGENT_BASE}; kontak: {value})"


@dataclasses.dataclass(slots=True)
class FetchResult:
    url: str
    status: int | None
    text: str | None
    bytes: int
    sha256: str | None
    elapsed_ms: int
    fetched_utc: dt.datetime
    error: str | None = None
    archive_path: str | None = None

    @property
    def ok(self) -> bool:
        return self.status == 200 and bool(self.text)


class Fetcher:
    def __init__(
        self,
        contact: str | None = None,
        delay_seconds: float = 1.0,
        timeout: int = 45,
        retries: int = 3,
        archive_dir: str | pathlib.Path | None = None,
        base_url: str = BASE_URL,
    ):
        self.base_url = base_url.rstrip("/")
        self.delay = delay_seconds
        self.timeout = timeout
        self.retries = retries
        self.archive_dir = pathlib.Path(archive_dir) if archive_dir else None
        self.session = requests.Session()
        self.session.headers.update(
            {
                "User-Agent": build_user_agent(contact),
                "Accept": "text/html,application/xhtml+xml",
                "Accept-Encoding": "gzip, deflate",
                "Accept-Language": "id,en;q=0.8",
            }
        )
        self._last_request = 0.0

    def _throttle(self) -> None:
        wait = self.delay - (time.monotonic() - self._last_request)
        if wait > 0:
            time.sleep(wait)
        self._last_request = time.monotonic()

    def get(self, path: str) -> FetchResult:
        url = path if path.startswith("http") else f"{self.base_url}{path}"
        last_error: str | None = None
        last_result: FetchResult | None = None
        elapsed = 0
        fetched = dt.datetime.now(dt.timezone.utc).replace(microsecond=0)
        for attempt in range(1, max(1, self.retries) + 1):
            self._throttle()
            started = time.monotonic()
            fetched = dt.datetime.now(dt.timezone.utc).replace(microsecond=0)
            try:
                response = self.session.get(url, timeout=self.timeout)
                elapsed = int((time.monotonic() - started) * 1000)
                body = response.text
                digest = hashlib.sha256(body.encode("utf-8", "replace")).hexdigest()
                result = FetchResult(
                    url=url,
                    status=response.status_code,
                    text=body,
                    bytes=len(response.content),
                    sha256=digest,
                    elapsed_ms=elapsed,
                    fetched_utc=fetched,
                )
                if response.status_code == 200:
                    return result
                last_error = f"HTTP {response.status_code}"
                result.error = last_error
                # Simpan jawaban sungguhan agar fetch_log dapat membedakan
                # 503 dari kegagalan jaringan.
                last_result = result
                # 404 tidak akan membaik dengan pengulangan.
                if response.status_code == 404:
                    return result
            except requests.RequestException as exc:
                elapsed = int((time.monotonic() - started) * 1000)
                last_error = f"{type(exc).__name__}: {exc}"
            if attempt < self.retries:
                time.sleep(min(60.0, (2 ** attempt) + random.uniform(0, 1.0)))
        if last_result is not None:
            return last_result
        return FetchResult(
            url=url, status=None, text=None, bytes=0, sha256=None,
            elapsed_ms=elapsed, fetched_utc=fetched, error=last_error,
        )

    def close(self) -> None:
        self.session.close()

    def __enter__(self) -> "Fetcher":
        return self

    def __exit__(self, *exc) -> None:
        self.close()

    def archive(self, run_id: str, name: str, payload: Any) -> str | None:
        """Simpan muatan JSON terekstraksi dalam bentuk terkompresi.

        Yang diarsipkan adalah JSON hasil ekstraksi, bukan HTML utuh: isinya
        identik secara informasi tetapi ukurannya jauh lebih kecil. HTML utuh
        hanya diarsipkan ketika penguraian gagal, lewat ``archive_raw``.
        """
        if self.archive_dir is None:
            return None
        day = run_id[:8]
        target = self.archive_dir / day / run_id
        target.mkdir(parents=True, exist_ok=True)
        path = target / f"{name}.json.gz"
        with gzip.open(path, "wt", encoding="utf-8") as handle:
            json.dump(payload, handle, separators=(",", ":"), default=str)
        return str(path)

    def archive_raw(self, run_id: str, name: str, html: str) -> str | None:
        if self.archive_dir is None:
            return None
        target = self.archive_dir / "gagal" / run_id
        target.mkdir(parents=True, exist_ok=True)
        path = target / f"{name}.html.gz"
        with gzip.open(path, "wt", encoding="utf-8") as handle:
            handle.write(html)
        return str(path)
