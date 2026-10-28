"""SPKU portal stand-in for the E2E stack, built from the controlled dataset.

Serves the three page types the Data Worker's collector reads (/lokasi-spku,
/sitemap.xml, /spku/<uuid>, same markup as src/data_worker/tests/fixtures) for
the active stations of fixture/gt_station.csv, with their observations from
fixture/gt_observation.csv.gz. fetch_ground_truth therefore runs its real
collection path, guards included, without the internet.

The fixture rows are already in the database (load_fixture.py), so a pass
re-ingests identical values and the dataset stays as exported. Points flagged
as a stuck sensor (qc S) are left out: that flag comes from the portal's 48 h
history, and a 3 h replay would clear it.

SPKU_MODE: ok (default) or down (every request 503, used by E2E-04).
Standard library only; runs in python:3.11-slim.
"""
from __future__ import annotations

import csv
import datetime as dt
import gzip
import hashlib
import http.server
import io
import json
import os
import pathlib

FIXTURE = pathlib.Path(__file__).resolve().parent / "fixture"
WIB = dt.timedelta(hours=7)  # detail pages carry naive WIB timestamps


def fixture_rows(name: str) -> list[dict]:
    manifest = json.loads((FIXTURE / "manifest.json").read_text(encoding="utf-8"))
    raw = (FIXTURE / name).read_bytes()
    expected = next(f["sha256"] for f in manifest["files"] if f["file"] == name)
    if hashlib.sha256(raw).hexdigest() != expected:
        raise SystemExit(f"{name}: checksum tidak cocok dengan manifest.json")
    data = gzip.decompress(raw) if name.endswith(".gz") else raw
    return list(csv.DictReader(io.StringIO(data.decode("utf-8"))))


# An empty CSV cell is a NULL in the exported table and null on the portal; sending ""
# instead would overwrite the stored NULL (the store keeps a field only when null).
def text(value: str) -> str | None:
    return value or None


def number(value: str) -> float | None:
    return float(value) if value else None


def build_pages() -> dict[str, str]:
    stations = [s for s in fixture_rows("gt_station.csv") if s["active"] == "t"]
    history: dict[str, list[dict]] = {}
    for o in fixture_rows("gt_observation.csv.gz"):
        if "S" in o["qc"]:
            continue
        ts = dt.datetime.fromisoformat(o["ts_utc"].replace("+00", "+00:00")) + WIB
        history.setdefault(o["station_uuid"], []).append(
            {"time": ts.replace(tzinfo=None).isoformat(), "value": number(o["value"]),
             "metricName": o["metric"]})

    pages = {}
    rows, locs = [], []
    for i, s in enumerate(stations, 1):
        rows.append(f'<tr class="hover:bg-blue-50" data-redirect="/spku/{s["uuid"]}"><td class="px-6">{i}</td>'
                    f'<td class="px-6">\n    {s["initial"]} {s["name"]}\n    <div class="text-xs">{s["alamat"]}</div></td>'
                    f'<td class="px-6"><span class="bg-green">PM25</span></td><td>{s["kecamatan"]}</td><td>{s["kota"]}</td></tr>')
        locs.append(f"  <url><loc>https://udara.jakarta.go.id/spku/{s['uuid']}</loc></url>")
        points = sorted(history.get(s["uuid"], []), key=lambda p: (p["time"], p["metricName"]))
        payload = {
            "datasourceID": s["uuid"], "datasourceKode": text(s["kode"]), "datasourceName": text(s["name"]),
            "type": text(s["type"]), "latitude": number(s["lat"]), "longitude": number(s["lng"]),
            "kecamatanNama": text(s["kecamatan"]), "kotaKabupatenNama": text(s["kota"]),
            "alamat": text(s["alamat"]), "dioperasikan": text(s["dioperasikan"]),
            "metrics": [{"metricName": m} for m in sorted({p["metricName"] for p in points})],
            "rawHistory": points, "ispuHistory": [], "rawMeteoHistory": [], "weatherHistory": [],
            "dailyIspu30Days": [], "lastUpdate": points[-1]["time"] if points else None,
        }
        pages[f"/spku/{s['uuid']}"] = (
            "<!DOCTYPE html><html><body><script>\n"
            f"  const SPKU_DETAIL_DATA = {json.dumps(payload, separators=(',', ':'))};\n"
            "</script></body></html>\n")
    pages["/lokasi-spku"] = "<!DOCTYPE html><html><body><table><tbody>\n" + "\n".join(rows) + \
        "\n</tbody></table></body></html>"
    pages["/sitemap.xml"] = '<urlset xmlns="https://www.sitemaps.org/schemas/sitemap/0.9">\n' + "\n".join(locs) + \
        "\n</urlset>"
    return pages


def main() -> None:
    mode = os.environ.get("SPKU_MODE", "ok")
    pages = build_pages()

    class Handler(http.server.BaseHTTPRequestHandler):
        def do_GET(self):  # noqa: N802
            body = pages.get(self.path.split("?", 1)[0])
            status = 503 if mode == "down" else 200 if body is not None else 404
            data = (body if status == 200 else "layanan tidak tersedia" if status == 503 else "tidak ditemukan")
            data = data.encode("utf-8")
            self.send_response(status)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.send_header("Content-Length", str(len(data)))
            self.end_headers()
            self.wfile.write(data)

        def log_message(self, fmt, *args):
            pass  # one line per station page would drown the compose logs

    print(f"portal SPKU tiruan mode {mode}: {len(pages) - 2} stasiun", flush=True)
    http.server.ThreadingHTTPServer(("0.0.0.0", 8765), Handler).serve_forever()


if __name__ == "__main__":
    main()
