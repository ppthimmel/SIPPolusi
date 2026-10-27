"""Export the controlled E2E dataset from a source database (read-only).

    SOURCE_DATABASE_URL=postgresql://... python tests/e2e/fixture/export_fixture.py \
        --bbox 106.80,-6.20,106.845,-6.155 --window 2026-10-26T12:00Z --hours 3

Writes, next to this file:
    graph_version.csv        the active graph_version row
    road_edge.csv.gz         edges of that version whose geometry touches bbox
    road_node.csv.gz         every node those edges reference
    gt_station.csv           all SPKU stations (no generated columns)
    gt_observation.csv.gz    PM25 and NO2 observations in [window, window + hours)
    manifest.json            source, parameters, row counts and SHA-256 of each file

The session is read-only; nothing is written to the source. The DSN is read from
the environment and never printed.
"""
from __future__ import annotations

import argparse
import datetime as dt
import gzip
import hashlib
import io
import json
import os
import pathlib

import psycopg

HERE = pathlib.Path(__file__).resolve().parent

EDGES = """
SELECT edge_id, graph_version, u, v, length_m, highway, walk_allowed, bike_allowed,
       encode(ST_AsEWKB(geom), 'hex') AS geom, osm_way_id, name, oneway, tags
FROM osm.road_edge
WHERE graph_version = %(version)s AND geom && ST_MakeEnvelope(%(x0)s, %(y0)s, %(x1)s, %(y1)s, 4326)
ORDER BY edge_id
"""
NODES = """
SELECT n.node_id, n.graph_version, encode(ST_AsEWKB(n.geom), 'hex') AS geom, n.tags
FROM osm.road_node n
WHERE n.graph_version = %(version)s AND n.node_id IN (
    SELECT u FROM osm.road_edge e WHERE e.graph_version = %(version)s
        AND e.geom && ST_MakeEnvelope(%(x0)s, %(y0)s, %(x1)s, %(y1)s, 4326)
    UNION
    SELECT v FROM osm.road_edge e WHERE e.graph_version = %(version)s
        AND e.geom && ST_MakeEnvelope(%(x0)s, %(y0)s, %(x1)s, %(y1)s, 4326))
ORDER BY n.node_id
"""
STATIONS = """
SELECT uuid, kode, initial, name, type, lat, lng, kecamatan, kota, alamat, dioperasikan,
       first_seen_utc, last_seen_utc, active
FROM ground_truth.station ORDER BY uuid
"""
OBSERVATIONS = """
SELECT station_uuid, metric, ts_utc, value, qc, first_seen_utc
FROM ground_truth.observation
WHERE metric IN ('PM25', 'NO2') AND ts_utc >= %(start)s AND ts_utc < %(end)s
ORDER BY ts_utc, station_uuid, metric
"""


def copy_out(cur, query: str, params: dict) -> tuple[bytes, int]:
    """COPY (query) TO STDOUT as CSV with header; bytes and data row count."""
    buf = io.BytesIO()
    with cur.copy(f"COPY ({cur.mogrify(query, params)}) TO STDOUT WITH (FORMAT csv, HEADER true)") as copy:
        for chunk in copy:
            buf.write(chunk)
    data = buf.getvalue()
    return data, max(data.count(b"\n") - 1, 0)


def write(name: str, data: bytes) -> dict:
    path = HERE / name
    payload = gzip.compress(data, mtime=0) if name.endswith(".gz") else data
    path.write_bytes(payload)
    return {"file": name, "sha256": hashlib.sha256(payload).hexdigest(), "bytes": len(payload)}


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--bbox", required=True, help="min_lon,min_lat,max_lon,max_lat")
    parser.add_argument("--window", required=True, help="first hour, e.g. 2026-10-26T12:00Z")
    parser.add_argument("--hours", type=int, default=3)
    args = parser.parse_args()

    x0, y0, x1, y1 = (float(v) for v in args.bbox.split(","))
    start = dt.datetime.fromisoformat(args.window.replace("Z", "+00:00"))
    end = start + dt.timedelta(hours=args.hours)

    dsn = os.environ["SOURCE_DATABASE_URL"]
    files = []
    with psycopg.connect(dsn, options="-c default_transaction_read_only=on -c statement_timeout=60000") as conn:
        conn.read_only = True
        with psycopg.ClientCursor(conn) as cur:  # client-side binding: mogrify for COPY (query)
            cur.execute("SELECT version, osm_timestamp FROM osm.graph_version WHERE status = 'active'")
            version, osm_timestamp = cur.fetchone()
            box = {"version": version, "x0": x0, "y0": y0, "x1": x1, "y1": y1}
            graph, _ = copy_out(cur, "SELECT version, status, osm_timestamp, built_at, node_count, edge_count "
                                     "FROM osm.graph_version WHERE version = %(version)s", box)
            files.append({**write("graph_version.csv", graph), "rows": 1})
            for name, query, params in [
                ("road_edge.csv.gz", EDGES, box),
                ("road_node.csv.gz", NODES, box),
                ("gt_station.csv", STATIONS, {}),
                ("gt_observation.csv.gz", OBSERVATIONS, {"start": start, "end": end}),
            ]:
                data, rows = copy_out(cur, query, params)
                files.append({**write(name, data), "rows": rows})

    manifest = {
        "dataset": "sippolusi-e2e",
        "exported_at": dt.datetime.now(dt.timezone.utc).isoformat(timespec="seconds"),
        "source": "Railway PostgreSQL, environment development (read-only export)",
        "graph_version": version,
        "osm_timestamp": osm_timestamp.isoformat() if osm_timestamp else None,
        "bbox": [x0, y0, x1, y1],
        "ground_truth_windows": [(start + dt.timedelta(hours=h)).strftime("%Y-%m-%dT%H:%MZ") for h in range(args.hours)],
        "metrics": ["PM25", "NO2"],
        "files": files,
        "license": "Road data © OpenStreetMap contributors, ODbL. Air quality: SPKU, DLH DKI Jakarta (udara.jakarta.go.id).",
    }
    # Content version of the dataset: one hash over the file hashes, in order.
    manifest["dataset_version"] = hashlib.sha256("".join(f["sha256"] for f in files).encode()).hexdigest()[:12]
    (HERE / "manifest.json").write_text(json.dumps(manifest, indent=2) + "\n", encoding="utf-8")
    print(json.dumps({k: manifest[k] for k in ("graph_version", "dataset_version", "ground_truth_windows")}))
    for f in files:
        print(f"{f['file']:<24} {f['rows']:>7} rows {f['bytes']:>10} bytes")


if __name__ == "__main__":
    main()
