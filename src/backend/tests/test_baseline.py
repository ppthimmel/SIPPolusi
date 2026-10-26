import csv
import gzip
import json
import random
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import networkx as nx

from src.backend.route_model.astar_osm import (
    Edge, ExposureSnapshot, RoadRouter, _cached_graph, get_router, load_graph, solve_baseline,
)
from src.backend.route_model.route_metrics import evaluate_route


def router(coords, specs, mode="bike"):
    edges = []
    for edge_id, u, v, length, seconds, two_way in specs:
        a, b = coords[u], coords[v]
        wkt = f"LINESTRING({a[0]} {a[1]},{b[0]} {b[1]})"
        edges.append((Edge(edge_id, u, v, length, seconds, wkt), two_way))
    return RoadRouter("test-v1", mode, coords, edges)


def solve(r, start, end, **kwargs):
    return solve_baseline(dict(zip(("lon", "lat"), r.coords[start])),
                          dict(zip(("lon", "lat"), r.coords[end])), r.mode, router=r, **kwargs)


class BaselineTests(unittest.TestCase):
    def setUp(self):
        self.coords = {1: (0, 0), 2: (.001, 0), 3: (.002, 0)}
        # The 400 m path takes 20 s; the direct 100 m path takes 100 s.
        self.r = router(self.coords, [(1, 1, 3, 100, 100, False),
                                     (2, 1, 2, 200, 10, False), (3, 2, 3, 200, 10, False)])

    def test_time_optimal_and_hand_calculated_exposure(self):
        snapshot = ExposureSnapshot("test-v1", "2026-10-10T00:00:00Z", "pm25", {2: 10, 3: 20})
        result = solve(self.r, 1, 3, exposure=snapshot, max_snap_m=1)
        self.assertEqual(result["status"], "ok")
        self.assertEqual(result["metrics"]["travel_time_s"], 20)
        self.assertEqual(result["metrics"]["length_m"], 400)
        self.assertEqual(result["metrics"]["exposure_ug_s_m3"], 300)

    def test_missing_partial_and_mismatched_pollution(self):
        self.assertIsNone(solve(self.r, 1, 3)["metrics"]["exposure_ug_s_m3"])
        partial = ExposureSnapshot("test-v1", "window", "pm25", {2: 10})
        metrics = solve(self.r, 1, 3, exposure=partial)["metrics"]
        self.assertEqual(metrics["exposure_status"], "partial")
        self.assertEqual(metrics["exposure_edge_coverage"], .5)
        self.assertIsNone(metrics["exposure_ug_s_m3"])
        wrong = ExposureSnapshot("different", "window", "pm25", {})
        self.assertEqual(solve(self.r, 1, 3, exposure=wrong)["status"], "invalid_input")
        for value in (-1, float("nan"), float("inf")):
            invalid = ExposureSnapshot("test-v1", "window", "pm25", {2: value})
            self.assertEqual(solve(self.r, 1, 3, exposure=invalid)["status"], "invalid_input")

    def test_exposure_cannot_change_the_fastest_route(self):
        clean = ExposureSnapshot("test-v1", "window", "pm25", {1: 1000, 2: 0, 3: 0})
        polluted = ExposureSnapshot("test-v1", "window", "pm25", {1: 0, 2: 1000, 3: 1000})
        first = solve(self.r, 1, 3, exposure=clean)
        second = solve(self.r, 1, 3, exposure=polluted)
        for result in (first, second):
            self.assertEqual([f["properties"]["edge_id"] for f in result["route"]["features"]], [2, 3])
            self.assertEqual(result["metrics"]["travel_time_s"], 20)
        self.assertEqual(first["metrics"]["exposure_ug_s_m3"], 0)
        self.assertEqual(second["metrics"]["exposure_ug_s_m3"], 20000)

    def test_shared_evaluator_can_score_another_selected_route(self):
        snapshot = ExposureSnapshot("test-v1", "window", "pm25", {1: 2})
        metrics = evaluate_route([self.r.edges[1]], "test-v1", snapshot)
        self.assertEqual(metrics["travel_time_s"], 100)
        self.assertEqual(metrics["length_m"], 100)
        self.assertEqual(metrics["exposure_ug_s_m3"], 200)

    def test_unreachable_nearest_destination_and_valid_sink(self):
        coords = {1: (0, 0), 2: (.01, 0), 3: (.0101, 0), 4: (.02, 0)}
        r = router(coords, [(1, 1, 3, 100, 10, False), (2, 4, 2, 100, 10, False)])
        result = solve(r, 1, 2, max_snap_m=30)
        self.assertEqual(result["status"], "ok")
        self.assertEqual(result["snap"]["end_node"], 3)
        # Node 3 has no outgoing arcs but is a valid destination.
        self.assertEqual(solve(r, 1, 3, max_snap_m=1)["status"], "ok")

    def test_alternative_origin_and_disconnected_graph(self):
        coords = {1: (0, 0), 2: (.0001, 0), 3: (.01, 0), 4: (-.01, 0)}
        r = router(coords, [(1, 1, 4, 100, 10, False), (2, 2, 3, 100, 10, False)])
        self.assertEqual(solve(r, 1, 3, max_snap_m=30)["snap"]["start_node"], 2)
        self.assertEqual(solve(r, 1, 3, max_snap_m=1)["status"], "no_route")

    def test_same_location_and_same_node_are_not_empty_success(self):
        r = router(self.coords, [(1, 1, 2, 100, 10, True)])
        self.assertEqual(solve(r, 1, 1)["status"], "same_location")
        result = solve_baseline({"lon": 0, "lat": 0}, {"lon": .000001, "lat": 0}, "bike", router=r)
        self.assertEqual(result["status"], "same_node")
        self.assertIsNone(result["route"])

    def test_outside_invalid_input_and_fallback(self):
        self.assertEqual(solve_baseline(None, {}, "bike")["status"], "invalid_input")
        for mode in ("car", None):
            self.assertEqual(solve_baseline({"lon": 0, "lat": 0}, {"lon": 0, "lat": 1}, mode)["status"], "invalid_input")
        result = solve_baseline({"lon": 170, "lat": 30}, {"lon": 171, "lat": 30}, "bike", router=self.r)
        self.assertEqual(result["status"], "snap_failed")
        result = solve(self.r, 1, 3, fallback_reason="policy_unavailable")
        self.assertEqual(result["route_source"], "fallback")
        self.assertEqual(result["route"]["properties"]["route_source"], "fallback")
        self.assertFalse(result["preference_applied"])

    def test_geojson_is_deterministic_and_reverse_geometry(self):
        r = router(self.coords, [(1, 1, 2, 100, 10, True), (2, 2, 3, 100, 10, True)])
        first, second = solve(r, 3, 1), solve(r, 3, 1)
        self.assertEqual(json.dumps(first["route"]), json.dumps(second["route"]))
        self.assertEqual(first["route"]["features"][0]["geometry"]["coordinates"][0], list(self.coords[3]))

    def test_astar_matches_independent_dijkstra(self):
        rng = random.Random(18)
        coords = {i: (i * .001, (i % 3) * .001) for i in range(20)}
        specs = [(i, i, (i+1) % 20, 100, rng.uniform(1, 30), False) for i in range(20)]
        specs += [(20+i, *rng.sample(range(20), 2), 100, rng.uniform(1, 30), False) for i in range(35)]
        r = router(coords, specs)
        g = nx.MultiDiGraph()
        for _, u, v, _, seconds, _ in specs:
            g.add_edge(u, v, weight=seconds)
        for start in range(20):
            for end in range(20):
                _, cost, _ = r.search(start, end)
                self.assertAlmostEqual(cost, nx.dijkstra_path_length(g, start, end))

    def test_row_order_ties_do_not_change_path(self):
        coords = {1: (0, 0), 2: (.001, .001), 3: (.001, -.001), 4: (.002, 0)}
        specs = [(1, 1, 2, 100, 10, False), (2, 2, 4, 100, 10, False),
                 (3, 1, 3, 100, 10, False), (4, 3, 4, 100, 10, False)]
        self.assertEqual(router(coords, specs).search(1, 4)[0], router(coords, specs[::-1]).search(1, 4)[0])


def write_export(folder, *, corrupt=False):
    (folder / "graph_version.csv").write_text("version,status,node_count,edge_count\ntest-v1,active,2,1\n")
    with gzip.open(folder / "road_node.csv.gz", "wt") as stream:
        stream.write("node_id,graph_version,lon,lat\n1,test-v1,0,0\n2,test-v1,.001,0\n")
    with gzip.open(folder / "road_edge.csv.gz", "wt", newline="") as stream:
        writer = csv.writer(stream)
        writer.writerow(["edge_id", "graph_version", "u", "v", "length_m", "walk_allowed", "bike_allowed", "oneway", "geom_wkt"])
        writer.writerow([1, "test-v1", 1, 2, -100 if corrupt else 100, "t", "t", "t", "LINESTRING(0 0,.001 0)"])


class ExportTests(unittest.TestCase):
    def test_mode_speeds_and_oneway(self):
        with tempfile.TemporaryDirectory() as tmp:
            folder = Path(tmp)
            write_export(folder)
            walk, bike = load_graph(folder, "walk"), load_graph(folder, "bike")
            self.assertAlmostEqual(walk.search(1, 2)[1], 72)
            self.assertAlmostEqual(bike.search(1, 2)[1], 24)
            self.assertIsNotNone(walk.search(2, 1)[0])
            self.assertIsNone(bike.search(2, 1)[0])
            edge_file = folder / "road_edge.csv.gz"
            with gzip.open(edge_file, "rt") as stream:
                rows = list(csv.DictReader(stream))
            rows[0]["bike_allowed"] = "f"
            with gzip.open(edge_file, "wt", newline="") as stream:
                writer = csv.DictWriter(stream, fieldnames=rows[0].keys())
                writer.writeheader()
                writer.writerows(rows)
            with self.assertRaisesRegex(ValueError, "No usable edges"):
                load_graph(folder, "bike")
            self.assertIsNotNone(load_graph(folder, "walk").search(1, 2)[0])

    def test_cache_reuse_and_invalidation(self):
        with tempfile.TemporaryDirectory() as tmp:
            folder = Path(tmp)
            write_export(folder)
            _cached_graph.cache_clear()
            first = get_router(folder, "bike")
            with patch("src.backend.route_model.astar_osm.load_graph", side_effect=AssertionError("reloaded")):
                self.assertIs(get_router(folder, "bike"), first)
            with (folder / "graph_version.csv").open("a") as stream:
                stream.write("\n")
            self.assertIsNot(get_router(folder, "bike"), first)
            _cached_graph.cache_clear()

    def test_missing_and_invalid_graph_have_structured_status(self):
        with tempfile.TemporaryDirectory() as tmp:
            args = ({"lon": 0, "lat": 0}, {"lon": .001, "lat": 0}, "bike")
            self.assertEqual(solve_baseline(*args, graph_dir=tmp)["status"], "data_unavailable")
            write_export(Path(tmp), corrupt=True)
            self.assertEqual(solve_baseline(*args, graph_dir=tmp)["status"], "invalid_graph")


if __name__ == "__main__":
    unittest.main()
