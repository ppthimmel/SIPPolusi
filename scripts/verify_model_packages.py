"""TI-AI-11: package the models and check that they reload with the same output. From the repo root:

    python scripts/verify_model_packages.py route --set docs/experiments/TI-AI-09/set \\
        --out docs/experiments/TI-AI-11/route_smoke_test.json
    python scripts/verify_model_packages.py spatial --bundle <bundle dir> --graph-version jakarta-20261005

`route` builds the package in a temporary directory, then loads the original set and the
package, each in its own fresh interpreter (-I), and compares the edge sequence of every
route. Exposure is a fixed synthetic field: the real fields are not in git, so this proves
that the package reloads and reproduces the set, not route quality (that is TI-AI-10).
`spatial` validates a packaged bundle against the Worker's contract and loads it.
No credentials are read or written.
"""
from __future__ import annotations

import argparse
import json
import platform
import subprocess
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "src/backend"))
sys.path.insert(0, str(ROOT / "src/data_worker"))


def synthetic_exposure(edge_ids):
    return {e: 1.0 + ((e * 2654435761) % 1000) / 1000.0 for e in edge_ids}


def route_worker(args):
    """Runs in a fresh interpreter: decode routes with one model directory and write them as JSON."""
    from src.backend.route_model.astar_osm import DEFAULT_GRAPH, get_router
    from src.backend.route_model.morl.infer import PolicyFailure, decode_for_preference, load_policy_set
    from src.backend.route_model.morl.packaging import load_packaged_policy_set, runtime_contract
    from src.backend.route_model.training_env import load_od_pairs

    directory = Path(args.model_dir)
    manifest = json.loads((directory / "set_manifest.json").read_text(encoding="utf-8"))
    config = manifest["config"]
    router = get_router(DEFAULT_GRAPH, config["mode"])
    if args.kind == "package":
        runtime = runtime_contract(router.version, args.contracts_version)
        policies, _, compatibility = load_packaged_policy_set(directory, runtime)
    else:
        policies, compatibility = load_policy_set(directory), None
    pairs = load_od_pairs(directory / "od_pairs.json", split="val", graph_version=router.version)[: args.pairs]
    exposure = synthetic_exposure(router.edges)
    routes = []
    for od in pairs:
        for alpha in config["policy_set"]["alphas"]:
            try:
                member, route = decode_for_preference(policies, router, exposure, od.start, od.end, (alpha, 1 - alpha),
                                                      max_steps=config["env"]["max_steps"],
                                                      max_steps_factor=config["env"]["max_steps_factor"])
                routes.append({"od": od.od_id, "alpha": alpha, "member": member, "edges": list(route.edge_ids)})
            except PolicyFailure as failure:
                routes.append({"od": od.od_id, "alpha": alpha, "failure": failure.reason})
    Path(args.out).write_text(json.dumps({"graph_version": router.version, "compatibility": compatibility and compatibility.as_dict(),
                                          "routes": routes}), encoding="utf-8")


def run_worker(kind, model_dir, out, pairs, contracts_version):
    command = [sys.executable, "-I", str(Path(__file__).resolve()), "route-worker", "--kind", kind, "--model-dir", str(model_dir),
               "--out", str(out), "--pairs", str(pairs), "--contracts-version", contracts_version]
    # -I ignores PYTHONPATH and the script's directory; the worker re-adds only the repo paths it needs.
    subprocess.run(command, check=True, cwd=ROOT)


def route(args):
    from src.backend.route_model.morl.packaging import package_policy_set
    from src.backend.public_api.data_contracts import CONTRACT_VERSION
    import torch

    with tempfile.TemporaryDirectory() as tmp:
        package_dir = Path(tmp) / "package"
        manifest = package_policy_set(args.set, package_dir, data_contracts_version=CONTRACT_VERSION)
        original, packaged = Path(tmp) / "original.json", Path(tmp) / "package.json"
        run_worker("set", args.set, original, args.pairs, CONTRACT_VERSION)
        run_worker("package", package_dir, packaged, args.pairs, CONTRACT_VERSION)
        first, second = json.loads(original.read_text()), json.loads(packaged.read_text())
        size = sum(p.stat().st_size for p in package_dir.rglob("*") if p.is_file())
    identical = first["routes"] == second["routes"]
    reached = sum(1 for r in second["routes"] if "edges" in r)
    result = {"result": "pass" if identical and reached else "fail", "routes_compared": len(second["routes"]),
              "routes_reached": reached, "identical_edge_sequences": identical,
              "failures": sorted({r["failure"] for r in second["routes"] if "failure" in r}),
              "graph_version": second["graph_version"], "compatibility": second["compatibility"],
              "package": {"model_id": manifest["model_id"], "model_version": manifest["model_version"],
                          "files": len(manifest["artifacts"]), "bytes": size},
              "environment": {"python": platform.python_version(), "torch": torch.__version__},
              "exposure": "fixed synthetic field; real fields are not in git"}
    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text(json.dumps(result, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(result, indent=2))
    return 0 if result["result"] == "pass" else 1


def spatial(args):
    from spatial_model.artifacts.packaging import runtime_contract, verify_package
    from spatial_model.inference.serving import load_model
    manifest = verify_package(args.bundle, args.target)
    target = manifest["lineage"]["target"]
    loaded = load_model(args.bundle, target, runtime_contract(args.graph_version))
    result = {"result": "pass", "target": target, "model_version": manifest["model_version"],
              "compatibility": loaded.compatibility.as_dict(), "files": len(manifest["artifacts"])}
    print(json.dumps(result, indent=2))
    return 0


def main():
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    sub = parser.add_subparsers(dest="command", required=True)
    p = sub.add_parser("route")
    p.add_argument("--set", type=Path, required=True)
    p.add_argument("--out", type=Path, required=True)
    p.add_argument("--pairs", type=int, default=20)
    p = sub.add_parser("route-worker")
    p.add_argument("--kind", choices=("set", "package"), required=True)
    p.add_argument("--model-dir", required=True)
    p.add_argument("--out", required=True)
    p.add_argument("--pairs", type=int, required=True)
    p.add_argument("--contracts-version", required=True)
    p = sub.add_parser("spatial")
    p.add_argument("--bundle", type=Path, required=True)
    p.add_argument("--target", choices=("pm25", "no2"))
    p.add_argument("--graph-version", required=True)
    args = parser.parse_args()
    if args.command == "route-worker":
        sys.path.insert(0, str(ROOT))
        sys.path.insert(0, str(ROOT / "src/backend"))
        return route_worker(args)
    return {"route": route, "spatial": spatial}[args.command](args)


if __name__ == "__main__":
    sys.exit(main())
