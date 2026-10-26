"""Verification report for the RoadGraph read by the Backend.

Checks the whole active graph_version in the OSM Cache DB and prints a
Markdown report: version metadata, node/edge statistics, mode access counts,
edge validity (the same rules as graph.assemble_road_graph) and EdgeWeight
mapping. Read-only. Run from src/backend:

    python -m osm.verify > osm/VERIFICATION.md
"""
from __future__ import annotations

import asyncio
import sys
from datetime import datetime, timezone

from .config import OsmSettings
from .db import OsmDatabase
from .errors import CacheUnavailable
from .models import BBox, Mode
from .repository import OsmRepository

# Whole-table scans need more than the 1.5 s request budget.
_VERIFY_TIMEOUT_MS = 300_000
# About 1 x 1 km around Monas, read through the normal read_road_graph path.
_SAMPLE_BBOX = BBox(106.822, -6.180, 106.832, -6.170)

_SQL_VERSIONS = """
SELECT version, status, osm_timestamp, built_at, node_count, edge_count
FROM osm.graph_version ORDER BY built_at DESC NULLS LAST
"""

_SQL_COUNTS = """
SELECT
  (SELECT count(*) FROM osm.road_node WHERE graph_version = $1) AS nodes,
  (SELECT count(*) FROM osm.road_node WHERE graph_version = $1 AND geom IS NULL) AS nodes_without_geom,
  (SELECT count(*) FROM osm.road_node WHERE graph_version = $1 AND tags <> '{}'::jsonb) AS nodes_tagged,
  count(*) AS edges,
  count(*) FILTER (WHERE walk_allowed) AS walk,
  count(*) FILTER (WHERE bike_allowed) AS bike,
  count(*) FILTER (WHERE walk_allowed AND bike_allowed) AS walk_and_bike,
  count(*) FILTER (WHERE walk_allowed AND NOT bike_allowed) AS walk_only,
  count(*) FILTER (WHERE bike_allowed AND NOT walk_allowed) AS bike_only,
  count(*) FILTER (WHERE NOT walk_allowed AND NOT bike_allowed) AS neither,
  count(*) FILTER (WHERE oneway) AS oneway,
  count(*) FILTER (WHERE oneway AND bike_allowed) AS oneway_bike,
  count(*) FILTER (WHERE u = v) AS self_loops,
  round(sum(length_m)::numeric / 1000) AS total_km
FROM osm.road_edge WHERE graph_version = $1
"""

_SQL_VALIDITY = """
SELECT
  count(*) FILTER (WHERE e.geom IS NULL OR ST_NPoints(e.geom) < 2) AS missing_geometry,
  count(*) FILTER (WHERE e.length_m IS NULL OR NOT (e.length_m > 0)
                     OR e.length_m IN ('Infinity', 'NaN')) AS invalid_length,
  count(*) FILTER (WHERE nu.node_id IS NULL OR nv.node_id IS NULL
                      OR nu.geom IS NULL OR nv.geom IS NULL) AS missing_endpoint_node,
  count(*) FILTER (WHERE e.geom IS NOT NULL AND nu.geom IS NOT NULL AND nv.geom IS NOT NULL
                     AND (NOT ST_DWithin(ST_StartPoint(e.geom), nu.geom, 1e-5)
                          OR NOT ST_DWithin(ST_EndPoint(e.geom), nv.geom, 1e-5))) AS geometry_endpoint_mismatch,
  count(*) FILTER (WHERE e.geom IS NOT NULL AND e.length_m > 0
                     AND abs(e.length_m - ST_Length(e.geom::geography)) > greatest(1.0, 0.01 * e.length_m))
    AS length_differs_from_geometry,
  count(*) - count(DISTINCT e.edge_id) AS duplicate_edge_id
FROM osm.road_edge e
LEFT JOIN osm.road_node nu ON nu.node_id = e.u AND nu.graph_version = e.graph_version
LEFT JOIN osm.road_node nv ON nv.node_id = e.v AND nv.graph_version = e.graph_version
WHERE e.graph_version = $1
"""

# edge_id must be unique across versions so EdgeWeight (keyed by edge_id only) is unambiguous.
_SQL_GLOBAL_EDGE_IDS = "SELECT count(*) - count(DISTINCT edge_id) FROM osm.road_edge"

_SQL_EDGE_WEIGHTS = """
SELECT count(*) AS rows,
       count(*) FILTER (WHERE NOT EXISTS (
           SELECT 1 FROM osm.road_edge e WHERE e.edge_id = p.edge_id)) AS orphan_rows
FROM pollution.edge_pollution p
"""

_SQL_HIGHWAY = """
SELECT highway, count(*) AS edges,
       count(*) FILTER (WHERE walk_allowed) AS walk,
       count(*) FILTER (WHERE bike_allowed) AS bike
FROM osm.road_edge WHERE graph_version = $1
GROUP BY highway ORDER BY edges DESC
"""


def _n(value) -> str:
    return f"{value:,}".replace(",", ".") if isinstance(value, int) else str(value)


def _table(headers: list[str], rows: list[list]) -> str:
    lines = ["| " + " | ".join(headers) + " |", "|" + "---|" * len(headers)]
    lines += ["| " + " | ".join(_n(c) for c in row) + " |" for row in rows]
    return "\n".join(lines)


async def build_report() -> tuple[str, bool]:
    settings = OsmSettings(statement_timeout_ms=_VERIFY_TIMEOUT_MS,
                           command_timeout_s=_VERIFY_TIMEOUT_MS / 1000 + 30)
    db = OsmDatabase(settings)
    repo = OsmRepository(db)
    try:
        active = await repo.get_active_version()
        v = active.version
        async with db.connection() as conn:
            versions = await conn.fetch(_SQL_VERSIONS)
            counts = await conn.fetchrow(_SQL_COUNTS, v)
            validity = await conn.fetchrow(_SQL_VALIDITY, v)
            global_dupes = await conn.fetchval(_SQL_GLOBAL_EDGE_IDS)
            highways = await conn.fetch(_SQL_HIGHWAY, v)
            try:
                weights = await conn.fetchrow(_SQL_EDGE_WEIGHTS)
            except Exception as exc:  # schema pollution absent or not readable
                weights = None
                weights_error = type(exc).__name__
        samples = {mode: await repo.read_road_graph(_SAMPLE_BBOX, mode) for mode in Mode}
    finally:
        await db.close()

    problems = {k: validity[k] for k in validity.keys() if validity[k]}
    if global_dupes:
        problems["edge_id_reused_across_versions"] = global_dupes
    if counts["nodes_without_geom"]:
        problems["nodes_without_geom"] = counts["nodes_without_geom"]
    for mode, g in samples.items():
        if any(not mode.allows(e) for e in g.edges):
            problems[f"sample_{mode.value}_contains_forbidden_edge"] = 1
    ok = not problems

    out = [
        "# Bukti Verifikasi RoadGraph (Backend → OSM Cache DB)",
        "",
        f"Dibuat oleh `python -m osm.verify` pada {datetime.now(timezone.utc):%Y-%m-%d %H:%M} UTC. "
        "Semua kueri baca-saja.",
        "",
        f"**Hasil: {'LULUS' if ok else 'ADA MASALAH'}**"
        + ("" if ok else f": {problems}"),
        "",
        "## Aturan penyaringan yang diterapkan Backend",
        "",
        "Izin akses per ruas dihitung saat import (`walk_allowed`, `bike_allowed`, `oneway`; aturannya "
        "di `handoff_database.md` bagian 6). Backend menerapkannya saat merakit graf:",
        "",
        "1. Hanya versi `active` yang dibaca (`osm.graph_version`).",
        "2. `walk`: hanya ruas `walk_allowed = true`; setiap ruas dapat dilalui dua arah (`oneway` diabaikan).",
        "3. `bike`: hanya ruas `bike_allowed = true`; `u → v` selalu, `v → u` hanya bila `oneway = false`.",
        "4. Ruas ditolak bila: tanpa geometri / < 2 titik, `length_m` kosong atau ≤ 0, node ujung tidak ada "
        "atau tanpa lokasi, ujung geometri tidak sama dengan node `u`/`v`, atau `edge_id` ganda "
        "(`osm/graph.py`, `assemble_road_graph`).",
        "5. Self-loop dibuang saat merakit subgraf permintaan; simpul asal/tujuan di-snap hanya ke simpul "
        "yang menyentuh ruas yang boleh dilalui moda (maks. 200 m).",
        "",
        "Uji akses moda (`pytest` di `src/backend/osm`): `tests/test_assemble.py` "
        "(ruas tanpa geometri, panjang tidak valid, perbedaan akses antar moda, satu arah, tujuan terisolasi, "
        "pemetaan EdgeWeight), `tests/test_graph.py` (UT-BE-04..06), `tests/test_models.py` (arah per moda), "
        "dan `tests/test_integration_db.py` (filter moda dan validitas ruas pada DB asli).",
        "",
        "## Metadata versi graf",
        "",
        _table(["version", "status", "osm_timestamp", "built_at", "node_count", "edge_count"],
               [[r["version"], r["status"], f"{r['osm_timestamp']:%Y-%m-%d %H:%M:%S}Z" if r["osm_timestamp"] else "-",
                 f"{r['built_at']:%Y-%m-%d %H:%M:%S}Z" if r["built_at"] else "-",
                 r["node_count"], r["edge_count"]] for r in versions]),
        "",
        f"Backend hanya membaca versi berstatus `active` (`{v}`); versi `building` dan `retired` diabaikan.",
        "",
        "## Statistik node dan ruas (versi aktif)",
        "",
        _table(["Item", "Jumlah"], [
            ["Node", counts["nodes"]],
            ["Node bertag (gerbang, lampu lalu lintas, dll.)", counts["nodes_tagged"]],
            ["Ruas", counts["edges"]],
            ["Total panjang (km)", int(counts["total_km"])],
            ["Ruas satu arah (`oneway`)", counts["oneway"]],
            ["Self-loop (`u = v`)", counts["self_loops"]],
        ]),
        "",
        "## Akses moda",
        "",
        _table(["Kategori", "Ruas"], [
            ["Boleh jalan kaki (`walk_allowed`)", counts["walk"]],
            ["Boleh sepeda (`bike_allowed`)", counts["bike"]],
            ["Keduanya", counts["walk_and_bike"]],
            ["Hanya jalan kaki", counts["walk_only"]],
            ["Hanya sepeda", counts["bike_only"]],
            ["Tidak keduanya (tol, busway, private, dll.)", counts["neither"]],
            ["Satu arah dan boleh sepeda (arah `v → u` ditutup untuk sepeda)", counts["oneway_bike"]],
        ]),
        "",
        "<details><summary>Per tipe jalan (<code>highway</code>)</summary>",
        "",
        _table(["highway", "ruas", "walk", "bike"],
               [[r["highway"], r["edges"], r["walk"], r["bike"]] for r in highways]),
        "",
        "</details>",
        "",
        "## Validitas ruas (seluruh versi aktif)",
        "",
        "Aturan yang sama dipakai `assemble_road_graph` saat merakit graf; ruas yang gagal tidak "
        "dimasukkan ke `RoadGraph` dan dihitung di `rejected`.",
        "",
        _table(["Pemeriksaan", "Ruas bermasalah"], [
            ["Tanpa geometri atau < 2 titik", validity["missing_geometry"]],
            ["`length_m` kosong, ≤ 0, atau tak hingga", validity["invalid_length"]],
            ["Node ujung `u`/`v` tidak ada atau tanpa lokasi", validity["missing_endpoint_node"]],
            ["Ujung geometri ≠ lokasi node `u`/`v` (toleransi 1e-5°)", validity["geometry_endpoint_mismatch"]],
            ["`length_m` beda > max(1 m, 1%) dari panjang geometri", validity["length_differs_from_geometry"]],
            ["`edge_id` ganda dalam versi", validity["duplicate_edge_id"]],
            ["`edge_id` dipakai ulang antar versi", global_dupes],
            ["Node tanpa geometri", counts["nodes_without_geom"]],
        ]),
        "",
        "## Pemetaan ke EdgeWeight",
        "",
        "`EdgeWeight` (`pollution.edge_pollution`) dirujuk lewat `edge_id` saja; karena `edge_id` unik "
        "secara global, setiap baris memetakan ke tepat satu ruas (`RoadGraph.edges_by_id()`).",
        "",
        (_table(["Item", "Jumlah"], [["Baris `pollution.edge_pollution`", weights["rows"]],
                                     ["Baris tanpa ruas yang cocok", weights["orphan_rows"]]])
         if weights is not None else f"Schema `pollution` tidak dapat dibaca ({weights_error})."),
        "",
        "## Contoh `read_road_graph` (±1 × 1 km sekitar Monas)",
        "",
        _table(["Moda", "Node", "Ruas", "Ruas ditolak validasi", "Ruas terlarang ikut terbaca"],
               [[m.value, len(g.nodes), len(g.edges), g.rejected or "-",
                 sum(1 for e in g.edges if not m.allows(e))] for m, g in samples.items()]),
        "",
    ]
    return "\n".join(out), ok


def main() -> int:
    try:
        report, ok = asyncio.run(build_report())
    except CacheUnavailable as exc:
        print(f"OSM Cache DB tidak dapat dibaca: {exc.message}", file=sys.stderr)
        return 2
    print(report)
    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
