"""Paired evaluation of the existing scalarized policy, policy set and regret prior.

The test split retains TI-AI-10's clean-commit safeguard. See MORL-COMPARISON-PLAN.md.
"""
from __future__ import annotations

import argparse
import datetime as dt
import hashlib
import json
import platform
import random
import time
from pathlib import Path

import torch

from ..astar_osm import DEFAULT_GRAPH, get_router
from ..training_env import GRAPH_FILES, file_sha256
from . import evaluate_candidate as evaluation
from .augment import Augmentation, PerturbedExposure, SmoothField
from .infer import PolicyFailure, decode_route, load_policy_set
from .resources import thread_limit
from .scalarized import load_scalarized_policy
from .scalarized_policy import BoundPreference, RegretPrior
from .train import build_envs, config_digest, git_state

METHODS = ("fixed_preference_set", "scalarized", "regret_prior")
DEFAULT_SCALARIZED = Path("docs/experiments/TI-AI-09/scalarized/20261103T081938Z-scalarized-bike-v1-6eab87d4")


def checked_checkpoint(path, expected):
    actual = file_sha256(path)
    if actual != expected:
        raise ValueError(f"Checkpoint hash mismatch: {path}")
    return actual


def load_candidates(set_dir, scalarized_dir):
    set_dir, scalarized_dir = Path(set_dir), Path(scalarized_dir)
    set_manifest = json.loads((set_dir / "set_manifest.json").read_text(encoding="utf-8"))
    scalar_manifest = json.loads((scalarized_dir / "manifest.json").read_text(encoding="utf-8"))
    hashes = {}
    for member in set_manifest["members"]:
        path = set_dir / member["run_dir"] / member["best"]["checkpoint"]
        hashes[str(member["alpha"])] = checked_checkpoint(path, member["best"]["sha256"])
    path = scalarized_dir / scalar_manifest["best"]["checkpoint"]
    scalar_hash = checked_checkpoint(path, scalar_manifest["best"]["sha256"])
    if (set_manifest["data"]["graph_version"], set_manifest["data"]["mode"]) != (
        scalar_manifest["data"]["graph_version"], scalar_manifest["data"]["mode"]
    ):
        raise ValueError("Candidates were trained for different graph versions or modes")
    scalar, _ = load_scalarized_policy(path)
    prior = RegretPrior(scalar.config).eval()
    return load_policy_set(set_dir), scalar, prior, set_manifest, scalar_manifest, hashes, scalar_hash


def decode_conditioned(model, alpha, router, exposure, od, env_config):
    started = time.perf_counter()
    try:
        route = decode_route(BoundPreference(model, alpha), router, exposure, od.start, od.end,
                             max_steps=env_config["max_steps"], max_steps_factor=env_config["max_steps_factor"])
        result = evaluation.measure(router, exposure, route.edge_ids, od.start, od.end)
    except PolicyFailure as failure:
        result = {"reached": False, "valid": None, "failure": failure.reason}
    result["inference_ms"] = round((time.perf_counter() - started) * 1000, 2)
    return result


def evaluate_scenario(policies, scalar, prior, router, exposure, od, window, env_config, alphas):
    # The original evaluator computes fastest, optimum and each selected set member once.
    records = evaluation.evaluate_scenario(policies, router, exposure, od, window, env_config, alphas)
    for record in records:
        alpha = record["alpha"]
        record["methods"] = {
            "fixed_preference_set": {"member": record.pop("member"), "route": record.pop("policy")},
            "scalarized": {"member": alpha, "route": decode_conditioned(scalar, alpha, router, exposure, od, env_config)},
            "regret_prior": {"member": alpha, "route": decode_conditioned(prior, alpha, router, exposure, od, env_config)},
        }
    return records


def method_records(records, method):
    return [{**{k: v for k, v in r.items() if k != "methods"},
             "member": r["methods"][method]["member"], "policy": r["methods"][method]["route"]}
            for r in records]


def run(config, fields_dir, out, *, split, scalarized_dir=DEFAULT_SCALARIZED, graph_dir=DEFAULT_GRAPH,
        router=None, strata=None, git=git_state):
    state = git()
    if split == "test" and (state.get("dirty") is not False or not state.get("commit")):
        raise evaluation.DirtyTree("Commit the protocol and code first: test requires a clean, identified commit")
    out = Path(out)
    if out.exists() and any(out.iterdir()):
        raise FileExistsError(f"Output directory is not empty: {out}")
    torch.set_num_threads(thread_limit(config.get("threads", 1)))
    policies, scalar, prior, sm, am, set_hashes, scalar_hash = load_candidates(config["set_dir"], scalarized_dir)
    set_config = sm["config"]
    router = router or get_router(graph_dir, set_config["mode"])
    if (router.version, router.mode) != (sm["data"]["graph_version"], sm["data"]["mode"]):
        raise ValueError("Runtime graph version or mode does not match the candidates")
    if strata is None:
        strata = evaluation.sample_scenarios(router, config, set_config["od"]["split_seed"], split)
    augmentation = Augmentation(**set_config["augmentation"]) if set_config.get("augmentation") else None
    primary = next(s["name"] for s in config["strata"] if s["primary"])
    criteria, env_config = config["criteria"], set_config["env"]
    out.mkdir(parents=True, exist_ok=True)
    summaries = {}
    started = dt.datetime.now(dt.timezone.utc)
    with evaluation.scenario_writer(out) as stream:
        for name, (pairs, _) in strata.items():
            envs, fields = build_envs(router, fields_dir, pairs, env_config, split, set_config["seed"])
            for kind in (("real", "perturbed") if augmentation else ("real",)):
                records = []
                for i, od in enumerate(pairs):
                    env = envs[i % len(envs)]
                    exposure = env.exposure
                    if kind == "perturbed":
                        field = SmoothField(augmentation, random.Random(config["perturbed_seed"] + i))
                        exposure = PerturbedExposure(exposure, router, field)
                    for record in evaluate_scenario(policies, scalar, prior, router, exposure, od,
                                                    env.weights.time_window, env_config, config["alphas"]):
                        record.update(stratum=name, fields=kind)
                        stream.write(json.dumps(record, allow_nan=False) + "\n")
                        records.append(record)
                summaries.setdefault(name, {})[kind] = {
                    method: evaluation.summarize(method_records(records, method), criteria["inference_budget_ms"])
                    for method in METHODS}
                print(f"{name}/{kind}: {len(records)} paired scenarios", flush=True)
    decisions = {method: evaluation.decide(summaries[primary]["real"][method], criteria) for method in METHODS}
    field_manifest = Path(fields_dir) / "manifest.json"
    field_data = json.loads(field_manifest.read_text(encoding="utf-8"))
    used_windows = field_data["window_splits"][split]
    manifest = {
        "evaluation": "paired-morl-comparison", "split": split, "code": state,
        "config": config, "config_sha256": config_digest(config), "started_at": started.isoformat(),
        "candidates": {"fixed_preference_set": {"set_id": sm["set_id"], "checkpoints_sha256": set_hashes},
                       "scalarized": {"run_id": am["run_id"], "checkpoint_sha256": scalar_hash,
                                      "selected_update": am["best"]["update"]},
                       "regret_prior": {"trained": False, "config_from": am["run_id"]}},
        "graph_version": router.version, "mode": router.mode,
        "graph_files_sha256": {name: file_sha256(Path(graph_dir) / name) for name in GRAPH_FILES},
        "fields_manifest_sha256": file_sha256(field_manifest),
        "field_files_sha256": {w: file_sha256(Path(fields_dir) / field_data["windows"][w]["file"]) for w in used_windows},
        "strata": {name: {"od_pairs": len(pairs), "od_sha256": hashlib.sha256(
            json.dumps([[p.od_id, p.start, p.end] for p in pairs]).encode()).hexdigest(), "sampling": stats}
                   for name, (pairs, stats) in strata.items()},
        "versions": {"python": platform.python_version(), "torch": torch.__version__, "threads": torch.get_num_threads()},
        "limitations": ["TI-AI-10 test scenarios have been inspected; repeat results are exploratory.",
                        "The candidates differ in both preference conditioning and the regret path.",
                        "Latency is measured locally without enforcing a timeout."],
    }
    evaluation.write_results(out, manifest=manifest, summary=summaries, decision=decisions)
    (out / "scenario_od_pairs.json").write_text(json.dumps(
        {name: [vars(p) for p in pairs] for name, (pairs, _) in strata.items()}, indent=2), encoding="utf-8")
    return {"manifest": manifest, "summary": summaries, "decision": decisions}


def main():
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--config", type=Path, required=True)
    parser.add_argument("--fields", type=Path, required=True)
    parser.add_argument("--out", type=Path, required=True)
    parser.add_argument("--scalarized-dir", type=Path, default=DEFAULT_SCALARIZED)
    parser.add_argument("--graph-dir", type=Path, default=DEFAULT_GRAPH)
    parser.add_argument("--split", choices=("val", "test"), default="test")
    args = parser.parse_args()
    config = json.loads(args.config.read_text(encoding="utf-8"))
    result = run(config, args.fields, args.out, split=args.split, scalarized_dir=args.scalarized_dir,
                 graph_dir=args.graph_dir)
    print(json.dumps(result["decision"], indent=2))


if __name__ == "__main__":
    main()
