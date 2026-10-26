"""Load the controlled E2E dataset into an empty database.

Runs inside the Data Worker image (docker compose run --rm fixture), so the
schemas come from their owners' code: osm and pollution from
osm_cache.schema_sql() (the Railway DDL), ground_truth from spku.Store.

    python /e2e/fixture/load_fixture.py            # DATABASE_URL from the environment

The fixture is checked against manifest.json before anything is written. The
graph keeps its real version name; node_count/edge_count are set to the subset.
"""
from __future__ import annotations

import gzip
import hashlib
import json
import os
import pathlib
import sys

import psycopg

HERE = pathlib.Path(__file__).resolve().parent
sys.path.insert(0, "/app")  # Data Worker code inside its image

from osm_cache import schema_sql  # noqa: E402
from spku.store import Store  # noqa: E402

TABLES = [
    ("graph_version.csv", "osm.graph_version",
     "version, status, osm_timestamp, built_at, node_count, edge_count"),
    ("road_node.csv.gz", "osm.road_node", "node_id, graph_version, geom, tags"),
    ("road_edge.csv.gz", "osm.road_edge",
     "edge_id, graph_version, u, v, length_m, highway, walk_allowed, bike_allowed, geom, osm_way_id, name, oneway, tags"),
    ("gt_station.csv", "ground_truth.station",
     "uuid, kode, initial, name, type, lat, lng, kecamatan, kota, alamat, dioperasikan, first_seen_utc, "
     "last_seen_utc, active"),
    ("gt_observation.csv.gz", "ground_truth.observation", "station_uuid, metric, ts_utc, value, qc, first_seen_utc"),
]


def verify(manifest: dict) -> None:
    for f in manifest["files"]:
        digest = hashlib.sha256((HERE / f["file"]).read_bytes()).hexdigest()
        if digest != f["sha256"]:
            raise SystemExit(f"{f['file']}: checksum tidak cocok dengan manifest.json")


def main() -> None:
    manifest = json.loads((HERE / "manifest.json").read_text(encoding="utf-8"))
    verify(manifest)
    dsn = os.environ["DATABASE_URL"]
    with psycopg.connect(dsn, autocommit=True) as conn:
        conn.execute(schema_sql())
    Store(dsn).close()  # creates schema ground_truth from spku/schema.sql

    with psycopg.connect(dsn) as conn:
        for name, table, columns in TABLES:
            raw = (HERE / name).read_bytes()
            data = gzip.decompress(raw) if name.endswith(".gz") else raw
            with conn.cursor().copy(f"COPY {table} ({columns}) FROM STDIN WITH (FORMAT csv, HEADER true)") as copy:
                copy.write(data)
        conn.execute("""UPDATE osm.graph_version g SET
                            node_count = (SELECT count(*) FROM osm.road_node n WHERE n.graph_version = g.version),
                            edge_count = (SELECT count(*) FROM osm.road_edge e WHERE e.graph_version = g.version)""")
        counts = conn.execute("""SELECT (SELECT count(*) FROM osm.road_edge), (SELECT count(*) FROM osm.road_node),
                                        (SELECT count(*) FROM ground_truth.station),
                                        (SELECT count(*) FROM ground_truth.observation)""").fetchone()
    print(json.dumps({"dataset_version": manifest["dataset_version"], "graph_version": manifest["graph_version"],
                      "road_edge": counts[0], "road_node": counts[1], "station": counts[2], "observation": counts[3]}))


if __name__ == "__main__":
    main()
