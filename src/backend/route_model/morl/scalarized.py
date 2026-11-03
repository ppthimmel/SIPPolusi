"""Train, verify and export the scalarized MORL policy (PIC A of TI-AI-09). From the repo root:

    python -m src.backend.route_model.morl.scalarized train \\
        --config src/backend/route_model/morl/configs/scalarized-bike-v1.json \\
        --fields src/backend/route_model/runs/fields --out src/backend/route_model/runs/scalarized
    python -m src.backend.route_model.morl.scalarized verify --run <run dir> \\
        --fields src/backend/route_model/runs/fields
    python -m src.backend.route_model.morl.scalarized export --run <run dir>

One preference-conditioned policy maximises the environment's scalar reward
r = alpha * r_time + beta * r_exposure. Each training episode draws its own
alpha ~ U[0, 1] (beta = 1 - alpha), and the policy sees that (alpha, beta). The rest of
train.py is shared with the multi-policy set: warm start, PPO, run record and resume.

The checkpoint is selected by the mean return of the greedy policy over the preference
grid `preference.selection_alphas`, on the real validation fields. Test OD pairs are
never sampled and test windows never opened.
"""
from __future__ import annotations

import argparse
import json
import shutil
import statistics
import time
from pathlib import Path

import numpy as np
import torch

from ..astar_osm import DEFAULT_GRAPH, get_router
from ..training_env import file_sha256, load_od_pairs
from .features import FEATURE_VERSION
from .infer import CHECKPOINT_FORMAT, PolicyFailure, PolicyRoute, decode_route
from .scalarized_policy import BoundPreference, PreferenceActorCritic, RegretPrior, ScalarizedConfig
from .scalarized_report import plot_curves, write_summary
from .train import Actor, build_envs, evaluate, load_config, train, training_augmentation

ROOT = Path(__file__).resolve().parents[4]
ALGORITHM = "ppo-scalarized-1"
EVIDENCE_DIR = ROOT / "docs/experiments/TI-AI-09/scalarized"
# What a run contributes to the repository; periodic checkpoints and last.pt stay out of git.
EVIDENCE_FILES = ("config.json", "manifest.json", "od_pairs.json", "metrics.jsonl", "curves.csv", "evaluation.json",
                  "verification.json", "training_curves.png", "summary.md", "checkpoints/best.pt")


class PreferenceActor(Actor):
    """An episode stream whose episodes each draw a preference; samples end with (alpha, beta)."""

    def reset(self, env=None, field=None, **kwargs):
        if env is None:  # A new training episode; evaluation passes its env and has set alpha itself.
            self.alpha = self.rng.random()
        super().reset(env, field, **kwargs)

    def sample(self):
        sample = super().sample()
        return sample._replace(global_x=np.append(sample.global_x, (self.alpha, 1.0 - self.alpha)).astype(np.float32))


def load_scalarized_policy(path, device="cpu"):
    """Rebuild the network from a checkpoint alone; returns (model in eval mode, checkpoint)."""
    checkpoint = torch.load(path, map_location=device, weights_only=True)
    if checkpoint.get("format") != CHECKPOINT_FORMAT:
        raise ValueError(f"Unknown checkpoint format: {checkpoint.get('format')!r}")
    if checkpoint.get("feature_version") != FEATURE_VERSION:
        raise ValueError(f"Checkpoint expects features {checkpoint.get('feature_version')!r}, "
                         f"this code builds {FEATURE_VERSION!r}")
    model = PreferenceActorCritic(ScalarizedConfig(**checkpoint["model_config"])).to(device)
    model.load_state_dict(checkpoint["model_state"])
    return model.eval(), checkpoint


class Scalarized:
    """train.py's hooks for one preference-conditioned policy."""
    actor = PreferenceActor
    load_policy = staticmethod(load_scalarized_policy)

    @staticmethod
    def build_model(config):
        return PreferenceActorCritic(ScalarizedConfig(**config["model"]))

    @staticmethod
    def selection_alphas(config):
        return config["preference"]["selection_alphas"]

    @staticmethod
    def report_alphas(config):
        return config["preference"]["report_alphas"]

    @staticmethod
    def for_preference(model, alpha):
        return BoundPreference(model, alpha)


def decode_preference_route(model, router, exposure, start, end, preference, **kwargs) -> PolicyRoute:
    """The greedy route for a requested (alpha, beta); raises PolicyFailure, whose fallback is the A* route."""
    alpha, beta = preference
    if abs(alpha + beta - 1) > 1e-6:
        raise ValueError("alpha and beta must sum to 1")
    return decode_route(BoundPreference(model, alpha), router, exposure, start, end, **kwargs)


def train_scalarized(config, fields_dir, out=None, **kwargs):
    if config.get("algorithm") != ALGORITHM or "alpha" in config:
        raise ValueError(f"A scalarized config has algorithm {ALGORITHM!r} and no fixed alpha")
    return train(config, fields_dir, out, approach=Scalarized, **kwargs)


def verify(run_dir, fields_dir, graph_dir=DEFAULT_GRAPH):
    """verification.json, from the run's files alone: checksum, identical routes after reload, inference time,
    and the regret prior without any learning, on the validation fields, as the baseline of the policy."""
    run_dir = Path(run_dir)
    manifest = json.loads((run_dir / "manifest.json").read_text(encoding="utf-8"))
    config = manifest["config"]
    path = run_dir / manifest["best"]["checkpoint"]
    router = get_router(graph_dir, config["mode"])
    val_od = load_od_pairs(run_dir / "od_pairs.json", split="val", graph_version=router.version)
    envs, _ = build_envs(router, fields_dir, val_od, config["env"], "val", config["seed"])

    def routes():
        model, _ = load_scalarized_policy(path)
        result, seconds, failures = [], [], {}
        for i, od in enumerate(val_od):
            exposure = envs[i % len(envs)].exposure
            for alpha in config["preference"]["selection_alphas"]:
                started = time.perf_counter()
                try:
                    result.append(decode_preference_route(
                        model, router, exposure, od.start, od.end, (alpha, 1.0 - alpha),
                        max_steps=config["env"]["max_steps"], max_steps_factor=config["env"]["max_steps_factor"]
                    ).edge_ids)
                except PolicyFailure as failure:
                    result.append(failure.reason)
                    failures[failure.reason] = failures.get(failure.reason, 0) + 1
                seconds.append(time.perf_counter() - started)
        return result, sorted(seconds), failures

    first, seconds, failures = routes()
    second, _, _ = routes()
    model, _ = load_scalarized_policy(path)
    prior_alphas = [a for a in config["preference"]["report_alphas"] if a in config["preference"]["selection_alphas"]]

    def prior(augmentation=None):
        return evaluate(RegretPrior(model.config), envs, val_od, prior_alphas, model.config, "cpu",
                        config["schedule"]["eval_batch"], augmentation=augmentation, actor_type=PreferenceActor)[0]

    report = {"run_id": manifest["run_id"], "code_commit": manifest["code"]["commit"], "status": manifest["status"],
              "selected_update": manifest["best"]["update"],
              "checkpoint_matches_manifest": file_sha256(path) == manifest["best"]["sha256"],
              "checkpoint_selection": manifest["checkpoint_selection"],
              "reload": {"routes": len(first), "identical": first == second,
                         "reached": sum(isinstance(r, tuple) for r in first), "failures": failures},
              "route_seconds_cpu": {"median": round(statistics.median(seconds), 3),
                                    "p95": round(seconds[int(len(seconds) * 0.95)], 3), "max": round(seconds[-1], 3)},
              "regret_prior": {"note": "Greedy on the scalarized regret alone, no learned part.", "real_fields": prior()}}
    augmentation = training_augmentation(config)
    if augmentation:
        report["regret_prior"]["perturbed_fields"] = prior(augmentation)
    (run_dir / "verification.json").write_text(json.dumps(report, indent=2), encoding="utf-8")
    return report


def export_evidence(run_dir, to=EVIDENCE_DIR):
    """Write the run's summary, then copy its evidence to `to`/<run id>; returns the folder."""
    run_dir = Path(run_dir)
    try:
        plot_curves(run_dir)
    except ImportError:
        print("matplotlib is not installed: no training_curves.png")
    write_summary(run_dir)
    target = Path(to) / run_dir.name
    copied = []
    for name in EVIDENCE_FILES:
        if (run_dir / name).exists():
            (target / name).parent.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(run_dir / name, target / name)
            copied.append(name)
    print(f"{target}: {copied}")
    return target


def main():
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    commands = parser.add_subparsers(dest="command", required=True)

    command = commands.add_parser("train", help="start or resume a training run")
    command.add_argument("--config", required=True, type=Path)
    command.add_argument("--fields", required=True, type=Path)
    command.add_argument("--out", type=Path, help="parent directory of a new run")
    command.add_argument("--resume", type=Path, help="run directory to continue")
    command.add_argument("--graph-dir", type=Path, default=DEFAULT_GRAPH)
    command.add_argument("--device", default="auto", help="auto (cuda if available, else cpu), cpu, cuda, ...")
    command.add_argument("--max-hours", type=float, help="stop cleanly after this long; a recorded stop, not a failure")

    command = commands.add_parser("verify", help="checksum and reload check of a finished run")
    command.add_argument("--run", required=True, type=Path)
    command.add_argument("--fields", required=True, type=Path)
    command.add_argument("--graph-dir", type=Path, default=DEFAULT_GRAPH)

    command = commands.add_parser("export", help="copy a run's evidence to docs/experiments/TI-AI-09/scalarized")
    command.add_argument("--run", required=True, type=Path)
    command.add_argument("--to", type=Path, default=EVIDENCE_DIR)

    args = parser.parse_args()
    if args.command == "train":
        if bool(args.out) == bool(args.resume):
            parser.error("give exactly one of --out and --resume")
        device = args.device if args.device != "auto" else ("cuda" if torch.cuda.is_available() else "cpu")
        run = train_scalarized(load_config(args.config), args.fields, args.out, resume=args.resume,
                               graph_dir=args.graph_dir, device=device,
                               max_seconds=args.max_hours * 3600 if args.max_hours else None)
        print(json.dumps({k: run.manifest.get(k) for k in ("run_id", "status", "stop_reason", "best", "evaluation")},
                         indent=2))
        print(run.dir)
    elif args.command == "verify":
        print(json.dumps(verify(args.run, args.fields, args.graph_dir), indent=2))
    else:
        export_evidence(args.run, args.to)


if __name__ == "__main__":
    main()
