"""Compare the optimized mock solver with the prior per-edge validation path.

Run with PYTHONPATH=src/backend from the repository root. This is a benchmark,
not a timing-sensitive test; runtimes depend on the machine and load.
"""
from __future__ import annotations

import argparse
import statistics
import time

from route_model.costs import edge_exposure, route_totals, weighted_cost
from solver.cancel import CancelToken
from solver.mock import _routes
from solver.paths import shortest_path
from solver.tests.biggrid import grid_input


def previous_routes(si):
    """Return paths using the previous per-relaxation validation strategy."""
    token = CancelToken()
    fastest = shortest_path(si, lambda edge: edge.travel_time_s, token)
    by_id = {edge.edge_id: edge for edge in si.edges}
    t_ref, e_ref = route_totals((by_id[i].travel_time_s, by_id[i].exposure_index) for i in fastest)
    alpha, beta = si.preference.alpha, si.preference.beta
    path = shortest_path(si, lambda edge: weighted_cost(
        edge.travel_time_s, edge_exposure(edge.travel_time_s, edge.exposure_index),
        t_ref, e_ref, alpha, beta), token)
    return fastest, path


def legacy_routes(si):
    """Return paths using the pre-PR weighted formula without edge validation."""
    token = CancelToken()
    fastest = shortest_path(si, lambda edge: edge.travel_time_s, token)
    by_id = {edge.edge_id: edge for edge in si.edges}
    t_ref, e_ref = route_totals((by_id[i].travel_time_s, by_id[i].exposure_index) for i in fastest)
    alpha, beta = si.preference.alpha, si.preference.beta
    path = shortest_path(si, lambda edge: alpha * edge.travel_time_s / (t_ref or 1.0)
                         + beta * edge.exposure_index * edge.travel_time_s / (e_ref or 1.0), token)
    return fastest, path


def benchmark(size, repeats):
    """Measure both complete solver paths on the same grid and compare routes."""
    si = grid_input(size, reachable=True)
    samples = {"legacy": [], "previous": [], "optimized": []}
    expected = None
    # Alternate order across iterations to reduce a systematic ordering bias.
    for i in range(repeats):
        names = ("legacy", "previous", "optimized")
        for name in names[i % len(names):] + names[:i % len(names)]:
            started = time.perf_counter()
            if name == "legacy":
                paths = legacy_routes(si)
            elif name == "previous":
                paths = previous_routes(si)
            else:
                reply = _routes(CancelToken(), si)
                paths = (reply.comparison, reply.recommended)
            samples[name].append(time.perf_counter() - started)
            if expected is None:
                expected = paths
            elif paths != expected:
                raise AssertionError("Benchmark paths differ")
    return {"grid": size, "nodes": len(si.nodes), "edges": len(si.edges), "repeats": repeats,
            "legacy_s": statistics.median(samples["legacy"]),
            "previous_s": statistics.median(samples["previous"]),
            "optimized_s": statistics.median(samples["optimized"]), "same_paths": True}


def main():
    """Parse benchmark parameters and print median elapsed seconds."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--size", type=int, default=250)
    parser.add_argument("--repeats", type=int, default=3)
    args = parser.parse_args()
    if args.size < 2 or args.repeats < 1:
        parser.error("size must be at least 2 and repeats must be positive")
    print(benchmark(args.size, args.repeats))


if __name__ == "__main__":
    main()
