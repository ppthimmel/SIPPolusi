"""Run from repo root: python -m src.backend.route_model.train_env_smoke.

Smoke test of the TI-AI-08 environment on the versioned OSM export. Samples
OD pairs per split, then runs train-split episodes with two scripted policies:
`reference` follows the fastest route (its return must be exactly -1) and
`random` picks uniformly among legal actions (exercises truncation/dead ends).
One illegal action is injected per random episode to show it is logged.

Without --weights the edge exposure is a deterministic synthetic field, as the
OSM export carries no pollution snapshot; 2% of edges are left missing to
exercise median imputation. Pass an EdgeWeight CSV (edge_id, exposure_index)
from `spatial_model infer` together with --time-window for a real snapshot.
"""
import argparse
import json
import math
import random
from pathlib import Path

from .astar_osm import DEFAULT_GRAPH, get_router
from .route_metrics import ExposureSnapshot
from .training_env import (
    EnvConfig, EpisodeLogger, RouteEnv, SplitSpec, load_weights_csv, sample_od_pairs, save_od_pairs,
)

DEFAULT_OUTPUT = Path(__file__).resolve().parents[3] / "docs/experiments/TI-AI-08"


def synthetic_weights(router):
    values = {}
    for edge_id, edge in router.edges.items():
        if edge_id % 50 == 0:
            continue  # deliberately missing
        (x1, y1), (x2, y2) = router.coords[edge.u], router.coords[edge.v]
        x, y = (x1 + x2) / 2, (y1 + y2) / 2
        values[edge_id] = round(1.5 + 0.5 * math.sin(x * 300) * math.cos(y * 300), 6)
    return ExposureSnapshot(router.version, "synthetic", "synthetic_exposure_index", values)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--graph-dir", type=Path, default=DEFAULT_GRAPH)
    parser.add_argument("--mode", default="bike")
    parser.add_argument("--weights", type=Path)
    parser.add_argument("--time-window")
    parser.add_argument("--seed", type=int, default=42)
    parser.add_argument("--episodes", type=int, default=10, help="per policy")
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT)
    args = parser.parse_args()
    if args.weights and not args.time_window:
        parser.error("--weights requires --time-window")

    router = get_router(args.graph_dir, args.mode)
    weights = (load_weights_csv(args.weights, router.version, args.time_window)
               if args.weights else synthetic_weights(router))
    spec = SplitSpec(block_deg=0.02, seed=args.seed)
    pairs, stats = sample_od_pairs(router, spec, {"train": 40, "val": 10, "test": 10}, seed=args.seed,
                                   min_distance_m=500, max_distance_m=3000)
    args.output.mkdir(parents=True, exist_ok=True)
    save_od_pairs(args.output / "od_pairs.json", pairs, spec, stats, router.version)

    train = [p for p in pairs if p.split == "train"]
    config = EnvConfig(split="train", seed=args.seed, max_steps=400)
    logger = EpisodeLogger(args.output / "episodes.jsonl")
    env = RouteEnv(router, weights, train, config, logger=logger)
    rng = random.Random(args.seed)
    for policy in ("reference", "random"):
        for _ in range(args.episodes):
            obs, info = env.reset()
            reference = iter(info["reference_edge_ids"])
            injected = policy == "reference"
            while True:
                legal = [i for i, ok in enumerate(obs["action_mask"]) if ok]
                if policy == "reference":
                    edge_id = next(reference)
                    action = next(i for i in legal if obs["actions"][i]["edge_id"] == edge_id)
                elif not injected:
                    action, injected = len(obs["actions"]), True  # out of range: illegal
                else:
                    action = rng.choice(legal)
                obs, _, terminated, truncated, info = env.step(action)
                if terminated or truncated:
                    info["episode"]["policy"] = policy
                    break

    by_policy = {}
    for policy in ("reference", "random"):
        subset = EpisodeLogger()
        subset.records = [r for r in logger.records if r.get("policy") == policy]
        by_policy[policy] = subset.summary()
    # Rewrite the log so each line carries its policy label.
    with (args.output / "episodes.jsonl").open("w", encoding="utf-8") as stream:
        for record in logger.records:
            stream.write(json.dumps(record, allow_nan=False) + "\n")
    manifest = env.manifest(args.graph_dir) | {"smoke": {"seed": args.seed, "episodes_per_policy": args.episodes,
                                                         "synthetic_weights": args.weights is None}}
    (args.output / "manifest.json").write_text(json.dumps(manifest, indent=2), encoding="utf-8")
    summary = {"all": logger.summary(), "by_policy": by_policy}
    (args.output / "summary.json").write_text(json.dumps(summary, indent=2), encoding="utf-8")
    print(json.dumps(summary, indent=2))
    reference = by_policy["reference"]
    assert reference["success_rate"] == 1 and abs(reference["mean_return"] + 1) < 1e-9, reference


if __name__ == "__main__":
    main()
