"""Deterministic fastest walking/cycling baseline; see README.md for the contract."""
from __future__ import annotations

import argparse
import csv
import gzip
import heapq
import json
import math
import os
import threading
import time
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path

import networkx as nx

if __package__:
    from .route_metrics import ExposureSnapshot, evaluate_route
else:  # Preserve direct invocation of astar_osm.py as a CLI.
    from route_metrics import ExposureSnapshot, evaluate_route

DEFAULT_GRAPH = Path(__file__).resolve().parents[2] / "data_worker/dataset_processed/OSM"
SPEED_MPS = {"walk": 5 / 3.6, "bike": 15 / 3.6}
EARTH_RADIUS_M = 6_371_008.8
_LOAD_LOCK = threading.Lock()


def distance_m(a, b):
    lon1, lat1 = map(math.radians, a)
    lon2, lat2 = map(math.radians, b)
    h = math.sin((lat2-lat1)/2)**2 + math.cos(lat1)*math.cos(lat2)*math.sin((lon2-lon1)/2)**2
    return 2 * EARTH_RADIUS_M * math.asin(math.sqrt(min(1.0, max(0.0, h))))


def point(value):
    """Validate a public Point: {lon: degrees, lat: degrees}."""
    if not isinstance(value, dict):
        raise ValueError("Coordinates must contain lon and lat")
    lon, lat = value.get("lon"), value.get("lat")
    if any(isinstance(x, bool) or not isinstance(x, (int, float)) or not math.isfinite(x)
           for x in (lon, lat)) or not (-180 <= lon <= 180 and -90 <= lat <= 90):
        raise ValueError("Coordinates must be finite longitude/latitude in degrees")
    return float(lon), float(lat)


def geometry(wkt):
    if not isinstance(wkt, str) or not wkt.startswith("LINESTRING(") or not wkt.endswith(")"):
        raise ValueError("Expected LINESTRING geometry")
    result = [list(map(float, pair.split())) for pair in wkt[11:-1].split(",")]
    if len(result) < 2:
        raise ValueError("A road geometry needs at least two points")
    for xy in result:
        if len(xy) != 2:
            raise ValueError("Expected two-dimensional geometry")
        point(dict(zip(("lon", "lat"), xy)))
    return result


@dataclass(frozen=True, slots=True)
class Edge:
    edge_id: int
    u: int
    v: int
    length_m: float
    time_s: float
    wkt: str


class RoadRouter:
    def __init__(self, version, mode, coords, edges):
        self.version, self.mode, self.coords = version, mode, coords
        self.edges = {}
        self.arcs = {}
        self.incoming = set()
        self.scale = math.inf
        # Each item is (Edge, bidirectional). Sort before search to break ties
        # consistently even when the export's row order changes.
        for edge, bidirectional in sorted(edges, key=lambda item: item[0].edge_id):
            if edge.edge_id in self.edges:
                raise ValueError("Duplicate edge")
            if edge.u not in coords or edge.v not in coords:
                raise ValueError("Edge references missing node")
            if not all(math.isfinite(x) and x > 0 for x in (edge.length_m, edge.time_s)):
                raise ValueError("Edge length and time must be positive and finite")
            geometry(edge.wkt)
            self.edges[edge.edge_id] = edge
            if edge.u == edge.v:
                continue
            self.arcs.setdefault(edge.u, []).append((edge.v, edge.time_s, edge.edge_id, True))
            self.incoming.add(edge.v)
            if bidirectional:
                self.arcs.setdefault(edge.v, []).append((edge.u, edge.time_s, edge.edge_id, False))
                self.incoming.add(edge.u)
            straight = distance_m(coords[edge.u], coords[edge.v])
            if straight:
                self.scale = min(self.scale, edge.time_s / straight)
        if not self.arcs:
            raise ValueError("No usable edges for this mode")
        self.scale = self.scale if math.isfinite(self.scale) else 0.0
        for arcs in self.arcs.values():
            arcs.sort()
        directed = nx.DiGraph((u, v) for u, arcs in self.arcs.items() for v, *_ in arcs)
        components = sorted(nx.strongly_connected_components(directed), key=min)
        self.component = {node: i for i, nodes in enumerate(components) for node in nodes}
        self.dag = {}
        for u, v in directed.edges:
            a, b = self.component[u], self.component[v]
            if a != b:
                self.dag.setdefault(a, set()).add(b)
        self.component_count = len(components)

    def candidates(self, xy, nodes, radius):
        # Latitude bound avoids most trigonometry without excluding valid nodes.
        lat_bound = math.degrees(radius / EARTH_RADIUS_M)
        result = []
        for node in nodes:
            if abs(self.coords[node][1] - xy[1]) <= lat_bound:
                separation = distance_m(xy, self.coords[node])
                if separation <= radius:
                    result.append((separation, node))
        return sorted(result)

    def snap_pair(self, origin, destination, radius):
        starts = self.candidates(origin, self.arcs, radius)
        ends = self.candidates(destination, self.incoming, radius)
        if not starts or not ends:
            return "snap_failed", None
        # Lexicographic policy: nearest feasible origin, then nearest reachable
        # destination, with node ID as tie-breaker. No restriction to largest SCC.
        failed = set()
        for start_distance, start in starts:
            component = self.component[start]
            if component in failed:
                continue
            reachable, stack = {component}, [component]
            while stack:
                for neighbor in self.dag.get(stack.pop(), ()):
                    if neighbor not in reachable:
                        reachable.add(neighbor)
                        stack.append(neighbor)
            for end_distance, end in ends:
                if self.component[end] in reachable:
                    return "ok", (start, end, start_distance, end_distance)
            failed.add(component)
        return "no_route", None

    def search(self, start, end):
        """A*: seconds; h is consistent since every arc cost >= scale*distance."""
        heuristic = lambda node: self.scale * distance_m(self.coords[node], self.coords[end])
        queue = [(heuristic(start), 0.0, start)]
        best, previous, settled = {start: 0.0}, {}, set()
        while queue:
            _, cost, node = heapq.heappop(queue)
            if node in settled or cost > best[node]:
                continue
            settled.add(node)
            if node == end:
                path = []
                while node != start:
                    parent, edge_id, forward = previous[node]
                    path.append((edge_id, forward))
                    node = parent
                return path[::-1], cost, len(settled)
            for neighbor, seconds, edge_id, forward in self.arcs.get(node, ()):
                candidate = cost + seconds
                if candidate < best.get(neighbor, math.inf):
                    best[neighbor] = candidate
                    previous[neighbor] = (node, edge_id, forward)
                    heapq.heappush(queue, (candidate + heuristic(neighbor), candidate, neighbor))
        return None, None, len(settled)

    def solve(self, origin, destination, radius=300, exposure=None):
        result = {"status": "ok", "route": None, "graph_version": self.version,
                  "mode": self.mode, "route_source": "baseline", "preference_applied": False,
                  "metrics": None, "visited_nodes": 0,
                  "graph_components": self.component_count}
        if origin == destination:
            result["status"] = "same_location" if self.candidates(origin, self.component, radius) else "snap_failed"
            return result
        status, snapped = self.snap_pair(origin, destination, radius)
        result["status"] = status
        if snapped is None:
            return result
        start, end, sd, ed = snapped
        result["snap"] = {"start_node": start, "end_node": end,
                          "start_distance_m": sd, "end_distance_m": ed}
        if start == end:
            result["status"] = "same_location" if origin == destination else "same_node"
            return result
        path, _, visited = self.search(start, end)
        result["visited_nodes"] = visited
        if path is None:
            result["status"] = "no_route"
            return result
        # Search is complete. Shared evaluation can also score a policy's route.
        result["metrics"] = evaluate_route(
            [self.edges[edge_id] for edge_id, _ in path], self.version, exposure)
        features = []
        for edge_id, forward in path:
            edge = self.edges[edge_id]
            coordinates = geometry(edge.wkt)
            features.append({"type": "Feature", "geometry": {"type": "LineString",
                "coordinates": coordinates if forward else coordinates[::-1]},
                "properties": {"edge_id": edge_id, "length_m": edge.length_m,
                    "travel_time_s": edge.time_s, "from_node": edge.u if forward else edge.v,
                    "to_node": edge.v if forward else edge.u}})
        result["route"] = {"type": "FeatureCollection", "features": features,
            "properties": {"graph_version": self.version, "mode": self.mode,
                           "route_source": "baseline", **result["metrics"]}}
        return result


def boolean(value):
    if not isinstance(value, str) or value.lower() not in ("t", "f", "true", "false"):
        raise ValueError("Invalid boolean in graph export")
    return value.lower() in ("t", "true")


def load_graph(folder, mode):
    if mode not in SPEED_MPS:
        raise ValueError("Unsupported mode")
    folder = Path(folder)
    with (folder / "graph_version.csv").open(newline="", encoding="utf-8-sig") as stream:
        active = [row for row in csv.DictReader(stream) if row["status"] == "active"]
    if len(active) != 1:
        raise ValueError("Expected exactly one active graph version")
    version = active[0]["version"]
    coords = {}
    with gzip.open(folder / "road_node.csv.gz", "rt", newline="", encoding="utf-8-sig") as stream:
        for row in csv.DictReader(stream):
            if row["graph_version"] != version:
                continue
            node = int(row["node_id"])
            if node in coords:
                raise ValueError("Duplicate node")
            coords[node] = point({"lon": float(row["lon"]), "lat": float(row["lat"])})
    if len(coords) != int(active[0]["node_count"]):
        raise ValueError("Node count mismatch")
    edges, seen = [], set()
    with gzip.open(folder / "road_edge.csv.gz", "rt", newline="", encoding="utf-8-sig") as stream:
        for row in csv.DictReader(stream):
            if row["graph_version"] != version:
                continue
            edge_id = int(row["edge_id"])
            if edge_id in seen:
                raise ValueError("Duplicate edge")
            seen.add(edge_id)
            if not boolean(row["walk_allowed" if mode == "walk" else "bike_allowed"]):
                continue
            length = float(row["length_m"])
            explicit = row.get(mode + "_time_s")
            seconds = float(explicit) if explicit else length / SPEED_MPS[mode]
            edges.append((Edge(edge_id, int(row["u"]), int(row["v"]), length, seconds, row["geom_wkt"]),
                          mode == "walk" or not boolean(row["oneway"])))
    if len(seen) != int(active[0]["edge_count"]):
        raise ValueError("Edge count mismatch")
    return RoadRouter(version, mode, coords, edges)


@lru_cache(maxsize=2)
def _cached_graph(folder, mode, signature):
    return load_graph(folder, mode)


def get_router(folder, mode):
    folder = Path(folder).resolve()
    # Export replacements invalidate the cache; deploy datasets atomically.
    signature = tuple((p.stat().st_mtime_ns, p.stat().st_size) for p in
                      (folder / name for name in ("graph_version.csv", "road_node.csv.gz", "road_edge.csv.gz")))
    with _LOAD_LOCK:
        return _cached_graph(folder, mode, signature)


def solve_baseline(origin, destination, mode, *, graph_dir=None, max_snap_m=300,
                   exposure=None, router=None, fallback_reason=None):
    """Public backend/experiment entry point. Runtime diagnostics are outside GeoJSON."""
    started = time.perf_counter()
    result = {"status": "invalid_input", "route": None, "metrics": None,
              "route_source": "fallback" if fallback_reason else "baseline",
              "preference_applied": False}
    try:
        origin, destination = point(origin), point(destination)
        if mode not in SPEED_MPS or isinstance(max_snap_m, bool) or not isinstance(max_snap_m, (int, float)) or not 0 < max_snap_m < math.inf:
            raise ValueError("Mode must be walk/bike and snap radius must be positive and finite")
        if router is not None and router.mode != mode:
            raise ValueError("Router mode does not match request")
    except (ValueError, TypeError) as exc:
        result["message"] = str(exc)
    else:
        try:
            router = router or get_router(graph_dir or os.environ.get("OSM_GRAPH_DIR", DEFAULT_GRAPH), mode)
        except (OSError, EOFError):
            result.update(status="data_unavailable", message="OSM graph export is unavailable")
        except (ValueError, KeyError, TypeError, csv.Error):
            result.update(status="invalid_graph", message="OSM graph export failed validation")
        else:
            try:
                result = router.solve(origin, destination, max_snap_m, exposure)
            except ValueError as exc:
                result.update(status="invalid_input", message=str(exc))
    if fallback_reason:
        result.update(route_source="fallback", fallback_reason=fallback_reason)
        if result.get("route"):
            result["route"]["properties"].update(route_source="fallback", fallback_reason=fallback_reason)
    result["diagnostics"] = {"computation_ms": (time.perf_counter() - started) * 1000}
    return result


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--graph-dir", type=Path)
    parser.add_argument("--mode", required=True)
    for name in ("start-lat", "start-lon", "end-lat", "end-lon"):
        parser.add_argument("--" + name, type=float, required=True)
    parser.add_argument("--max-snap-m", type=float, default=300)
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()
    result = solve_baseline({"lon": args.start_lon, "lat": args.start_lat},
        {"lon": args.end_lon, "lat": args.end_lat}, args.mode,
        graph_dir=args.graph_dir, max_snap_m=args.max_snap_m)
    if args.output:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(json.dumps(result["route"] or result, ensure_ascii=False, allow_nan=False), encoding="utf-8")
    print(json.dumps(result, ensure_ascii=False, allow_nan=False))
    return 0 if result["status"] in ("ok", "same_location") else 2


if __name__ == "__main__":
    raise SystemExit(main())
