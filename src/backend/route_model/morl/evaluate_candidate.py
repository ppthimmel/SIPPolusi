"""Evaluate the multi-policy set against the fastest route on unseen data (TI-AI-10).

    python -m src.backend.route_model.morl.evaluate_candidate \\
        --config src/backend/route_model/morl/configs/evaluation-ti-ai-10.json \\
        --fields src/backend/route_model/runs/fields --out docs/experiments/TI-AI-10/results

docs/experiments/TI-AI-10/PROTOCOL.md defines the scenarios, metrics and decision rule.
Each scenario holds an OD pair and a time window fixed, so every method sees the same
graph, exposure table and references. `--split test` refuses to run on a tree with
uncommitted changes, so the commit in the output proves the criteria came first.
"""
from __future__ import annotations

import argparse
import copy
import datetime as dt
import hashlib
import json
import math
import platform
import random
import statistics
import subprocess
import time
from pathlib import Path

import numpy as np
import torch

from ..astar_osm import DEFAULT_GRAPH, get_router
from ..training_env import ODPair, SplitSpec, file_sha256, load_od_pairs, sample_od_pairs
from .augment import Augmentation, PerturbedExposure, SmoothField
from .features import FEATURE_VERSION
from .infer import CHECKPOINT_FORMAT, PolicyFailure, decode_route, load_policy_set, nearest_alpha, optimal_weighted_route
from .resources import thread_limit
from .train import ROOT, build_envs, config_digest, git_state

BOOTSTRAP_SAMPLES = 1000


class DirtyTree(RuntimeError):
    """The test split may only be read from a commit that already holds the criteria."""


# ------------------------------------------------------------------ one route

def route_is_valid(router, edge_ids, start, end) -> bool:
    """Connected from start to end, using only arcs this mode may travel."""
    node = start
    for edge_id in edge_ids:
        arc = next((a for a in router.arcs.get(node, ()) if a[2] == edge_id), None)
        if arc is None:
            return False
        node = arc[0]
    return bool(edge_ids) and node == end


def measure(router, exposure, edge_ids, start, end) -> dict:
    edges = [router.edges[e] for e in edge_ids]
    return {"reached": True, "valid": route_is_valid(router, edge_ids, start, end),
            "time_s": math.fsum(e.time_s for e in edges),
            "exposure": math.fsum(exposure[e.edge_id] * e.time_s for e in edges),
            "length_m": math.fsum(e.length_m for e in edges), "edges": len(edges),
            "route_sha256": hashlib.sha256(json.dumps(list(edge_ids)).encode()).hexdigest()[:16]}


def percent_change(value, reference) -> float:
    return (value / reference - 1.0) * 100.0 if reference else 0.0


def evaluate_scenario(policies, router, exposure, od, window, env_config, alphas) -> list[dict]:
    """One record per alpha. Each member decodes once; alphas it serves share that route."""
    started = time.perf_counter()
    path, _, _ = router.search(od.start, od.end)
    fastest_ms = (time.perf_counter() - started) * 1000
    fastest_edges = [edge_id for edge_id, _ in path]
    fastest = {**measure(router, exposure, fastest_edges, od.start, od.end), "inference_ms": round(fastest_ms, 2)}

    members = {}
    for member, model in policies.items():
        started = time.perf_counter()
        try:
            route = decode_route(model, router, exposure, od.start, od.end, max_steps=env_config["max_steps"],
                                 max_steps_factor=env_config["max_steps_factor"])
            result = measure(router, exposure, route.edge_ids, od.start, od.end)
        except PolicyFailure as failure:
            result = {"reached": False, "valid": None, "failure": failure.reason}
        result["inference_ms"] = round((time.perf_counter() - started) * 1000, 2)
        members[member] = result

    records = []
    for alpha in alphas:
        member = nearest_alpha(policies, alpha)
        edges, _ = optimal_weighted_route(router, exposure, od.start, od.end, alpha, 1.0 - alpha,
                                          fastest["time_s"], fastest["exposure"])
        records.append({"od_id": od.od_id, "start": od.start, "end": od.end, "window": window, "alpha": alpha,
                        "member": member, "policy": members[member], "fastest": fastest,
                        "optimal": measure(router, exposure, edges, od.start, od.end)})
    return records


# ------------------------------------------------------------------ summaries

def spread(values, seed=0) -> dict | None:
    """Median, quartiles and a bootstrap 95% interval of the median: the spread, not one average."""
    if not values:
        return None
    rng = np.random.default_rng(seed)
    data = np.asarray(values, dtype=float)
    medians = np.median(rng.choice(data, size=(BOOTSTRAP_SAMPLES, len(data))), axis=1)
    return {"n": len(values), "median": round(float(np.median(data)), 4),
            "q1": round(float(np.quantile(data, 0.25)), 4), "q3": round(float(np.quantile(data, 0.75)), 4),
            "min": round(float(data.min()), 4), "max": round(float(data.max()), 4),
            "median_ci95": [round(float(np.quantile(medians, 0.025)), 4), round(float(np.quantile(medians, 0.975)), 4)]}


def changes(records, method, fallback=False) -> dict:
    """Paired % change against the fastest route.

    Without fallback, failed routes are left out (their count is in `success_rate`). With
    fallback, a failure counts as the fastest route itself, as the service would answer.
    """
    rows = {"time": [], "exposure": [], "length": []}
    for record in records:
        route, base = record[method], record["fastest"]
        if not route["reached"]:
            if fallback:
                for key in rows:
                    rows[key].append(0.0)
            continue
        rows["time"].append(percent_change(route["time_s"], base["time_s"]))
        rows["exposure"].append(percent_change(route["exposure"], base["exposure"]))
        rows["length"].append(percent_change(route["length_m"], base["length_m"]))
    return {key: spread(values) for key, values in rows.items()}


def direction_counts(records) -> dict:
    """Against the fastest route: how many policy routes are identical, better or worse on exposure and time."""
    reached = [r for r in records if r["policy"]["reached"]]
    same = [r for r in reached if r["policy"]["route_sha256"] == r["fastest"]["route_sha256"]]
    differ = [r for r in reached if r["policy"]["route_sha256"] != r["fastest"]["route_sha256"]]
    return {"same_as_fastest": len(same), "different": len(differ),
            "less_exposure": sum(r["policy"]["exposure"] < r["fastest"]["exposure"] for r in differ),
            "more_exposure": sum(r["policy"]["exposure"] > r["fastest"]["exposure"] for r in differ),
            "less_exposure_and_slower_or_equal": sum(
                r["policy"]["exposure"] < r["fastest"]["exposure"] and r["policy"]["time_s"] >= r["fastest"]["time_s"]
                for r in differ),
            "worse_on_both": sum(r["policy"]["exposure"] > r["fastest"]["exposure"]
                                 and r["policy"]["time_s"] > r["fastest"]["time_s"] for r in differ)}


def preference_fulfilment(records) -> dict:
    """EAI-05. An OD pair is fulfilled when moving from alpha 1 to alpha 0 does not raise exposure
    and does not shorten the time. Identical routes satisfy this without showing any
    response, so they are counted apart: `shifted` is what demonstrates one."""
    by_od = {}
    for record in records:
        by_od.setdefault(record["od_id"], {})[record["alpha"]] = record["policy"]
    usable = fulfilled = shifted = 0
    for routes in by_od.values():
        time_first, clean_first = routes.get(1.0), routes.get(0.0)
        if not (time_first and clean_first and time_first["reached"] and clean_first["reached"]):
            continue
        usable += 1
        direction = clean_first["exposure"] <= time_first["exposure"] and clean_first["time_s"] >= time_first["time_s"]
        moved = clean_first["route_sha256"] != time_first["route_sha256"]
        fulfilled += direction
        shifted += direction and moved
    return {"od_pairs": len(by_od), "usable": usable, "fulfilled": fulfilled, "shifted": shifted,
            "fulfilled_rate": fulfilled / usable if usable else None,
            "shifted_rate": shifted / usable if usable else None}


def summarize(records, budget_ms) -> dict:
    alphas = sorted({r["alpha"] for r in records})
    by_alpha = {}
    for alpha in alphas:
        rows = [r for r in records if r["alpha"] == alpha]
        reached = [r for r in rows if r["policy"]["reached"]]
        by_alpha[f"{alpha:g}"] = {
            "member": rows[0]["member"], "scenarios": len(rows), "reached": len(reached),
            "success_rate": len(reached) / len(rows),
            "policy": changes(rows, "policy"), "policy_with_fallback": changes(rows, "policy", fallback=True),
            "optimal": changes(rows, "optimal"), "against_fastest": direction_counts(rows)}
    # One timing per decoded route: alphas served by the same member share a decode.
    decodes = {(r["od_id"], r["window"], r["member"]): r["policy"] for r in records}
    searches = {(r["od_id"], r["window"]): r["fastest"]["inference_ms"] for r in records}
    routes = list(decodes.values())
    reached = [r for r in routes if r["reached"]]
    failures = {}
    for route in routes:
        if not route["reached"]:
            failures[route["failure"]] = failures.get(route["failure"], 0) + 1
    ms = sorted(r["inference_ms"] for r in routes)
    fastest_ms = sorted(searches.values())
    return {
        "scenarios": len(records), "od_pairs": len({r["od_id"] for r in records}),
        "windows": sorted({r["window"] for r in records}), "alphas": alphas,
        "decoded_routes": len(routes), "reached_routes": len(reached), "failures": failures,
        "success_rate": len(reached) / len(routes),
        "route_validity": sum(bool(r["valid"]) for r in reached) / len(reached) if reached else None,
        "inference_ms": {"median": statistics.median(ms), "p95": ms[int(len(ms) * 0.95)], "max": ms[-1],
                         "over_budget": sum(m > budget_ms for m in ms)},
        "fastest_search_ms": {"median": statistics.median(fastest_ms), "p95": fastest_ms[int(len(fastest_ms) * 0.95)],
                              "max": fastest_ms[-1]},
        "by_alpha": by_alpha, "preference": preference_fulfilment(records),
    }


def decide(summary, criteria) -> dict:
    """The rule of PROTOCOL.md, applied mechanically. `summary` is the primary stratum on real fields."""
    default = summary["by_alpha"][f"{criteria['default_alpha']:g}"]
    time_change, exposure_change = default["policy"]["time"], default["policy"]["exposure"]
    gates = {
        "route_validity": {"value": summary["route_validity"], "target": criteria["route_validity_min"],
                           "passed": summary["route_validity"] is not None
                           and summary["route_validity"] >= criteria["route_validity_min"]},
        "success_rate": {"value": summary["success_rate"], "target": criteria["success_rate_min"],
                         "passed": summary["success_rate"] >= criteria["success_rate_min"]},
        "inference_p95_ms": {"value": summary["inference_ms"]["p95"], "target": criteria["inference_p95_max_ms"],
                             "passed": summary["inference_ms"]["p95"] <= criteria["inference_p95_max_ms"]},
    }
    reduction = -exposure_change["median"] if exposure_change else None
    fulfilled = summary["preference"]["fulfilled_rate"]
    research = {
        "eai04": {"median_exposure_reduction_pct": reduction,
                  "target_reduction_pct": criteria["eai04_median_exposure_reduction_min_pct"],
                  "median_added_time_pct": time_change["median"] if time_change else None,
                  "target_added_time_pct": criteria["eai04_median_added_time_max_pct"],
                  "passed": reduction is not None and reduction >= criteria["eai04_median_exposure_reduction_min_pct"]
                  and time_change["median"] <= criteria["eai04_median_added_time_max_pct"]},
        "eai05": {"fulfilled_rate": fulfilled, "shifted_rate": summary["preference"]["shifted_rate"],
                  "target": criteria["eai05_fulfilled_min"],
                  "passed": fulfilled is not None and fulfilled >= criteria["eai05_fulfilled_min"]},
    }
    technical = all(g["passed"] for g in gates.values())
    targets = all(t["passed"] for t in research.values())
    outcome = "not_a_candidate" if not technical else "candidate" if targets else "conditional_candidate"
    return {"outcome": outcome, "gates": gates, "research_targets": research}


# ------------------------------------------------------------------ run

def sample_scenarios(router, config, split_seed, split):
    """Per stratum, OD pairs of `split` and a rotating time window, so each window is covered."""
    spec = SplitSpec(seed=split_seed)
    strata = {}
    for stratum in config["strata"]:
        pairs, stats = sample_od_pairs(router, spec, {split: stratum["pairs"]}, seed=stratum["seed"],
                                       min_distance_m=stratum["min_distance_m"], max_distance_m=stratum["max_distance_m"],
                                       max_attempts_factor=2000)
        strata[stratum["name"]] = ([ODPair(f"{stratum['name']}-{p.od_id}", p.start, p.end, p.split) for p in pairs], stats)
    return strata


def run(config, fields_dir, out, *, split, graph_dir=DEFAULT_GRAPH, router=None, strata=None, git=git_state) -> dict:
    state = git()
    if split == "test" and state["dirty"] is not False:
        raise DirtyTree("Commit the protocol and code first: the test split is read from a clean tree only")
    torch.set_num_threads(thread_limit(config.get("threads", 1)))
    started = dt.datetime.now(dt.timezone.utc)
    set_dir = Path(config["set_dir"])
    set_manifest = json.loads((set_dir / "set_manifest.json").read_text(encoding="utf-8"))
    set_config = set_manifest["config"]
    router = router or get_router(graph_dir, set_config["mode"])
    policies = load_policy_set(set_dir)
    env_config, criteria = set_config["env"], config["criteria"]
    if strata is None:
        strata = sample_scenarios(router, config, set_config["od"]["split_seed"], split)
    augmentation = Augmentation(**set_config["augmentation"]) if set_config.get("augmentation") else None

    out = Path(out)
    out.mkdir(parents=True, exist_ok=True)
    summaries, primary = {}, next(s["name"] for s in config["strata"] if s["primary"])
    wall = time.perf_counter()
    with (out / "scenarios.jsonl").open("w", encoding="utf-8") as stream:
        for name, (pairs, _) in strata.items():
            # One RouteEnv per window: the exposure table every method reads, missing edges at the median.
            envs, fields = build_envs(router, fields_dir, pairs, env_config, split, set_config["seed"])
            for kind in ("real", "perturbed") if augmentation else ("real",):
                records = []
                for i, od in enumerate(pairs):
                    env = envs[i % len(envs)]
                    exposure = env.exposure
                    if kind == "perturbed":
                        field = SmoothField(augmentation, random.Random(config["perturbed_seed"] + i))
                        exposure = PerturbedExposure(exposure, router, field)
                    for record in evaluate_scenario(policies, router, exposure, od, env.weights.time_window, env_config,
                                                    config["alphas"]):
                        record.update(stratum=name, fields=kind)
                        stream.write(json.dumps(record, allow_nan=False) + "\n")
                        records.append(record)
                summaries.setdefault(name, {})[kind] = summarize(records, criteria["inference_budget_ms"])
                print(f"{name}/{kind}: {summaries[name][kind]['scenarios']} scenarios", flush=True)
            del envs

    decision = decide(summaries[primary]["real"], criteria)
    manifest = {
        "evaluation": config["name"], "split": split, "code": state, "config_sha256": config_digest(config),
        "config": config, "started_at": started.isoformat(timespec="seconds"),
        "wall_s": round(time.perf_counter() - wall, 1),
        "candidate": {"set_id": set_manifest["set_id"], "approach": set_manifest["approach"],
                      "training_commit": set_manifest["code"]["commit"],
                      "checkpoints": {f"{m['alpha']:g}": m["best"]["sha256"] for m in set_manifest["members"]}},
        "graph_version": router.version, "mode": router.mode,
        "fields_manifest_sha256": file_sha256(Path(fields_dir) / "manifest.json"),
        "strata": {name: {"od_pairs": len(pairs), "od_sha256": hashlib.sha256(
            json.dumps([[p.od_id, p.start, p.end] for p in pairs]).encode()).hexdigest(), "sampling": stats}
            for name, (pairs, stats) in strata.items()},
        "versions": {"python": platform.python_version(), "torch": torch.__version__, "threads": torch.get_num_threads()},
    }
    for name, payload in (("manifest", manifest), ("summary", summaries), ("decision", decision)):
        (out / f"{name}.json").write_text(json.dumps(payload, indent=2, allow_nan=False), encoding="utf-8")
    (out / "scenario_od_pairs.json").write_text(json.dumps(
        {name: [vars(p) for p in pairs] for name, (pairs, _) in strata.items()}, indent=2), encoding="utf-8")
    write_candidate(out, set_manifest, set_dir, decision, config, state)
    return {"manifest": manifest, "summary": summaries, "decision": decision}


def write_candidate(out, set_manifest, set_dir, decision, config, state):
    """What TI-SE-07 and TI-AI-12 need to take this set over, without reading the report."""
    members = [{"alpha": m["alpha"], "checkpoint": str(Path(config["set_dir"]) / m["run_dir"] / m["best"]["checkpoint"]),
                "sha256": m["best"]["sha256"], "selected_update": m["best"]["update"]} for m in set_manifest["members"]]
    candidate = {
        "model_name": "morl-route-policy", "approach": set_manifest["approach"], "decision": decision["outcome"],
        "set_id": set_manifest["set_id"], "set_dir": config["set_dir"], "training_commit": set_manifest["code"]["commit"],
        "evaluation_commit": state["commit"], "checkpoint_format": CHECKPOINT_FORMAT, "feature_version": FEATURE_VERSION,
        "graph_version": set_manifest["data"]["graph_version"], "mode": set_manifest["data"]["mode"], "members": members,
        "selection_rule": set_manifest["selection_rule"],
        "load": "route_model.morl.infer.load_policy_set(set_dir)",
        "infer": "route_model.morl.infer.decode_for_preference(policies, router, exposure, start, end, preference)",
        "reference_config": "src/backend/route_model/morl/configs/multi-policy-bike-v1.json",
        "env_limits": set_manifest["config"]["env"],
        "requirements": ["torch (CPU is enough for inference)", "numpy"],
        "constraints_for_integration": [
            "Requests must validate alpha and beta: decode_for_preference reads only preference[0].",
            "timeout_ms is checked inside the step loop only, after the A* reference and two reverse searches.",
            "Missing edge exposure must be filled with the median of covered edges, as RouteEnv does.",
            "A PolicyFailure must fall back to the A* route, marked as baseline.",
            "No Model Registry entry yet (TI-AI-11).",
        ],
    }
    (out / "candidate.json").write_text(json.dumps(candidate, indent=2), encoding="utf-8")


def main():
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--config", required=True, type=Path)
    parser.add_argument("--fields", required=True, type=Path)
    parser.add_argument("--out", required=True, type=Path)
    parser.add_argument("--graph-dir", type=Path, default=DEFAULT_GRAPH)
    parser.add_argument("--split", choices=("val", "test"), default="test",
                        help="val only checks the pipeline, on pairs sampled from the validation blocks")
    args = parser.parse_args()
    config = json.loads(args.config.read_text(encoding="utf-8"))
    result = run(config, args.fields, args.out, split=args.split, graph_dir=args.graph_dir)
    print(json.dumps(result["decision"], indent=2))


if __name__ == "__main__":
    main()
