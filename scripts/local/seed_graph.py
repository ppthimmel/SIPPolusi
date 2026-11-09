"""Seed an empty local database with the schemas and the road graph Railway development uses.

Runs inside the Data Worker image, which has the graph files mounted at /app/dataset_processed:

    docker compose --profile tools run --rm seed_graph

Schemas come from their owners' code (osm/pollution from osm_cache.schema_sql(), ground_truth from
spku.Store), as in tests/e2e/fixture/load_fixture.py. The graph is written through
osm_cache.write_road_graph, the only supported import path, and keeps the edge ids of the Railway graph
(jakarta-20261005, edge ids 1..373352): the registered route policy refers to those ids. A graph version
that already exists is left alone, so running this twice is safe.
"""
from __future__ import annotations

import csv
import datetime as dt
import gzip
import json
import os
import pathlib
import sys

import psycopg

sys.path.insert(0, "/app")  # Data Worker code inside its image
from osm_cache import GraphEdge, GraphNode, RoadGraphData, schema_sql, write_road_graph  # noqa: E402
from spku.store import Store  # noqa: E402

GRAPH_DIR = pathlib.Path(os.environ.get("SEED_GRAPH_DIR", "/app/dataset_processed/OSM"))
csv.field_size_limit(1 << 30)


def _bool(value: str) -> bool:
    return value.strip().lower() in ("t", "true", "1")


def _linestring(wkt: str) -> tuple[tuple[float, float], ...]:
    body = wkt[wkt.index("(") + 1:wkt.rindex(")")]
    return tuple((float(x), float(y)) for x, y in (point.split() for point in body.split(",")))


def read_graph() -> RoadGraphData:
    with open(GRAPH_DIR / "graph_version.csv", encoding="utf-8", newline="") as handle:
        meta = next(csv.DictReader(handle))
    with gzip.open(GRAPH_DIR / "road_node.csv.gz", "rt", encoding="utf-8", newline="") as handle:
        nodes = [GraphNode(int(r["node_id"]), float(r["lon"]), float(r["lat"]), json.loads(r["tags"] or "{}"))
                 for r in csv.DictReader(handle) if r["graph_version"] == meta["version"]]
    with gzip.open(GRAPH_DIR / "road_edge.csv.gz", "rt", encoding="utf-8", newline="") as handle:
        edges = [GraphEdge(int(r["edge_id"]), int(r["u"]), int(r["v"]), float(r["length_m"]), r["highway"] or None,
                           _bool(r["walk_allowed"]), _bool(r["bike_allowed"]), _linestring(r["geom_wkt"]),
                           osm_way_id=int(r["osm_way_id"]) if r["osm_way_id"] else None, name=r["name"] or None,
                           oneway=_bool(r["oneway"]), tags=json.loads(r["tags"] or "{}"))
                 for r in csv.DictReader(handle) if r["graph_version"] == meta["version"]]
    stamp = meta.get("osm_timestamp") or None
    osm_timestamp = dt.datetime.fromisoformat(stamp.replace("+00", "+00:00")) if stamp else None
    return RoadGraphData(version=meta["version"], nodes=nodes, edges=edges, osm_timestamp=osm_timestamp)


def main() -> None:
    dsn = os.environ["DATABASE_URL"]
    with psycopg.connect(dsn, autocommit=True) as conn:
        conn.execute(schema_sql())
    Store(dsn).close()  # creates schema ground_truth from spku/schema.sql

    with open(GRAPH_DIR / "graph_version.csv", encoding="utf-8", newline="") as handle:
        version = next(csv.DictReader(handle))["version"]
    with psycopg.connect(dsn, autocommit=True) as conn:
        existing = conn.execute("SELECT status FROM osm.graph_version WHERE version = %s", (version,)).fetchone()
        if existing:
            print(json.dumps({"graph_version": version, "status": existing[0], "note": "already present, not rewritten"}))
            return
        graph = read_graph()
        print(f"writing {version}: {len(graph.nodes)} nodes, {len(graph.edges)} edges", flush=True)
        result = write_road_graph(conn, graph)
    print(json.dumps({"graph_version": result.version, "node_count": result.node_count, "edge_count": result.edge_count,
                      "largest_component_ratio": round(result.largest_component_ratio, 4)}))


if __name__ == "__main__":
    main()
