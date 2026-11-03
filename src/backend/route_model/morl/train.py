"""Train one member of the multi-policy set, for one fixed preference (PIC B of TI-AI-09).

multi_policy.py is the entry point; it calls train() once per alpha.

Algorithm: PPO on the TI-AI-08 environment, under the member's fixed (alpha, beta).
Each episode draws an OD pair and a time window. An optional warm start imitates a
teacher route, because a random policy almost never reaches the destination
(TI-AI-08: 0 of 10), which leaves PPO nothing to improve on.

Training episodes may run on perturbed fields (augment.py). Only the train and val
splits are ever loaded. The checkpoint is selected by the mean return of the greedy
policy on the real validation fields (see evaluate), never on test or perturbed ones.

A run directory holds config.json, od_pairs.json, manifest.json, metrics.jsonl,
curves.csv, evaluation.json and checkpoints/. manifest.json is rewritten at every
evaluation and again however the run ends, with `status` and `stop_reason`, so a
failed or interrupted run still says what happened and which artifacts it left.

resources.py stops a run cleanly at its memory and disk limits. An interrupted or
stopped run can continue from its last.pt; the resumed part starts new episodes from
a seed derived from the update, so it is reproducible but not bit-identical to an
uninterrupted run. The manifest lists every resume.
"""
from __future__ import annotations

import copy
import csv
import datetime as dt
import hashlib
import json
import math
import platform
import random
import shutil
import signal
import statistics
import subprocess
import time
import traceback
from pathlib import Path

import numpy as np
import torch

from ..astar_osm import DEFAULT_GRAPH, get_router
from ..training_env import (
    ENV_VERSION, EnvConfig, RouteEnv, SplitSpec, file_sha256, load_od_pairs, load_weights,
    sample_od_pairs, save_od_pairs,
)
from .augment import Augmentation, PerturbedExposure, SmoothField
from .features import FEATURE_VERSION, build_sample, collate, episode_context
from .infer import PolicyFailure, decode_route, load_policy, optimal_weighted_route, save_checkpoint
from .policy import ActorCritic, ModelConfig, entropy, sample_actions, sample_offsets, to_tensors
from .resources import ResourceGuard, ResourceLimits, prune_checkpoints, release_accelerator_cache, thread_limit

ROOT = Path(__file__).resolve().parents[4]
ALGORITHM = "ppo-fixed-preference-1"


class TrainingDiverged(RuntimeError):
    """The loss or the parameters are no longer finite."""


class _Terminated(BaseException):
    """SIGTERM (a scheduler stopping the job), handled like Ctrl-C."""


def _raise_terminated(signum, frame):
    raise _Terminated(f"signal {signum}")


def load_config(path) -> dict:
    return json.loads(Path(path).read_text(encoding="utf-8"))


def config_digest(config) -> str:
    return hashlib.sha256(json.dumps(config, sort_keys=True).encode()).hexdigest()


def seed_everything(seed):
    random.seed(seed)
    np.random.seed(seed % 2**32)
    torch.manual_seed(seed)


def git_state():
    def git(*args):
        try:
            return subprocess.run(["git", "-C", str(ROOT), *args], capture_output=True, text=True, check=True).stdout.strip()
        except (OSError, subprocess.CalledProcessError):
            return None
    status = git("status", "--porcelain")
    return {"commit": git("rev-parse", "HEAD"), "branch": git("rev-parse", "--abbrev-ref", "HEAD"),
            "dirty": bool(status) if status is not None else None}


# ------------------------------------------------------------------ environments

def build_envs(router, fields_dir, od_pairs, env_config, split, seed):
    """One RouteEnv per time window of `split`, from the fields manifest.

    Windows of other splits are named for the split check, but their data is never read.
    """
    manifest = json.loads((Path(fields_dir) / "manifest.json").read_text(encoding="utf-8"))
    if manifest["graph_version"] != router.version:
        raise ValueError(f"Fields were built on graph {manifest['graph_version']}, not {router.version}")
    window_splits = {name: tuple(windows) for name, windows in manifest["window_splits"].items()}
    envs = []
    for window in window_splits[split]:
        weights = load_weights(Path(fields_dir) / manifest["windows"][window]["file"], router.version, window)
        envs.append(RouteEnv(router, weights, od_pairs, EnvConfig(split=split, seed=seed, **env_config),
                             window_splits=window_splits))
    if not envs:
        raise ValueError(f"The fields manifest has no {split} window")
    return envs, manifest


class Actor:
    """One stream of episodes. Environments are shallow copies, so all actors share
    each window's 300k-entry exposure table instead of holding their own."""

    def __init__(self, envs, seed, model_config: ModelConfig, alpha: float, augmentation: Augmentation | None = None):
        self.rng, self.alpha = random.Random(seed), alpha
        self.envs = [copy.copy(env) for env in envs]
        for i, env in enumerate(self.envs):
            env.rng = random.Random(self.rng.randrange(2**32) + i)
        self.config, self.augmentation = model_config, augmentation
        self.reset()

    def reset(self, env=None, field=None, **kwargs):
        self.env = env or self.rng.choice(self.envs)
        env = self.env
        if field is None and self.augmentation and self.rng.random() < self.augmentation.probability:
            field = SmoothField(self.augmentation, self.rng)
        # The copy's table is replaced for this episode; the real field stays in the shared original.
        real = getattr(env, "real_exposure", None)
        if real is None:
            real = env.real_exposure = env.exposure
        env.exposure = PerturbedExposure(real, env.router, field) if field else real
        env.reset(preference=(self.alpha, 1.0 - self.alpha), **kwargs)
        self.context = episode_context(env.router, env.exposure, env.od.start, env.od.end, env.reference_time,
                                       env._exposure_scale, env.step_limit)

    def sample(self):
        env = self.env
        return build_sample(env.router, env.exposure, self.context, env.node, env.visited, env.time_s,
                            env.exposure_total, env.steps, self.config.ego_hops, self.config.ego_max_nodes)


# ------------------------------------------------------------------ warm start

def teacher_edges(env, teacher):
    if teacher == "fastest":
        return list(env.reference_edge_ids)
    if teacher == "weighted":
        edges, _ = optimal_weighted_route(env.router, env.exposure, env.od.start, env.od.end, env.alpha, env.beta,
                                          env.reference_time, env.reference_exposure)
        return edges
    raise ValueError(f"Unknown teacher {teacher!r}")


def collect_demonstrations(actor, episodes, teacher):
    """(sample, teacher action) along teacher routes; simple paths only, as the environment masks revisits."""
    samples, actions = [], []
    for _ in range(episodes):
        actor.reset()
        env = actor.env
        for edge_id in teacher_edges(env, teacher):
            action = next(i for i, arc in enumerate(env.router.arcs[env.node]) if arc[2] == edge_id)
            samples.append(actor.sample())
            actions.append(action)
            env.step(action)
    return samples, actions


def warm_start(model, optimizer_lr, samples, actions, cfg, device, generator, log):
    optimizer = torch.optim.Adam(model.parameters(), lr=optimizer_lr)
    for epoch in range(cfg["epochs"]):
        order = torch.randperm(len(samples), generator=generator).tolist()
        losses, hits = [], []
        for start in range(0, len(order), cfg["minibatch_size"]):
            chosen = order[start:start + cfg["minibatch_size"]]
            batch = to_tensors(collate([samples[i] for i in chosen]), device)
            log_probs, _ = model(batch)
            size = len(chosen)
            target = torch.as_tensor([actions[i] for i in chosen], device=device)
            offsets = sample_offsets(batch["action_batch"], size)
            loss = -log_probs[offsets + target].mean()
            optimizer.zero_grad()
            loss.backward()
            optimizer.step()
            losses.append(loss.item())
            hits.append((sample_actions(log_probs.detach(), batch["action_batch"], size, greedy=True)[0] == target)
                        .float().mean().item())
        log({"phase": "warm_start", "epoch": epoch, "loss": statistics.fmean(losses),
             "teacher_action_accuracy": statistics.fmean(hits)})


# ------------------------------------------------------------------ evaluation

@torch.inference_mode()
def evaluate(model, val_envs, od_pairs, alphas, model_config, device, batch_size, oracle=False, augmentation=None):
    """Greedy policy on every (validation OD, alpha); each OD keeps one window across alphas,
    so routes at different preferences compare like with like.

    `mean_return` selects the checkpoint: a reached route scores -(alpha T/T_ref +
    beta E/E_ref), a failure scores -failure_return, so it rewards both reaching the
    destination and the trade-off at each preference.

    With `augmentation`, each OD pair gets a fixed perturbed field, the same at every
    preference, which shows how the route responds where a trade-off exists.
    """
    model.eval()
    jobs = [(od, alpha, val_envs[i % len(val_envs)],
             SmoothField(augmentation, random.Random(i)) if augmentation else None)
            for i, od in enumerate(od_pairs) for alpha in alphas]
    records, slots, position = [], [], 0
    while position < len(jobs) or slots:
        while position < len(jobs) and len(slots) < batch_size:
            od, alpha, env, field = jobs[position]
            position += 1
            actor = Actor.__new__(Actor)
            actor.config, actor.augmentation, actor.alpha = model_config, None, alpha
            actor.reset(env=copy.copy(env), field=field, od_id=od.od_id)
            slots.append(actor)
        batch = to_tensors(collate([a.sample() for a in slots]), device)
        log_probs, _ = model(batch)
        actions = sample_actions(log_probs, batch["action_batch"], len(slots), greedy=True)[0].tolist()
        remaining = []
        for actor, action in zip(slots, actions):
            *_, info = actor.env.step(action)
            if info["outcome"]:
                record = info["episode"]
                if oracle:
                    env = actor.env
                    edges, record["oracle_weighted_cost"] = optimal_weighted_route(
                        env.router, env.exposure, env.od.start, env.od.end, env.alpha, env.beta,
                        env.reference_time, env.reference_exposure)
                    times = [env.router.edges[e].time_s for e in edges]
                    record["oracle_time_ratio"] = math.fsum(times) / env.reference_time
                    record["oracle_exposure_ratio"] = math.fsum(
                        env.exposure[e] * t for e, t in zip(edges, times)) / env._exposure_scale
                records.append(record)
            else:
                remaining.append(actor)
        slots = remaining
    model.train()
    return summarize_evaluation(records), records


def summarize_evaluation(records):
    reached = [r for r in records if r["outcome"] == "reached"]
    mean = lambda rows, key: statistics.fmean(r[key] for r in rows) if rows else None
    by_alpha = {}
    for alpha in sorted({r["alpha"] for r in records}):
        rows = [r for r in records if r["alpha"] == alpha]
        good = [r for r in rows if r["outcome"] == "reached"]
        by_alpha[f"{alpha:g}"] = {"episodes": len(rows), "success_rate": len(good) / len(rows),
                                  "mean_return": mean(rows, "return"), "time_ratio": mean(good, "time_ratio"),
                                  "exposure_ratio": mean(good, "exposure_ratio"),
                                  "weighted_cost": mean(good, "weighted_cost")}
        if rows and "oracle_time_ratio" in rows[0]:
            by_alpha[f"{alpha:g}"].update(oracle_time_ratio=mean(rows, "oracle_time_ratio"),
                                          oracle_exposure_ratio=mean(rows, "oracle_exposure_ratio"))
    summary = {"episodes": len(records), "mean_return": mean(records, "return"),
               "success_rate": len(reached) / len(records) if records else None,
               "outcomes": {o: sum(r["outcome"] == o for r in records) for o in ("reached", "dead_end", "truncated")},
               "mean_steps": mean(records, "steps"), "by_alpha": by_alpha}
    oracle = [r for r in reached if r.get("oracle_weighted_cost")]
    if oracle:
        summary["mean_optimality_gap"] = statistics.fmean(
            r["weighted_cost"] / r["oracle_weighted_cost"] - 1 for r in oracle)
    return summary


# ------------------------------------------------------------------ PPO

@torch.inference_mode()
def collect_rollout(model, actors, steps, device):
    """`steps` transitions per actor; returns per-actor trajectories and the last values."""
    trajectories = [[] for _ in actors]
    finished = []
    for _ in range(steps):
        samples = [a.sample() for a in actors]
        batch = to_tensors(collate(samples), device)
        log_probs, values = model(batch)
        size = len(actors)
        actions, flat = sample_actions(log_probs, batch["action_batch"], size)
        chosen = log_probs[flat].tolist()
        values = values.tolist()
        for i, (actor, action) in enumerate(zip(actors, actions.tolist())):
            _, reward, terminated, truncated, info = actor.env.step(action)
            done = terminated or truncated
            trajectories[i].append({"sample": samples[i], "action": action, "log_prob": chosen[i], "value": values[i],
                                    "reward": reward, "done": done})
            if done:
                finished.append(info["episode"])
                actor.reset()
    batch = to_tensors(collate([a.sample() for a in actors]), device)
    _, last = model(batch)
    return trajectories, last.tolist(), finished


def advantages(trajectory, last_value, gamma, lam):
    """Generalised advantage estimation; a finished episode cuts the bootstrap."""
    adv = 0.0
    for t in range(len(trajectory) - 1, -1, -1):
        step = trajectory[t]
        following = trajectory[t + 1]["value"] if t + 1 < len(trajectory) else last_value
        keep = 0.0 if step["done"] else 1.0
        adv = step["reward"] + gamma * following * keep - step["value"] + gamma * lam * keep * adv
        step["advantage"], step["returns"] = adv, adv + step["value"]


def ppo_update(model, optimizer, steps, cfg, device, generator, train_policy=True):
    """`train_policy=False` fits only the critic: an untrained critic's advantages are
    noise, and following them would undo what the warm start taught the actor."""
    stats = {"policy_loss": [], "value_loss": [], "entropy": [], "approx_kl": [], "clip_fraction": []}
    for _ in range(cfg["epochs"]):
        order = torch.randperm(len(steps), generator=generator).tolist()
        for start in range(0, len(order), cfg["minibatch_size"]):
            chosen = [steps[i] for i in order[start:start + cfg["minibatch_size"]]]
            size = len(chosen)
            batch = to_tensors(collate([s["sample"] for s in chosen]), device)
            log_probs, values = model(batch)
            offsets = sample_offsets(batch["action_batch"], size)
            new = log_probs[offsets + torch.as_tensor([s["action"] for s in chosen], device=device)]
            old = torch.as_tensor([s["log_prob"] for s in chosen], device=device, dtype=new.dtype)
            adv = torch.as_tensor([s["advantage"] for s in chosen], device=device, dtype=new.dtype)
            adv = (adv - adv.mean()) / (adv.std() + 1e-8) if size > 1 else adv
            ratio = (new - old).exp()
            policy_loss = -torch.min(ratio * adv, ratio.clamp(1 - cfg["clip"], 1 + cfg["clip"]) * adv).mean()
            target = torch.as_tensor([s["returns"] for s in chosen], device=device, dtype=values.dtype)
            value_loss = ((values - target) ** 2).mean()
            ent = entropy(log_probs, batch["action_batch"], size).mean()
            loss = cfg["value_coef"] * value_loss
            if train_policy:
                loss = loss + policy_loss - cfg["entropy_coef"] * ent
            if not torch.isfinite(loss):
                raise TrainingDiverged(f"Non-finite loss: policy {policy_loss.item()}, value {value_loss.item()}")
            optimizer.zero_grad()
            loss.backward()
            torch.nn.utils.clip_grad_norm_(model.parameters(), cfg["max_grad_norm"])
            optimizer.step()
            stats["policy_loss"].append(policy_loss.item())
            stats["value_loss"].append(value_loss.item())
            stats["entropy"].append(ent.item())
            stats["approx_kl"].append((old - new).mean().item())
            stats["clip_fraction"].append(((ratio - 1).abs() > cfg["clip"]).float().mean().item())
    if not all(torch.isfinite(p).all() for p in model.parameters()):
        raise TrainingDiverged("Non-finite parameters after update")
    return {k: statistics.fmean(v) for k, v in stats.items()}


# ------------------------------------------------------------------ run

class Run:
    """The run directory: everything needed to identify, audit and resume reading a run."""

    # Set during optimisation: writes checkpoints/last.pt, so an interrupted run can continue.
    save_last = None

    @classmethod
    def reopen(cls, run_dir, config, device):
        """An existing run directory, for --resume; refuses a different configuration."""
        run = cls.__new__(cls)
        run.dir = Path(run_dir)
        run.manifest = json.loads((run.dir / "manifest.json").read_text(encoding="utf-8"))
        if run.manifest["config_sha256"] != config_digest(config):
            raise ValueError("--config differs from the run being resumed")
        if not (run.dir / "checkpoints" / "last.pt").exists():
            raise ValueError(f"{run.dir} has no checkpoints/last.pt to resume from")
        run.run_id, run.metrics_path = run.manifest["run_id"], run.dir / "metrics.jsonl"
        run.manifest.setdefault("resumes", []).append({
            "at": dt.datetime.now(dt.timezone.utc).isoformat(timespec="seconds"), "device": str(device),
            "previous_status": run.manifest["status"], "previous_stop_reason": run.manifest["stop_reason"],
            "code": git_state()})
        run.manifest.update(status="running", stop_reason=None, finished_at=None)
        run.manifest.pop("traceback", None)
        run.save()
        return run

    def __init__(self, out, config, fields_dir, device, graph_dir):
        started = dt.datetime.now(dt.timezone.utc)
        digest = config_digest(config)
        self.run_id = f"{started:%Y%m%dT%H%M%SZ}-{config['name']}-{digest[:8]}"
        self.dir = Path(out) / self.run_id
        attempt = 1
        while self.dir.exists():  # same second, same configuration: never mix two runs' files
            attempt += 1
            self.dir = Path(out) / f"{self.run_id}-{attempt}"
        self.run_id = self.dir.name
        (self.dir / "checkpoints").mkdir(parents=True)
        (self.dir / "config.json").write_text(json.dumps(config, indent=2), encoding="utf-8")
        self.metrics_path = self.dir / "metrics.jsonl"
        self.manifest = {
            "run_id": self.run_id, "experiment": "morl-route-policy", "status": "running", "stop_reason": None,
            "started_at": started.isoformat(timespec="seconds"), "finished_at": None,
            "algorithm": ALGORITHM, "env_version": ENV_VERSION, "feature_version": FEATURE_VERSION,
            "threads": torch.get_num_threads(), "resource_limits": resource_limits(config).as_dict(),
            "config_sha256": digest, "config": config, "alpha": config["alpha"], "seed": config["seed"],
            "device": str(device),
            "code": git_state(), "versions": {"python": platform.python_version(), "torch": torch.__version__,
                                              "numpy": np.__version__},
            "fields_dir": str(fields_dir), "graph_dir": str(graph_dir), "data": {},
            "checkpoint_selection": {"metric": "validation mean_return (greedy policy; failure = -failure_return)",
                                     "split": "val", "test_split_loaded": False},
            "progress": {"updates": 0, "env_steps": 0, "episodes": 0}, "best": None, "artifacts": [],
        }
        self.save()

    def log(self, record):
        with self.metrics_path.open("a", encoding="utf-8") as stream:
            stream.write(json.dumps(record, allow_nan=False) + "\n")

    def save(self):
        self.manifest["artifacts"] = sorted(p.relative_to(self.dir).as_posix() for p in self.dir.rglob("*")
                                            if p.is_file() and p.name != "manifest.json")
        path = self.dir / "manifest.json"
        temporary = path.with_suffix(".tmp")
        temporary.write_text(json.dumps(self.manifest, indent=2), encoding="utf-8")
        temporary.replace(path)

    def finish(self, status, reason):
        self.manifest.update(status=status, stop_reason=reason,
                             finished_at=dt.datetime.now(dt.timezone.utc).isoformat(timespec="seconds"))
        write_curves(self.metrics_path, self.dir / "curves.csv")
        self.save()


def write_curves(metrics_path, out):
    if not metrics_path.exists():
        return
    records = [json.loads(line) for line in metrics_path.read_text(encoding="utf-8").splitlines()]
    # After a resume the updates since the last checkpoint run again: the later record wins.
    train = {r["update"]: r for r in records if r.get("phase") == "train"}.values()
    val = {r["update"]: r for r in records if r.get("phase") == "val"}
    columns = ["update", "env_steps", "elapsed_s", "rss_mb", "train_mean_return", "train_success_rate", "policy_loss",
               "value_loss", "entropy", "approx_kl", "clip_fraction", "val_mean_return", "val_success_rate"]
    with out.open("w", newline="", encoding="utf-8") as stream:
        writer = csv.DictWriter(stream, columns, extrasaction="ignore")
        writer.writeheader()
        for row in train:
            evaluation = val.get(row["update"], {})
            writer.writerow({**row, "val_mean_return": evaluation.get("mean_return"),
                             "val_success_rate": evaluation.get("success_rate")})


def resource_limits(config) -> ResourceLimits:
    return ResourceLimits(**config.get("resources", {}))


def training_augmentation(config) -> Augmentation | None:
    settings = config.get("augmentation")
    return Augmentation(**settings) if settings else None


def sample_od(router, config, run_dir, given=None):
    """Train and val OD pairs, saved with the run; `given` replaces the sampling."""
    od = config["od"]
    spec = SplitSpec(seed=od["split_seed"])
    if given is None:
        # No test pair is ever sampled here; TI-AI-10 samples its own from the same split rule.
        pairs, stats = sample_od_pairs(router, spec, {"train": od["train_pairs"], "val": od["val_pairs"]},
                                       seed=od["seed"], min_distance_m=od["min_distance_m"],
                                       max_distance_m=od["max_distance_m"])
    else:
        pairs, stats = list(given), {"given": True}
    path = Path(run_dir) / "od_pairs.json"
    save_od_pairs(path, pairs, spec, stats, router.version)
    return (load_od_pairs(path, split="train", graph_version=router.version),
            load_od_pairs(path, split="val", graph_version=router.version))


def train(config, fields_dir, out=None, *, graph_dir=DEFAULT_GRAPH, device="cpu", router=None, max_seconds=None,
          od_pairs=None, resume=None, guard=None):
    """Run or resume one training; returns the Run. Exceptions are recorded in the manifest, then re-raised."""
    torch.set_num_threads(thread_limit(config.get("threads", 4)))
    seed_everything(config["seed"])
    device = torch.device(device)
    run = Run.reopen(resume, config, device) if resume else Run(out, config, fields_dir, device, graph_dir)
    guard = guard or ResourceGuard(resource_limits(config), run.dir, device)
    previous = signal.signal(signal.SIGTERM, _raise_terminated)
    try:
        status, reason = _train(run, config, fields_dir, graph_dir, device, router, max_seconds, od_pairs, guard,
                                resume is not None)
        run.finish(status, reason)
    except TrainingDiverged as exc:
        run.finish("failed", f"diverged: {exc}")
        raise
    except (KeyboardInterrupt, _Terminated) as exc:
        if run.save_last:
            run.save_last()
        run.finish("interrupted", f"{type(exc).__name__}: {exc}")
    except Exception as exc:  # noqa: BLE001 - recorded for the audit trail, then raised
        run.manifest["traceback"] = traceback.format_exc()
        run.finish("failed", f"{type(exc).__name__}: {exc}")
        raise
    finally:
        signal.signal(signal.SIGTERM, previous)
    return run


def final_evaluation(run, router, val_envs, val_od, config, device):
    """evaluation.json: the selected checkpoint, reloaded from disk, on the validation set.

    Compares it with the exact optimum per preference, and repeats inference from a
    second fresh load to show the checkpoint reproduces its routes.
    """
    path = run.dir / "checkpoints" / "best.pt"
    model, checkpoint = load_policy(path, device)
    schedule, alphas = config["schedule"], [config["alpha"]]
    summary, _ = evaluate(model, val_envs, val_od, alphas, model.config, device, schedule["eval_batch"], oracle=True)

    def routes(policy):
        result = []
        for i, od in enumerate(val_od):
            exposure = val_envs[i % len(val_envs)].exposure
            try:
                result.append(list(decode_route(policy, router, exposure, od.start, od.end,
                                                max_steps=config["env"]["max_steps"],
                                                max_steps_factor=config["env"]["max_steps_factor"],
                                                device=device).edge_ids))
            except PolicyFailure as failure:
                result.append(failure.reason)
        return result

    first, second = routes(model), routes(load_policy(path, device)[0])
    evaluation = {"checkpoint": {"update": checkpoint["update"], "sha256": file_sha256(path)}, "split": "val",
                  "alpha": config["alpha"], "summary": summary,
                  "reload_check": {"od_pairs": len(val_od), "identical": first == second,
                                   "routes_reached": sum(isinstance(r, list) for r in first)}}
    augmentation = training_augmentation(config)
    if augmentation:
        evaluation["perturbed_fields"] = {
            "note": "Synthetic perturbation of the validation fields; not used to select the checkpoint.",
            "summary": evaluate(model, val_envs, val_od, alphas, model.config, device,
                                schedule["eval_batch"], oracle=True, augmentation=augmentation)[0]}
    (run.dir / "evaluation.json").write_text(json.dumps(evaluation, indent=2), encoding="utf-8")
    run.manifest["evaluation"] = {k: evaluation[k] for k in ("reload_check",)} | {
        "mean_return": summary["mean_return"], "success_rate": summary["success_rate"],
        "mean_optimality_gap": summary.get("mean_optimality_gap")}
    run.save()


def _train(run, config, fields_dir, graph_dir, device, router, max_seconds, od_pairs, guard, resuming):
    result = _optimise(run, config, fields_dir, graph_dir, device, router, max_seconds, od_pairs, guard, resuming)
    final_evaluation(run, *result[2:], config, device)
    return result[:2]


def _optimise(run, config, fields_dir, graph_dir, device, router, max_seconds, od_pairs, guard, resuming):
    started = time.perf_counter()
    router = router or get_router(graph_dir, config["mode"])
    if resuming:  # The run's own pairs, not a new sample.
        train_od = load_od_pairs(run.dir / "od_pairs.json", split="train", graph_version=router.version)
        val_od = load_od_pairs(run.dir / "od_pairs.json", split="val", graph_version=router.version)
    else:
        train_od, val_od = sample_od(router, config, run.dir, od_pairs)
    train_envs, fields = build_envs(router, fields_dir, train_od, config["env"], "train", config["seed"])
    val_envs, _ = build_envs(router, fields_dir, val_od, config["env"], "val", config["seed"])
    run.manifest["data"] = {
        "graph_version": router.version, "mode": router.mode,
        "fields": {"dataset": fields["dataset"], "estimator": fields["estimator"],
                   "manifest_sha256": file_sha256(Path(fields_dir) / "manifest.json")},
        "graph_files_sha256": fields["graph_files_sha256"],
        "train": {"od_pairs": len(train_od), "windows": fields["window_splits"]["train"],
                  "env": train_envs[0].manifest()["config"],
                  "window_sha256": {e.weights.time_window: e.manifest()["weights"]["sha256"] for e in train_envs}},
        "val": {"od_pairs": len(val_od), "windows": fields["window_splits"]["val"],
                "window_sha256": {e.weights.time_window: e.manifest()["weights"]["sha256"] for e in val_envs}},
        "od_pairs_sha256": file_sha256(run.dir / "od_pairs.json"),
    }
    run.save()

    model_config = ModelConfig(**config["model"])
    model = ActorCritic(model_config).to(device)
    run.manifest["parameters"] = sum(p.numel() for p in model.parameters())
    generator = torch.Generator().manual_seed(config["seed"])
    ppo, schedule = config["ppo"], config["schedule"]
    alphas, alpha = [config["alpha"]], config["alpha"]
    optimizer = torch.optim.Adam(model.parameters(), lr=ppo["learning_rate"])
    limits = resource_limits(config)
    progress = {"update": 0, "env_steps": 0, "episodes": 0}
    best_return, stale = -math.inf, 0
    if resuming:
        saved = torch.load(run.dir / "checkpoints" / "last.pt", map_location=device, weights_only=True)
        model.load_state_dict(saved["model_state"])
        optimizer.load_state_dict(saved["optimizer_state"])
        progress = dict(saved["progress"])
        best_return, stale = saved["best_return"], saved["stale"]
        seed_everything(config["seed"] + progress["update"])
        generator.manual_seed(config["seed"] + progress["update"])
    actor_seed = config["seed"] + 1000 + progress["update"] * ppo["actors"]
    augmentation = training_augmentation(config)
    run.manifest["data"]["train"]["augmentation"] = {
        "synthetic": True, "used_for": "training episodes only", **augmentation.as_dict()} if augmentation else None
    actors = [Actor(train_envs, actor_seed + i, model_config, alpha, augmentation) for i in range(ppo["actors"])]

    def checkpoint(update, metrics, name=None):
        path = run.dir / "checkpoints" / (name or f"update_{update:05d}.pt")
        save_checkpoint(path, model, run_id=run.run_id, update=update, config_sha256=run.manifest["config_sha256"],
                        graph_version=router.version, mode=router.mode, val_metrics=json.loads(json.dumps(metrics)))
        return path

    def save_last():
        save_checkpoint(run.dir / "checkpoints" / "last.pt", model, run_id=run.run_id, update=progress["update"],
                        config_sha256=run.manifest["config_sha256"], graph_version=router.version, mode=router.mode,
                        optimizer_state=optimizer.state_dict(), progress=dict(progress), best_return=best_return,
                        stale=stale)
        run.save()

    def validate(update):
        """Evaluate, log and keep the best checkpoint; returns True when it improved."""
        nonlocal best_return, stale
        summary, _ = evaluate(model, val_envs, val_od, alphas, model_config, device, schedule["eval_batch"])
        run.log({"phase": "val", "update": update, **summary})
        if augmentation:
            run.log({"phase": "val_perturbed", "update": update,
                     **evaluate(model, val_envs, val_od, alphas, model_config, device, schedule["eval_batch"],
                                augmentation=augmentation)[0]})
        path = checkpoint(update, summary)
        improved = summary["mean_return"] > best_return
        if improved:
            best_return, stale = summary["mean_return"], 0
            shutil.copyfile(path, run.dir / "checkpoints" / "best.pt")
            run.manifest["best"] = {"update": update, "checkpoint": "checkpoints/best.pt",
                                    "sha256": file_sha256(run.dir / "checkpoints" / "best.pt"),
                                    "val_mean_return": summary["mean_return"],
                                    "val_success_rate": summary["success_rate"]}
        else:
            stale += 1
        prune_checkpoints(run.dir / "checkpoints", limits.keep_checkpoints)
        release_accelerator_cache(device)
        save_last()
        print(f"update {update}: val return {summary['mean_return']:.3f}, success {summary['success_rate']:.3f}"
              f"{' (best)' if improved else ''}", flush=True)
        return improved

    if not resuming:
        teacher = config.get("warm_start")
        if teacher and teacher.get("episodes"):
            demo_actor = Actor(train_envs, config["seed"] + 500, model_config, alpha, augmentation)
            samples, actions = collect_demonstrations(demo_actor, teacher["episodes"], teacher["teacher"])
            warm_start(model, teacher["learning_rate"], samples, actions, teacher, device, generator, run.log)
            del demo_actor, samples, actions
        validate(0)
    run.save_last = save_last

    for update in range(progress["update"] + 1, schedule["max_updates"] + 1):
        trajectories, last_values, finished = collect_rollout(model, actors, ppo["rollout_steps"], device)
        steps = []
        for trajectory, last in zip(trajectories, last_values):
            advantages(trajectory, last, ppo["gamma"], ppo["gae_lambda"])
            steps.extend(trajectory)
        stats = ppo_update(model, optimizer, steps, ppo, device, generator,
                           train_policy=update > ppo.get("critic_warmup_updates", 0))
        progress.update(update=update, env_steps=progress["env_steps"] + len(steps),
                        episodes=progress["episodes"] + len(finished))
        reached = sum(r["outcome"] == "reached" for r in finished)
        usage, limit_reason = guard.check()
        run.log({"phase": "train", "update": update, "env_steps": progress["env_steps"],
                 "episodes": progress["episodes"], "elapsed_s": round(time.perf_counter() - started, 1), **usage,
                 "train_mean_return": statistics.fmean(r["return"] for r in finished) if finished else None,
                 "train_success_rate": reached / len(finished) if finished else None, **stats})
        run.manifest["progress"] = {"updates": update, "env_steps": progress["env_steps"],
                                    "episodes": progress["episodes"]}
        if limit_reason:
            save_last()
            return "stopped", limit_reason, router, val_envs, val_od

        if update % schedule["eval_every"] == 0 or update == schedule["max_updates"]:
            validate(update)
            if stale >= schedule["early_stop_patience"]:
                return "early_stopped", f"No validation improvement in {stale} evaluations", router, val_envs, val_od
        if max_seconds is not None and time.perf_counter() - started > max_seconds:
            validate(update)
            return "stopped", f"Time budget of {max_seconds} s reached at update {update}", router, val_envs, val_od
    return "completed", f"Reached max_updates = {schedule['max_updates']}", router, val_envs, val_od
