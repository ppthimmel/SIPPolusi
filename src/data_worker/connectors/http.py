"""Klien HTTP konektor sumber data sekunder: timeout, coba ulang, dan jejak kegagalan (Tabel 3.19).

Setiap sumber memakai satu ``HttpClient`` per siklus. Gagal sementara (galat
jaringan, timeout, HTTP 429 dan 5xx) dicoba ulang dengan jeda eksponensial;
setiap pengulangan dicatat pada log. Gagal permanen (sisa percobaan habis, atau
HTTP 4xx lain) dinaikkan sebagai ``SourceError`` dan dihitung pada
``failed_requests``. Kredensial hanya dikirim lewat header dan tidak pernah
masuk log, pesan galat, maupun basis data.
"""

from __future__ import annotations

import dataclasses
import io
import logging
import re
import time
from typing import Callable
from urllib.parse import urlsplit

import requests

log = logging.getLogger("data_worker.connectors")

USER_AGENT = "SIPPolusi-data-worker/1.0 (riset akademik kualitas udara Jakarta)"
RETRY_STATUSES = frozenset({429, 500, 502, 503, 504})


class SourceError(RuntimeError):
    """Sumber tidak dapat diambil pada siklus ini setelah seluruh percobaan."""


class FormatError(ValueError):
    """Respons sumber tidak sesuai format yang diharapkan (format berubah)."""


def safe_url(url: str) -> str:
    """URL tanpa query (token SAS dan kunci tidak ikut tercatat)."""
    parts = urlsplit(url)
    return f"{parts.scheme}://{parts.netloc}{parts.path}"


_URL_QUERY = re.compile(r"((?:https?|s3)://[^\s?#'\"`]+)\?[^\s'\"`]*")


def scrub(text) -> str:
    """Teks dengan query setiap URL dibuang, untuk pesan galat pustaka yang memuat URL bertanda tangan."""
    return _URL_QUERY.sub(r"\1?…", str(text))


class ScrubQueryFilter(logging.Filter):
    """Buang query URL dari catatan log pustaka (mis. GDAL lewat rasterio mencatat URL SAS pada INFO)."""

    def filter(self, record: logging.LogRecord) -> bool:
        message = record.getMessage()
        clean = scrub(message)
        if clean != message:
            record.msg, record.args = clean, ()
        return True


#: Logger pustaka yang dapat mencatat URL aset bertanda tangan. Filter logger tidak diwarisi
#: logger anak, sehingga setiap nama didaftarkan sendiri.
LIBRARY_LOGGERS = ("rasterio", "rasterio._env", "rasterio._err", "rasterio._base", "rasterio._io", "rasterio.env",
                   "urllib3.connectionpool", "h5py")
_SCRUB_FILTER = ScrubQueryFilter()


def install_log_scrubbing() -> None:
    for name in LIBRARY_LOGGERS:
        logger = logging.getLogger(name)
        if _SCRUB_FILTER not in logger.filters:
            logger.addFilter(_SCRUB_FILTER)


@dataclasses.dataclass(frozen=True, slots=True)
class Policy:
    """Batas per sumber (Tabel 3.19): timeout per permintaan, jumlah percobaan, jeda awal."""

    timeout_s: float
    tries: int = 3
    backoff_s: float = 2.0


class HttpClient:
    def __init__(self, source: str, policy: Policy, *, session: requests.Session | None = None,
                 headers: dict | None = None, sleep: Callable[[float], None] | None = None):
        self.source = source
        self.policy = policy
        self.session = session or requests.Session()
        self.session.headers.setdefault("User-Agent", USER_AGENT)
        self.headers = dict(headers or {})
        self.sleep = sleep or time.sleep
        self.requests = 0
        self.retried_requests = 0
        self.failed_requests = 0

    def counts(self) -> dict:
        return {"requests": self.requests, "retried_requests": self.retried_requests,
                "failed_requests": self.failed_requests}

    def request(self, method: str, url: str, *, ok=(200,), plain: bool = False, **kwargs) -> requests.Response:
        """Satu permintaan dengan coba ulang; status di ``ok`` dikembalikan apa adanya.

        ``plain`` mengirim permintaan tanpa header kredensial klien, mis. ke URL
        penyimpanan objek bertanda tangan hasil pengalihan.
        """
        kwargs.setdefault("timeout", self.policy.timeout_s)
        headers = {**({} if plain else self.headers), **kwargs.pop("headers", {})}
        self.requests += 1
        last = ""
        for attempt in range(1, self.policy.tries + 1):
            try:
                response = self.session.request(method, url, headers=headers, **kwargs)
            except (requests.ConnectionError, requests.Timeout) as exc:
                last = f"{type(exc).__name__}"
            else:
                if response.status_code in ok:
                    if attempt > 1:
                        self.retried_requests += 1
                        log.info("%s: %s %s berhasil pada percobaan %d", self.source, method,
                                 safe_url(url), attempt)
                    return response
                last = f"HTTP {response.status_code}"
                if response.status_code not in RETRY_STATUSES:
                    response.close()
                    break
                retry_after = response.headers.get("Retry-After", "")
                response.close()
                if response.status_code == 429 and retry_after.isdigit() and attempt < self.policy.tries:
                    log.warning("%s: %s %s HTTP 429, menunggu %s detik", self.source, method,
                                safe_url(url), retry_after)
                    self.sleep(min(float(retry_after), 120.0))
                    continue
            if attempt < self.policy.tries:
                delay = self.policy.backoff_s * 2 ** (attempt - 1)
                log.warning("%s: %s %s gagal pada percobaan %d/%d (%s); diulang dalam %.0f detik",
                            self.source, method, safe_url(url), attempt, self.policy.tries, last, delay)
                self.sleep(delay)
        self.failed_requests += 1
        log.error("%s: %s %s tetap gagal (%s)", self.source, method, safe_url(url), last)
        raise SourceError(f"{self.source}: {method} {safe_url(url)} gagal: {last}")

    def get(self, url: str, **kwargs) -> requests.Response:
        return self.request("GET", url, **kwargs)

    def get_json(self, url: str, **kwargs):
        response = self.get(url, **kwargs)
        try:
            return response.json()
        except ValueError as exc:
            raise FormatError(f"{self.source}: respons {safe_url(url)} bukan JSON") from exc

    def download(self, url: str, max_bytes: int, **kwargs) -> bytes:
        """Unduh utuh ke memori dengan batas ukuran."""
        response = self.get(url, stream=True, **kwargs)
        buffer = io.BytesIO()
        for chunk in response.iter_content(1 << 20):
            buffer.write(chunk)
            if buffer.tell() > max_bytes:
                response.close()
                raise SourceError(f"{self.source}: {safe_url(url)} melebihi {max_bytes} byte")
        return buffer.getvalue()


class RangeFile(io.RawIOBase):
    """Berkas jarak jauh yang dibaca per blok lewat HTTP Range, untuk ``h5py.File``.

    Hanya blok yang disentuh pustaka HDF5 yang diunduh, sehingga variabel kecil
    dari granule NetCDF/HDF5 besar dapat dibaca tanpa mengunduh seluruh berkas.
    """

    def __init__(self, client: HttpClient, url: str, block_size: int = 2 << 20, max_bytes: int = 600 << 20):
        super().__init__()
        self.client = client
        self.block_size = block_size
        self.max_bytes = max_bytes
        probe = client.get(url, headers={"Range": "bytes=0-0"}, ok=(206,), stream=True)
        self.url = probe.url             # URL akhir setelah pengalihan (mis. ke penyimpanan objek)
        self.plain = urlsplit(self.url).netloc != urlsplit(url).netloc
        total = probe.headers.get("Content-Range", "").rpartition("/")[2]
        probe.close()
        if not total.isdigit():
            raise FormatError(f"{client.source}: {safe_url(url)} tidak mendukung HTTP Range")
        self.size = int(total)
        self.position = 0
        self.fetched = 0
        self.blocks: dict[int, bytes] = {}
        #: Galat terakhir saat membaca blok; h5py membungkusnya menjadi OSError tanpa pesan asli.
        self.error: Exception | None = None

    def readable(self) -> bool:
        return True

    def seekable(self) -> bool:
        return True

    def tell(self) -> int:
        return self.position

    def seek(self, offset: int, whence: int = io.SEEK_SET) -> int:
        base = {io.SEEK_SET: 0, io.SEEK_CUR: self.position, io.SEEK_END: self.size}[whence]
        self.position = max(0, base + offset)
        return self.position

    def _block(self, index: int) -> bytes:
        if index not in self.blocks:
            start = index * self.block_size
            end = min(start + self.block_size, self.size) - 1
            try:
                data = self.client.get(self.url, headers={"Range": f"bytes={start}-{end}"}, ok=(206,),
                                       plain=self.plain).content
                self.fetched += len(data)
                if len(data) != end - start + 1:
                    # Respons pendek atau kosong: tanpa pemeriksaan ini readinto tidak pernah maju.
                    raise SourceError(f"{self.client.source}: respons Range {start}-{end} berisi {len(data)} byte, "
                                      f"seharusnya {end - start + 1}")
                if self.fetched > self.max_bytes:
                    raise SourceError(f"{self.client.source}: pembacaan melebihi {self.max_bytes} byte")
            except Exception as exc:
                self.error = exc
                raise
            self.blocks[index] = data
        return self.blocks[index]

    def readinto(self, buffer) -> int:
        size = min(len(buffer), self.size - self.position)
        if size <= 0:
            return 0
        out = memoryview(buffer)
        written = 0
        while written < size:
            index, offset = divmod(self.position, self.block_size)
            chunk = self._block(index)[offset:offset + size - written]
            if not chunk:
                self.error = SourceError(f"{self.client.source}: blok {index} tidak memuat posisi {self.position}")
                raise self.error
            out[written:written + len(chunk)] = chunk
            written += len(chunk)
            self.position += len(chunk)
        return written
