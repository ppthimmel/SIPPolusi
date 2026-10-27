"""Portal SPKU tiruan untuk uji jalur berhasil dan gagal Data Worker (TI-DO-07).

Menyajikan halaman rekaman ``tests/fixtures`` (label waktu 2026-09-13 WIB,
sehingga time window yang berisi data adalah 2026-09-12T22:00Z). Arahkan
worker ke sini dengan ``SPKU_BASE_URL=http://127.0.0.1:<port>``.

Mode:
  ok      halaman normal
  flaky   setiap path menjawab 503 pada permintaan pertama, lalu normal (gagal sementara)
  down    selalu 503 (gagal permanen)
  empty   halaman rinci HTTP 200 tanpa observasi (pola gangguan 8 September 2026)

    python scripts/spku_stub_portal.py --mode flaky --port 8765
"""

from __future__ import annotations

import argparse
import http.server
import json
import pathlib

FIX = pathlib.Path(__file__).resolve().parent.parent / "tests" / "fixtures"
DETAIL_BY_UUID = {
    "2d554554-7567-4ac1-ab87-536260d2ba79": "detail_referensi_lengkap.html",
    "022441fc-0402-4b51-af62-ba5c8e4fa37f": "detail_sensor_meteo_nol.html",
    "42b53155-7cfe-4543-aec2-5832c886d36f": "detail_lcs_per_jam.html",
}


def _empty_detail(station_uuid: str) -> str:
    payload = {"datasourceID": station_uuid, "datasourceKode": None, "datasourceName": None, "type": None,
               "latitude": None, "longitude": None, "metrics": [], "rawHistory": [], "ispuHistory": [],
               "rawMeteoHistory": [], "weatherHistory": [], "dailyIspu30Days": [], "ispuValue": None}
    return f"<html><script>const SPKU_DETAIL_DATA = {json.dumps(payload)};</script></html>"


def make_handler(mode: str):
    seen: set[str] = set()

    class Handler(http.server.BaseHTTPRequestHandler):
        def do_GET(self):  # noqa: N802
            path = self.path.split("?", 1)[0]
            first = path not in seen
            seen.add(path)
            if mode == "down" or (mode == "flaky" and first):
                return self._send(503, "layanan tidak tersedia")
            body = self._body(path)
            if body is None:
                return self._send(404, "tidak ditemukan")
            return self._send(200, body)

        def _body(self, path):
            if path == "/lokasi-spku":
                return (FIX / "lokasi_spku.html").read_text(encoding="utf-8")
            if path == "/sitemap.xml":
                return (FIX / "sitemap.xml").read_text(encoding="utf-8")
            if path.startswith("/spku/"):
                station_uuid = path.rsplit("/", 1)[-1]
                if station_uuid not in DETAIL_BY_UUID:
                    return None
                if mode == "empty":
                    return _empty_detail(station_uuid)
                return (FIX / DETAIL_BY_UUID[station_uuid]).read_text(encoding="utf-8")
            if path == "/":
                return (FIX / "home_ok.html").read_text(encoding="utf-8")
            return None

        def _send(self, status, text):
            data = text.encode("utf-8")
            self.send_response(status)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.send_header("Content-Length", str(len(data)))
            self.end_headers()
            self.wfile.write(data)

        def log_message(self, fmt, *args):
            print(f"stub {mode}: {self.command} {self.path} -> {args[1] if len(args) > 1 else ''}", flush=True)

    return Handler


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--mode", choices=("ok", "flaky", "down", "empty"), default="ok")
    parser.add_argument("--port", type=int, default=8765)
    args = parser.parse_args()
    server = http.server.ThreadingHTTPServer(("127.0.0.1", args.port), make_handler(args.mode))
    print(f"portal SPKU tiruan mode {args.mode} di http://127.0.0.1:{args.port}", flush=True)
    server.serve_forever()


if __name__ == "__main__":
    main()
