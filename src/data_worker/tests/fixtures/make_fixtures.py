"""Bangkitkan fixture yang meniru persis keanehan muatan portal.

Setiap keanehan di sini teramati pada muatan nyata 13 September 2026 dan
didokumentasikan di docs/TEMUAN_DATA.md.
"""

from __future__ import annotations

import datetime as dt
import json
import pathlib
import random

HERE = pathlib.Path(__file__).resolve().parent
UTC = dt.timezone.utc
BASE = dt.datetime(2026, 9, 11, 7, 0, tzinfo=UTC)


def stamp(moment: dt.datetime) -> str:
    """Halaman rinci menulis UTC tanpa penanda zona."""
    return moment.replace(tzinfo=None).isoformat()


def build_detail(
    station_uuid: str,
    kode: str,
    name: str,
    kind: str,
    metrics: list[str],
    cadence_minutes: int,
    points: int,
    meteo_mode: str,
    shuffle_metrics_history: bool = True,
    stuck_metric: str | None = None,
) -> dict:
    rng = random.Random(hash(station_uuid) & 0xFFFF)
    times = [BASE + dt.timedelta(minutes=cadence_minutes * i) for i in range(points)]

    raw_history = []
    metric_blocks = []
    for index, metric in enumerate(metrics, start=1):
        series = []
        for i, moment in enumerate(times):
            if metric == stuck_metric:
                value = 41.0
            else:
                value = round(20 + 30 * rng.random() + 5 * (i % 7), 2)
            series.append({"time": stamp(moment), "value": value, "metricName": metric})
        raw_history.extend(series)
        history = [{"time": p["time"], "value": p["value"]} for p in series]
        if shuffle_metrics_history:
            rng.shuffle(history)  # meniru metrics[].history yang tidak terurut
        metric_blocks.append(
            {
                "metricID": index,
                "metricName": metric,
                "parameterName": metric,
                "currentIspu": 103,
                "currentRaw": series[-1]["value"],
                "history": history,
            }
        )
    raw_history.sort(key=lambda p: (p["time"], p["metricName"]))

    hourly = [t for t in times if t.minute == 0]
    ispu_history = [
        {"time": stamp(t), "value": 50 + (i % 60), "metricName": metric}
        for metric in metrics
        for i, t in enumerate(hourly)
    ]

    meteo = []
    for moment in times:
        if meteo_mode == "reference":
            row = {"temperature": 28.4, "humidity": 71.2, "windSpeed": 1.3}
        elif meteo_mode == "zero":            # suhu dan kelembapan palsu
            row = {"temperature": 0, "humidity": 0, "windSpeed": 2.25}
        elif meteo_mode == "no-wind":         # LCS: angin selalu kosong
            row = {"temperature": 29.1, "humidity": 75.4, "windSpeed": None}
        else:
            row = {"temperature": None, "humidity": None, "windSpeed": None}
        meteo.append({"time": stamp(moment), "ispu": None, **row})

    daily = []
    for offset in range(30, -1, -1):
        day = dt.datetime(2026, 9, 13, tzinfo=UTC) - dt.timedelta(days=offset)
        daily.append(
            {
                "date": day.isoformat().replace("+00:00", "Z"),
                "maxIspu": 90 + (offset % 30),
                "concentration": round(60 + 40 * rng.random(), 1),
                "dominantMetric": "PM25",
            }
        )

    return {
        "datasourceID": station_uuid,
        "datasourceKode": kode,
        "datasourceName": name,
        "kecamatanNama": "MENTENG",
        "kotaKabupatenNama": "KOTA ADM. JAKARTA PUSAT",
        "alamat": "Jl. Contoh No. 1",
        "dioperasikan": "Dinas Lingkungan Hidup",
        "type": kind,
        "latitude": -6.195459,
        "longitude": 106.822731,
        "tahunPengadaan": None,
        "lastMaintenanceAt": None,
        # lastUpdate sengaja dibuat basi dan tanpa penanda zona, meniru LCS25.
        "lastUpdate": "2026-07-08T15:00:00" if kind == "Sensor" else "2026-09-13T06:00:00Z",
        "ispuValue": 103,
        "ispuCategory": "Tidak Sehat",
        "ispuParameter": "PM25",
        "ispuConcentration": 110.6,
        "metrics": metric_blocks,
        "temperature": 27.83,
        "humidity": 72.99,
        "windSpeed": 0.16,
        "weatherCondition": None,
        "meteorologyHours": [stamp(t) for t in times],
        "forecast": [],
        "ispuHistory": ispu_history,
        "rawHistory": raw_history,
        "weatherHistory": [],          # kosong pada seluruh 119 stasiun
        "rawMeteoHistory": meteo,
        "dailyIspu30Days": daily,
    }


def wrap_detail(payload: dict) -> str:
    """Bungkus dalam HTML, termasuk jebakan '];' di dalam string."""
    body = json.dumps(payload, separators=(",", ":"))
    return (
        "<!DOCTYPE html><html><head><title>SPKU</title></head><body>\n"
        '<script>window.__FIREBASE_CONFIG__ = {"a":"b"};</script>\n'
        "<script>\n"
        '  const CATATAN = "nilai palsu }; dan ]; di dalam string";\n'
        f"  const SPKU_DETAIL_DATA = {body};\n"
        '  window.__HEALTH_DATA__ = {"category":"Tidak Sehat"};\n'
        "</script></body></html>\n"
    )


def build_home(n: int, empty: bool = False) -> str:
    stations = []
    for i in range(0 if empty else n):
        stations.append(
            {
                "id": f"0000{i:04d}-0000-4000-8000-{i:012d}",
                "name": f"DKI_PM25_{i}",
                "initial": f"DKI{i:02d}",
                "datasourceName": f"Stasiun {i}",
                "area": "Kota Adm. Jakarta Pusat",
                "kecamatan": "MENTENG",
                "kecamatanID": "31.71.05",
                "kota": "Kota Adm. Jakarta Pusat",
                "lat": -6.19 - i / 1000,
                "lng": 106.82 + i / 1000,
                "ispu": 100 + i,
                "status": "Tidak Sehat",
                "dominantMetric": "PM25",
                "dominantRawValue": 26.81,
                "temperature": None,
                "humidity": None,
                "wind": None,
                "windDirection": None,
                # Beranda memakai WIB tanpa penanda zona.
                "dominantMetricTime": "2026-09-13T13:30:00",
            }
        )
    blob = json.dumps(stations, separators=(",", ":"))
    update = "null" if empty else '"2026-09-13 13:30:00"'
    return (
        "<!DOCTYPE html><html><body><script>\n"
        f"  window.__SPKU_DATA__ = {blob};\n"
        f"  window.__SPKU_UPDATE_TIME__ = {update};\n"
        "  window.__SILAM_DATA__ = null;\n"
        "</script></body></html>\n"
    )


def build_list(entries: list[tuple[str, str]]) -> str:
    rows = "\n".join(
        f'<tr class="hover:bg-blue-50" data-redirect="/spku/{u}">'
        f'<td class="px-6">{i + 1}</td>'
        f'<td class="px-6">\n    {label}\n    <div class="text-xs">Jl. Contoh</div></td>'
        f'<td class="px-6"><span class="bg-green">PM25</span></td>'
        f"<td>MENTENG</td><td>KOTA ADM. JAKARTA PUSAT</td></tr>"
        for i, (u, label) in enumerate(entries)
    )
    return f"<!DOCTYPE html><html><body><table><tbody>\n{rows}\n</tbody></table></body></html>"


def build_sitemap(uuids: list[str]) -> str:
    locs = "\n".join(
        f"  <url><loc>https://udara.jakarta.go.id/spku/{u}</loc></url>" for u in uuids
    )
    return f'<urlset xmlns="https://www.sitemaps.org/schemas/sitemap/0.9">\n{locs}\n</urlset>'


CASES = {
    "referensi_lengkap": dict(
        station_uuid="2d554554-7567-4ac1-ab87-536260d2ba79", kode="DKI1", name="Bundaran HI",
        kind="Reference", metrics=["SO2", "PM10", "NO2", "CO", "O3", "PM25"],
        cadence_minutes=30, points=96, meteo_mode="reference",
    ),
    "sensor_meteo_nol": dict(
        station_uuid="022441fc-0402-4b51-af62-ba5c8e4fa37f", kode="DKI20", name="Rusunawa Pesakih",
        kind="Sensor", metrics=["SO2", "PM10", "NO2", "CO", "O3", "PM25"],
        cadence_minutes=30, points=96, meteo_mode="zero", stuck_metric="SO2",
    ),
    "lcs_per_jam": dict(
        station_uuid="42b53155-7cfe-4543-aec2-5832c886d36f", kode="LCS01",
        name="Patung Pemuda Membangun", kind="Sensor", metrics=["PM25"],
        cadence_minutes=60, points=48, meteo_mode="no-wind",
    ),
}


def main() -> None:
    HERE.mkdir(parents=True, exist_ok=True)
    entries = []
    for slug, kwargs in CASES.items():
        payload = build_detail(**kwargs)
        (HERE / f"detail_{slug}.html").write_text(wrap_detail(payload), encoding="utf-8")
        entries.append((kwargs["station_uuid"], f"{kwargs['kode']} {kwargs['name']}"))
    (HERE / "home_ok.html").write_text(build_home(20), encoding="utf-8")
    (HERE / "home_kosong.html").write_text(build_home(0, empty=True), encoding="utf-8")
    (HERE / "lokasi_spku.html").write_text(build_list(entries), encoding="utf-8")
    (HERE / "sitemap.xml").write_text(
        build_sitemap([u for u, _ in entries]), encoding="utf-8"
    )
    (HERE / "detail_rusak.html").write_text(
        "<html><body><script>const SPKU_DETAIL_DATA = {\"a\": </script></body></html>",
        encoding="utf-8",
    )
    print(f"fixture ditulis ke {HERE}")


if __name__ == "__main__":
    main()
