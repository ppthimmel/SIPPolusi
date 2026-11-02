"""Train and evaluate the multi-policy MORL set (PIC B of TI-AI-09). From the repo root:

    python -m src.backend.route_model.morl.multi_policy \\
        --config src/backend/route_model/morl/configs/multi-policy-bike-v1.json \\
        --fields src/backend/route_model/runs/fields --out src/backend/route_model/runs

One independent policy per alpha of `policy_set.alphas`, trained one after another so
that memory use stays that of a single training. All members share the OD pairs and
the time windows, and differ only in the preference their reward is weighted by (and
in their seed). At inference a request is served by the member with the nearest alpha
(infer.nearest_alpha), so a preference between grid points gets the closest policy,
not an interpolation.

The set directory holds set_manifest.json, od_pairs.json, evaluation.json and
members/<run>/, each member a complete run of train.py. An interrupted or stopped set
continues with --resume <set dir>: finished members are kept, and an unfinished one
continues from its last.pt.
"""
from __future__ import annotations

import argparse
import copy
import datetime as dt
import json
import signal
import time
import traceback
from pathlib import Path

import torch

from ..astar_osm import DEFAULT_GRAPH, get_router
from ..training_env import file_sha256, load_od_pairs
from .features import FEATURE_VERSION
from .infer import PolicyFailure, decode_for_preference, load_policy_set, nearest_alpha
from .resources import thread_limit
from .train import (
    ALGORITHM, _raise_terminated, _Terminated, build_envs, config_digest, evaluate, git_state, load_config,
    sample_od, summarize_evaluation, train, training_augmentation,
)

APPROACH = "multi-policy"
FINISHED = ("completed", "early_stopped")


def member_config(config, alpha, index) -> dict:
    member = copy.deepcopy(config)
    del member["policy_set"]
    member.update(name=f"{config['name']}-a{alpha:g}", alpha=alpha, seed=config["seed"] + index)
    return member


def _now():
    return dt.datetime.now(dt.timezone.utc).isoformat(timespec="seconds")


class PolicySetRun:
    def __init__(self, config, out=None, resume=None, device="cpu"):
        if resume:
            self.dir = Path(resume)
            self.manifest = json.loads((self.dir / "set_manifest.json").read_text(encoding="utf-8"))
            if self.manifest["config_sha256"] != config_digest(config):
                raise ValueError("--config differs from the policy set being resumed")
            self.manifest.setdefault("resumes", []).append(
                {"at": _now(), "previous_status": self.manifest["status"],
                 "previous_stop_reason": self.manifest["stop_reason"], "code": git_state()})
            self.manifest.update(status="running", stop_reason=None, finished_at=None)
            self.manifest.pop("traceback", None)
        else:
            alphas = config["policy_set"]["alphas"]
            if not alphas or len(set(alphas)) != len(alphas) or any(not 0 <= a <= 1 for a in alphas):
                raise ValueError("policy_set.alphas must be distinct values in [0, 1]")
            started = _now()
            digest = config_digest(config)
            set_id = f"{started[:19].replace('-', '').replace(':', '')}Z-{config['name']}-{digest[:8]}"
            self.dir = Path(out) / set_id
            attempt = 1
            while self.dir.exists():  # same second, same configuration: never mix two sets' files
                attempt += 1
                self.dir = Path(out) / f"{set_id}-{attempt}"
            (self.dir / "members").mkdir(parents=True)
            self.manifest = {
                "set_id": self.dir.name, "experiment": "morl-route-policy", "approach": APPROACH,
                "status": "running", "stop_reason": None, "started_at": started, "finished_at": None,
                "algorithm": ALGORITHM, "feature_version": FEATURE_VERSION, "device": str(device),
                "selection_rule": "nearest alpha; on a tie the lower alpha",
                "config_sha256": digest, "config": config, "code": git_state(),
                "members": [{"alpha": a, "status": "pending"} for a in alphas],
            }
        self.save()

    def save(self):
        path = self.dir / "set_manifest.json"
        temporary = path.with_suffix(".tmp")
        temporary.write_text(json.dumps(self.manifest, indent=2), encoding="utf-8")
        temporary.replace(path)

    def finish(self, status, reason):
        self.manifest.update(status=status, stop_reason=reason, finished_at=_now())
        self.save()

    def record_member(self, member, run_dir):
        """Copy a member's outcome from its own manifest, the single source of truth."""
        manifest = json.loads((Path(run_dir) / "manifest.json").read_text(encoding="utf-8"))
        member.update(run_dir=str(Path(run_dir).relative_to(self.dir)), run_id=manifest["run_id"],
                      status=manifest["status"], stop_reason=manifest["stop_reason"], seed=manifest["seed"],
                      progress=manifest["progress"], best=manifest["best"], evaluation=manifest.get("evaluation"))
        self.save()
        return member


def _train_member(run, member, config, index, fields_dir, od_pairs, **kwargs):
    alpha = member["alpha"]
    settings = member_config(config, alpha, index)
    resume = run.dir / member["run_dir"] if member.get("run_dir") else None
    if resume and not (resume / "checkpoints" / "last.pt").exists():
        resume = None  # it stopped before its first evaluation: nothing to continue from
    # A member that raises has still written its manifest, but no result reaches this frame.
    before = set((run.dir / "members").iterdir())
    try:
        result = train(settings, fields_dir, run.dir / "members", resume=resume, od_pairs=None if resume else od_pairs,
                       **kwargs)
    except Exception:
        created = set((run.dir / "members").iterdir()) - before
        target = resume or (created.pop() if created else None)
        if target:
            run.record_member(member, target)
        raise
    return run.record_member(member, result.dir)


def evaluate_set(run, config, router, fields_dir, val_od, device):
    """evaluation.json: the whole set, reloaded from disk, across the evaluation alphas on validation.

    Each alpha goes to the member a real request would reach, including alphas between
    members. Real fields decide nothing here either: this is the report for TI-AI-10.
    """
    policies = load_policy_set(run.dir, device)
    val_envs, _ = build_envs(router, fields_dir, val_od, config["env"], "val", config["seed"])
    alphas = config["policy_set"]["evaluation_alphas"]
    batch = config["schedule"]["eval_batch"]
    model_config = next(iter(policies.values())).config
    assigned = {member: [a for a in alphas if nearest_alpha(policies, a) == member] for member in policies}

    def report(augmentation):
        records = []
        for member, served in assigned.items():
            if served:
                records += evaluate(policies[member], val_envs, val_od, served, model_config, device, batch,
                                    oracle=True, augmentation=augmentation)[1]
        return summarize_evaluation(records)

    def routes(policy_set):
        result = []
        for i, od in enumerate(val_od):
            exposure = val_envs[i % len(val_envs)].exposure
            for alpha in config["policy_set"]["alphas"]:
                try:
                    result.append(decode_for_preference(policy_set, router, exposure, od.start, od.end,
                                                        (alpha, 1.0 - alpha), max_steps=config["env"]["max_steps"],
                                                        max_steps_factor=config["env"]["max_steps_factor"],
                                                        device=device)[1].edge_ids)
                except PolicyFailure as failure:
                    result.append(failure.reason)
        return result

    first, second = routes(policies), routes(load_policy_set(run.dir, device))
    evaluation = {"split": "val", "selection_rule": run.manifest["selection_rule"],
                  "served_by": {f"{m:g}": served for m, served in assigned.items()},
                  "summary": report(None),
                  "reload_check": {"routes": len(first), "identical": first == second,
                                   "routes_reached": sum(isinstance(r, tuple) for r in first)}}
    augmentation = training_augmentation(config)
    if augmentation:
        evaluation["perturbed_fields"] = {
            "note": "Synthetic perturbation of the validation fields; not used to select any checkpoint.",
            "summary": report(augmentation)}
    (run.dir / "evaluation.json").write_text(json.dumps(evaluation, indent=2), encoding="utf-8")
    run.manifest["evaluation"] = {"mean_return": evaluation["summary"]["mean_return"],
                                  "success_rate": evaluation["summary"]["success_rate"],
                                  "mean_optimality_gap": evaluation["summary"].get("mean_optimality_gap"),
                                  "reload_check": evaluation["reload_check"]}
    run.save()


def train_set(config, fields_dir, out=None, *, resume=None, graph_dir=DEFAULT_GRAPH, device="cpu", router=None,
              od_pairs=None, max_seconds=None, guard_factory=None) -> PolicySetRun:
    """Train every member not finished yet, then evaluate the set. Failures are recorded, then re-raised."""
    torch.set_num_threads(thread_limit(config.get("threads", 4)))
    run = PolicySetRun(config, out, resume, device)
    previous = signal.signal(signal.SIGTERM, _raise_terminated)
    started = time.perf_counter()
    try:
        router = router or get_router(graph_dir, config["mode"])
        pairs_path = run.dir / "od_pairs.json"
        if not pairs_path.exists():
            train_od, val_od = sample_od(router, config, run.dir, od_pairs)
        else:  # The set's own pairs, not a new sample.
            train_od = load_od_pairs(pairs_path, split="train", graph_version=router.version)
            val_od = load_od_pairs(pairs_path, split="val", graph_version=router.version)
        run.manifest["data"] = {"graph_version": router.version, "mode": router.mode,
                                "od_pairs_sha256": file_sha256(pairs_path),
                                "od_pairs": {"train": len(train_od), "val": len(val_od)},
                                "test_split_loaded": False}
        run.save()
        for index, member in enumerate(run.manifest["members"]):
            if member["status"] in FINISHED:
                continue
            budget = None if max_seconds is None else max_seconds - (time.perf_counter() - started)
            _train_member(run, member, config, index, fields_dir, train_od + val_od, graph_dir=graph_dir,
                          device=device, router=router, max_seconds=budget,
                          guard=guard_factory(member["alpha"]) if guard_factory else None)
            if member["status"] not in FINISHED:
                run.finish(member["status"], f"Member alpha={member['alpha']:g}: {member['stop_reason']}")
                return run
        evaluate_set(run, config, router, fields_dir, val_od, device)
        run.finish("completed", f"All {len(run.manifest['members'])} members finished")
    except (KeyboardInterrupt, _Terminated) as exc:
        run.finish("interrupted", f"{type(exc).__name__}: {exc}")
    except Exception as exc:  # noqa: BLE001 - recorded for the audit trail, then raised
        run.manifest["traceback"] = traceback.format_exc()
        run.finish("failed", f"{type(exc).__name__}: {exc}")
        raise
    finally:
        signal.signal(signal.SIGTERM, previous)
    return run


def main():
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--config", required=True, type=Path)
    parser.add_argument("--fields", required=True, type=Path)
    parser.add_argument("--out", type=Path, help="parent directory of a new policy set")
    parser.add_argument("--resume", type=Path, help="policy set directory to continue")
    parser.add_argument("--graph-dir", type=Path, default=DEFAULT_GRAPH)
    parser.add_argument("--device", default="auto", help="auto (cuda if available, else cpu), cpu, cuda, mps, ...")
    parser.add_argument("--max-hours", type=float, help="stop cleanly after this long; a recorded stop, not a failure")
    args = parser.parse_args()
    if bool(args.out) == bool(args.resume):
        parser.error("give exactly one of --out and --resume")
    device = args.device if args.device != "auto" else ("cuda" if torch.cuda.is_available() else "cpu")
    run = train_set(load_config(args.config), args.fields, args.out, resume=args.resume, graph_dir=args.graph_dir,
                    device=device, max_seconds=args.max_hours * 3600 if args.max_hours else None)
    print(json.dumps({k: run.manifest.get(k) for k in ("set_id", "status", "stop_reason", "evaluation")}, indent=2))
    print(run.dir)


if __name__ == "__main__":
    main()
