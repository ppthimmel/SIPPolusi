"""Check a trained policy set from its files alone. From the repo root:

    python -m src.backend.route_model.morl.verify_set --set docs/experiments/TI-AI-09/set \\
        --fields src/backend/route_model/runs/fields --out docs/experiments/TI-AI-09/verification.json

Each selected checkpoint must match the SHA-256 in the manifest. The set is loaded
twice and must give identical routes on the validation pairs. Also measures how long a
route takes on CPU, for comparison with the design's limit of 8 s per request.
"""
from __future__ import annotations

import argparse
import json
import statistics
import time
from pathlib import Path

from ..astar_osm import DEFAULT_GRAPH, get_router
from ..training_env import file_sha256, load_od_pairs
from .infer import PolicyFailure, decode_for_preference, load_policy_set
from .train import build_envs


def main():
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--set", required=True, type=Path, dest="set_dir")
    parser.add_argument("--fields", required=True, type=Path)
    parser.add_argument("--out", required=True, type=Path)
    parser.add_argument("--graph-dir", type=Path, default=DEFAULT_GRAPH)
    args = parser.parse_args()

    manifest = json.loads((args.set_dir / "set_manifest.json").read_text(encoding="utf-8"))
    config = manifest["config"]
    checkpoints = {}
    for member in manifest["members"]:
        path = args.set_dir / member["run_dir"] / member["best"]["checkpoint"]
        checkpoints[f"{member['alpha']:g}"] = file_sha256(path) == member["best"]["sha256"]

    router = get_router(args.graph_dir, config["mode"])
    val_od = load_od_pairs(args.set_dir / "od_pairs.json", split="val", graph_version=router.version)
    envs, _ = build_envs(router, args.fields, val_od, config["env"], "val", config["seed"])

    def routes(policies):
        result, seconds, failures = [], [], {}
        for i, od in enumerate(val_od):
            exposure = envs[i % len(envs)].exposure
            for alpha in config["policy_set"]["alphas"]:
                started = time.perf_counter()
                try:
                    result.append(decode_for_preference(policies, router, exposure, od.start, od.end, (alpha, 1 - alpha),
                                                        max_steps=config["env"]["max_steps"],
                                                        max_steps_factor=config["env"]["max_steps_factor"])[1].edge_ids)
                except PolicyFailure as failure:
                    result.append(failure.reason)
                    failures[failure.reason] = failures.get(failure.reason, 0) + 1
                seconds.append(time.perf_counter() - started)
        return result, seconds, failures

    first, seconds, failures = routes(load_policy_set(args.set_dir))
    second, _, _ = routes(load_policy_set(args.set_dir))
    ordered = sorted(seconds)
    report = {"set_id": manifest["set_id"], "code_commit": manifest["code"]["commit"],
              "checkpoints_match_manifest": checkpoints,
              "reload": {"routes": len(first), "identical": first == second,
                         "reached": sum(isinstance(r, tuple) for r in first), "failures": failures},
              "route_seconds_cpu": {"median": round(statistics.median(ordered), 3),
                                    "p95": round(ordered[int(len(ordered) * 0.95)], 3), "max": round(ordered[-1], 3)}}
    args.out.write_text(json.dumps(report, indent=2), encoding="utf-8")
    print(json.dumps(report, indent=2))


if __name__ == "__main__":
    main()
