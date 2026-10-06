"""Shortest walking/cycling route on the exported Jakarta OSM graph.

Run ``python code/astar_osm.py --help`` for examples and options.
"""

from __future__ import annotations

import argparse
import csv
import gzip
import heapq
import json
import math
from collections import defaultdict
from pathlib import Path


DEFAULT_GRAPH = (
    Path(__file__).resolve().parents[2]
    / "data_worker"
    / "dataset_processed"
    / "OSM"
)
EARTH_RADIUS_M = 6_371_008.8


def distance_m(a: tuple[float, float], b: tuple[float, float]) -> float:
    """Haversine distance for (longitude, latitude) points."""
    lon1, lat1 = map(math.radians, a)
    lon2, lat2 = map(math.radians, b)
    dlat, dlon = lat2 - lat1, lon2 - lon1
    h = math.sin(dlat / 2) ** 2 + math.cos(lat1) * math.cos(lat2) * math.sin(dlon / 2) ** 2
    return 2 * EARTH_RADIUS_M * math.asin(min(1.0, math.sqrt(h)))


def active_version(folder: Path) -> tuple[str, int, int]:
    with (folder / "graph_version.csv").open(newline="", encoding="utf-8-sig") as stream:
        active = [row for row in csv.DictReader(stream) if row["status"] == "active"]
    if len(active) != 1:
        raise ValueError(f"Expected one active graph version, found {len(active)}")
    row = active[0]
    return row["version"], int(row["node_count"]), int(row["edge_count"])


def load_graph(folder: Path, mode: str):
    version, expected_nodes, expected_edges = active_version(folder)
    coords: dict[int, tuple[float, float]] = {}
    with gzip.open(folder / "road_node.csv.gz", "rt", newline="", encoding="utf-8-sig") as stream:
        for row in csv.DictReader(stream):
            if row["graph_version"] != version:
                continue
            node = int(row["node_id"])
            if node in coords:
                raise ValueError(f"Duplicate node {node}")
            coords[node] = (float(row["lon"]), float(row["lat"]))
    if len(coords) != expected_nodes:
        raise ValueError(f"Node count mismatch: {len(coords)} versus {expected_nodes}")

    # Arcs contain (neighbor, distance in meters, edge_id, follows stored u->v).
    graph: dict[int, list[tuple[int, float, int, bool]]] = defaultdict(list)
    edges_read = eligible = 0
    seen_edges: set[int] = set()
    heuristic_scale = 1.0
    with gzip.open(folder / "road_edge.csv.gz", "rt", newline="", encoding="utf-8-sig") as stream:
        for row in csv.DictReader(stream):
            if row["graph_version"] != version:
                continue
            edges_read += 1
            edge_id = int(row["edge_id"])
            if edge_id in seen_edges:
                raise ValueError(f"Duplicate edge {edge_id}")
            seen_edges.add(edge_id)
            if row["walk_allowed" if mode == "walk" else "bike_allowed"].lower() != "t":
                continue
            u, v = int(row["u"]), int(row["v"])
            if u not in coords or v not in coords:
                raise ValueError(f"Edge {edge_id} refers to a missing node")
            length = float(row["length_m"])
            if not math.isfinite(length) or length <= 0:
                raise ValueError(f"Edge {edge_id} has invalid length")
            if u == v:
                continue  # Positive-cost self-loops cannot improve a shortest path.
            eligible += 1
            straight = distance_m(coords[u], coords[v])
            if straight > 0:
                heuristic_scale = min(heuristic_scale, length / straight)
            graph[u].append((v, length, edge_id, True))
            if mode == "walk" or row["oneway"].lower() != "t":
                graph[v].append((u, length, edge_id, False))
    if edges_read != expected_edges:
        raise ValueError(f"Edge count mismatch: {edges_read} versus {expected_edges}")
    if not eligible:
        raise ValueError(f"No {mode} edges in graph")
    # Each edge cost is at least scale times its straight-line distance. By
    # triangle inequality, this makes the A* heuristic admissible and consistent.
    heuristic_scale = max(0.0, min(1.0, heuristic_scale))
    return version, coords, graph, heuristic_scale


def snap(point: tuple[float, float], coords, graph, max_distance_m: float):
    # An endpoint with only incoming one-way arcs can be a valid destination;
    # use all incident nodes, not just graph keys with outgoing arcs.
    candidates = set(graph)
    for arcs in graph.values():
        candidates.update(v for v, *_ in arcs)
    lat_radians = math.radians(point[1])
    best = min(candidates, key=lambda n: (
        (coords[n][0] - point[0]) ** 2 * math.cos(lat_radians) ** 2
        + (coords[n][1] - point[1]) ** 2))
    separation = distance_m(point, coords[best])
    if separation > max_distance_m:
        raise ValueError(
            f"Nearest routable node is {separation:.0f} m away, beyond "
            f"--max-snap-m {max_distance_m:g}; check coordinates or mode"
        )
    return best, separation


def astar(start, goal, coords, graph, heuristic_scale):
    if start == goal:
        return [], 0.0, 0
    heuristic = lambda n: heuristic_scale * distance_m(coords[n], coords[goal])
    queue = [(heuristic(start), 0.0, start)]
    best_cost = {start: 0.0}
    previous = {}
    settled = set()
    while queue:
        _, current_cost, node = heapq.heappop(queue)
        if node in settled or current_cost > best_cost[node]:
            continue
        settled.add(node)
        if node == goal:
            path = []
            while node != start:
                parent, edge_id, forward, edge_length = previous[node]
                path.append((edge_id, parent, node, forward, edge_length))
                node = parent
            path.reverse()
            return path, current_cost, len(settled)
        for neighbor, length, edge_id, forward in graph.get(node, ()):
            candidate = current_cost + length
            if candidate < best_cost.get(neighbor, math.inf):
                best_cost[neighbor] = candidate
                previous[neighbor] = (node, edge_id, forward, length)
                heapq.heappush(queue, (candidate + heuristic(neighbor), candidate, neighbor))
    raise ValueError("No route connects the snapped nodes for this travel mode")


def path_geojson(folder: Path, path, properties):
    wanted = {edge_id for edge_id, *_ in path}
    shapes = {}
    with gzip.open(folder / "road_edge.csv.gz", "rt", newline="", encoding="utf-8-sig") as stream:
        for row in csv.DictReader(stream):
            edge_id = int(row["edge_id"])
            if edge_id in wanted:
                wkt = row["geom_wkt"]
                if not wkt.startswith("LINESTRING(") or not wkt.endswith(")"):
                    raise ValueError(f"Unsupported geometry on edge {edge_id}")
                coordinates = [list(map(float, pair.strip().split()))
                               for pair in wkt[11:-1].split(",")]
                shapes[edge_id] = coordinates
    if len(shapes) != len(wanted):
        raise ValueError("Route geometry is missing from the edge export")
    features = []
    for order, (edge_id, u, v, forward, length) in enumerate(path, 1):
        coordinates = shapes[edge_id]
        if not forward:
            coordinates = coordinates[::-1]
        features.append({
            "type": "Feature",
            "geometry": {"type": "LineString", "coordinates": coordinates},
            "properties": {"order": order, "edge_id": edge_id,
                           "from_node": u, "to_node": v, "length_m": length},
        })
    return {"type": "FeatureCollection", "properties": properties, "features": features}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--graph-dir", type=Path, default=DEFAULT_GRAPH)
    parser.add_argument("--mode", choices=("walk", "bike"), required=True)
    parser.add_argument("--start-lat", type=float, required=True)
    parser.add_argument("--start-lon", type=float, required=True)
    parser.add_argument("--end-lat", type=float, required=True)
    parser.add_argument("--end-lon", type=float, required=True)
    parser.add_argument("--max-snap-m", type=float, default=300)
    parser.add_argument("--output", type=Path, help="Optional route GeoJSON file")
    args = parser.parse_args()
    if not (0 < args.max_snap_m < math.inf):
        parser.error("--max-snap-m must be positive and finite")
    if not all(math.isfinite(x) for x in (args.start_lat, args.start_lon, args.end_lat, args.end_lon)):
        parser.error("Coordinates must be finite")
    if not all(-90 <= x <= 90 for x in (args.start_lat, args.end_lat)) or not all(
        -180 <= x <= 180 for x in (args.start_lon, args.end_lon)
    ):
        parser.error("Coordinates are outside latitude/longitude bounds")
    version, coords, graph, scale = load_graph(args.graph_dir, args.mode)
    start, start_snap = snap((args.start_lon, args.start_lat), coords, graph, args.max_snap_m)
    end, end_snap = snap((args.end_lon, args.end_lat), coords, graph, args.max_snap_m)
    path, length, visited = astar(start, end, coords, graph, scale)
    result = {
        "graph_version": version,
        "mode": args.mode,
        "distance_m": round(length, 2),
        "edge_count": len(path),
        "start_node": start,
        "end_node": end,
        "start_snap_m": round(start_snap, 2),
        "end_snap_m": round(end_snap, 2),
        "visited_nodes": visited,
        "cost": "road distance in meters",
    }
    if args.output:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        geojson = path_geojson(args.graph_dir, path, result)
        args.output.write_text(json.dumps(geojson, ensure_ascii=False), encoding="utf-8")
        result["output"] = str(args.output)
    print(json.dumps(result, indent=2))


if __name__ == "__main__":
    main()
