"""Run from repo root: python -m src.backend.route_model.verify_jakarta.

Optional: --output report.json. Uses the same explicit OD for both modes.
"""
import argparse
import hashlib
import json
from pathlib import Path

from .astar_osm import get_router, DEFAULT_GRAPH, solve_baseline


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--graph-dir", type=Path, default=DEFAULT_GRAPH)
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()
    origin = {"lon": 106.8502, "lat": -6.2099}
    destination = {"lon": 106.8272, "lat": -6.1754}
    report = {"origin": origin, "destination": destination, "max_snap_m": 300, "runs": []}
    for mode in ("bike", "walk"):
        previous = None
        for attempt in range(2):
            result = solve_baseline(origin, destination, mode, graph_dir=args.graph_dir)
            assert result["status"] == "ok", result
            geo = json.dumps(result.pop("route"), sort_keys=True).encode()
            if previous is not None:
                assert geo == previous, "GeoJSON changed between identical requests"
            previous = geo
            result.update(attempt="cold" if attempt == 0 else "warm", geojson_sha256=hashlib.sha256(geo).hexdigest())
            report["runs"].append(result)
        router = get_router(args.graph_dir, mode)
        # Compare the previous independent nearest-node snapping against the new pair.
        nearest_end = router.candidates((destination["lon"], destination["lat"]), router.component, 300)[0][1]
        old_path, _, _ = router.search(result["snap"]["start_node"], nearest_end)
        report[mode + "_nearest_destination"] = {"node": nearest_end, "reachable": old_path is not None}
    output = json.dumps(report, indent=2, allow_nan=False)
    if args.output:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(output, encoding="utf-8")
    print(output)


if __name__ == "__main__":
    main()
