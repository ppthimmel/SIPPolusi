"""TI-AI-09, PIC B: ego-graph features, Actor-Critic, checkpoints, the policy set and its run record.

Small graphs with hand-known routes; the training tests run in seconds on CPU.
"""
import json
import math
import random
import tempfile
import unittest
from pathlib import Path
from unittest import mock

import numpy as np
import torch

from src.backend.route_model.astar_osm import Edge, RoadRouter
from src.backend.route_model.morl import fields as fields_module
from src.backend.route_model.morl import multi_policy as multi_policy_module
from src.backend.route_model.morl import train as train_module
from src.backend.route_model.morl.augment import Augmentation, PerturbedExposure, SmoothField
from src.backend.route_model.morl.features import (
    EDGE_FEATURES, EpisodeContext, build_sample, collate, cost_to_go, episode_context,
)
from src.backend.route_model.morl.infer import (
    PolicyFailure, decode_for_preference, decode_route, fastest_reference, load_policy, load_policy_set, nearest_alpha,
    optimal_weighted_route, save_checkpoint,
)
from src.backend.route_model.morl.multi_policy import member_config, train_set
from src.backend.route_model.morl.policy import ActorCritic, ModelConfig, entropy, sample_actions, to_tensors
from src.backend.route_model.morl.resources import (
    GB, MB, ResourceGuard, ResourceLimits, current_rss_bytes, prune_checkpoints, thread_limit,
)
from src.backend.route_model.morl.train import Actor, TrainingDiverged, build_envs, evaluate, train
from src.backend.route_model.training_env import ODPair, RouteEnv

SMALL = ModelConfig(hidden_dim=16, graph_layers=2, ego_hops=3, ego_max_nodes=32)
TRAIN_WINDOW, VAL_WINDOW, TEST_WINDOW = "2026-09-13T00:00:00Z", "2026-09-25T00:00:00Z", "2026-09-29T00:00:00Z"


def two_route_router():
    """1-2-4 is fast but polluted (T=20 s, E=80); 1-3-4 is slow but clean (T=30 s, E=30); 2-5 is a dead end."""
    coords = {1: (0, 0), 2: (.001, .001), 3: (.001, -.001), 4: (.002, 0), 5: (.003, .002)}
    specs = [(12, 1, 2, 10), (24, 2, 4, 10), (13, 1, 3, 15), (34, 3, 4, 15), (25, 2, 5, 5)]
    edges = []
    for edge_id, u, v, seconds in specs:
        a, b = coords[u], coords[v]
        edges.append((Edge(edge_id, u, v, seconds, seconds, f"LINESTRING({a[0]} {a[1]},{b[0]} {b[1]})"), False))
    return RoadRouter("test-v1", "bike", coords, edges)


EXPOSURE = {12: 4.0, 24: 4.0, 13: 1.0, 34: 1.0, 25: 1.0}


def context_for(router, exposure, start=1, end=4, step_limit=10):
    _, time_ref, exposure_ref = fastest_reference(router, exposure, start, end)
    return episode_context(router, exposure, start, end, time_ref, exposure_ref, step_limit)


def sample_at(router, node=1, visited=frozenset({1}), hops=3):
    return build_sample(router, EXPOSURE, context_for(router, EXPOSURE), node, visited, 0.0, 0.0, 0, hops, 32)


def write_fields(folder, router, test_file=False):
    """A fields directory. The test window is named but, by default, has no file at all."""
    folder = Path(folder)
    folder.mkdir(parents=True, exist_ok=True)
    entries = {}
    for window, split in ((TRAIN_WINDOW, "train"), (VAL_WINDOW, "val"), (TEST_WINDOW, "test")):
        name = window.replace(":", "") + ".csv"
        entries[window] = {"split": split, "file": name}
        if split != "test" or test_file:
            rows = "".join(f"{edge},{value}\n" for edge, value in EXPOSURE.items())
            (folder / name).write_text("edge_id,exposure_index\n" + rows, encoding="utf-8")
    manifest = {"fields_version": "test", "graph_version": router.version, "graph_files_sha256": {},
                "dataset": {"version": "test"}, "estimator": {"method": "test"},
                "window_splits": {"train": [TRAIN_WINDOW], "val": [VAL_WINDOW], "test": [TEST_WINDOW]},
                "windows": entries}
    (folder / "manifest.json").write_text(json.dumps(manifest), encoding="utf-8")
    return folder


def tiny_config(**schedule):
    return {"name": "tiny", "mode": "bike", "seed": 42, "threads": 1, "alpha": 0.5,
            "model": {"hidden_dim": 16, "graph_layers": 2, "ego_hops": 3, "ego_max_nodes": 32},
            "env": {"max_steps": 20, "max_steps_factor": 3.0, "failure_return": 4.0, "illegal_action_penalty": 0.1},
            "od": {"split_seed": 42, "seed": 42, "train_pairs": 1, "val_pairs": 1,
                   "min_distance_m": 0.0, "max_distance_m": 1e6},
            "ppo": {"actors": 4, "rollout_steps": 16, "learning_rate": 0.003, "gamma": 1.0, "gae_lambda": 0.95,
                    "clip": 0.2, "epochs": 4, "minibatch_size": 32, "value_coef": 0.5, "entropy_coef": 0.01,
                    "max_grad_norm": 0.5},
            "schedule": {"max_updates": 2, "eval_every": 1, "early_stop_patience": 100, "eval_batch": 4, **schedule}}


def tiny_set_config(alphas=(0.0, 1.0), **schedule):
    config = tiny_config(**schedule)
    del config["alpha"]
    config["policy_set"] = {"alphas": list(alphas), "evaluation_alphas": [0.0, 0.3, 0.5, 0.7, 1.0]}
    return config


OD_PAIRS = [ODPair("train-00000", 1, 4, "train"), ODPair("val-00000", 1, 4, "val")]


class FeatureTests(unittest.TestCase):
    def setUp(self):
        self.router = two_route_router()

    def test_root_is_first_and_actions_follow_the_router_order(self):
        sample = sample_at(self.router)
        self.assertEqual(sample.node_x[0, 4], 1)  # is_root
        arcs = self.router.arcs[1]
        self.assertEqual(len(sample.action_node), len(arcs))
        self.assertTrue(sample.action_mask.all())
        self.assertEqual(sample.edge_index.shape[0], 2)

    def test_visited_neighbours_are_masked(self):
        sample = sample_at(self.router, visited={1, 2})
        arcs = self.router.arcs[1]
        self.assertEqual(sample.action_mask.tolist(), [v != 2 for v, *_ in arcs])

    def test_exposure_is_visible_ahead_of_the_first_arc(self):
        """Edge 24 is two hops away, but appears in the ego-graph with its exposure."""
        one_hop = sample_at(self.router, hops=1)
        three_hops = sample_at(self.router, hops=3)
        self.assertLess(one_hop.edge_x.shape[0], three_hops.edge_x.shape[0])
        self.assertGreater(three_hops.edge_index.shape[1], 2)

    def test_same_state_gives_identical_arrays(self):
        a, b = sample_at(self.router), sample_at(self.router)
        for x, y in zip(a, b):
            np.testing.assert_array_equal(x, y)

    def test_every_action_stays_visible_at_the_node_cap(self):
        coords = {0: (0, 0), **{i: (0.001 * i, 0.001) for i in range(1, 41)}}
        edges = [(Edge(i, 0, i, 10, 10, f"LINESTRING(0 0,{0.001 * i} 0.001)"), False) for i in range(1, 41)]
        router = RoadRouter("v", "bike", coords, edges)
        exposure = {i: 1.0 for i in range(1, 41)}
        context = EpisodeContext(1, 10.0, 10.0, 100.0, 5, {0: 10.0, 1: 0.0}, {0: 10.0, 1: 0.0})
        with self.assertRaises(ValueError):
            build_sample(router, exposure, context, 0, {0}, 0, 0, 0, 2, 16)
        # 40 outgoing arcs exceed the 32-node cap, and all of them stay actions.
        sample = build_sample(router, exposure, context, 0, {0}, 0, 0, 0, 2, 32)
        self.assertEqual(len(sample.action_node), 40)
        self.assertEqual(len(set(sample.action_node.tolist())), 40)

    def test_potentials_are_the_least_cost_to_the_goal(self):
        context = context_for(self.router, EXPOSURE)
        self.assertEqual(dict(context.time_to_goal), {4: 0.0, 2: 10.0, 3: 15.0, 1: 20.0})
        self.assertEqual(dict(context.exposure_to_goal), {4: 0.0, 2: 40.0, 3: 15.0, 1: 30.0})

    def test_a_node_that_cannot_reach_the_goal_has_no_potential(self):
        self.assertNotIn(5, cost_to_go(self.router, 4, 1, lambda seconds, edge_id: seconds))

    def test_regret_is_zero_on_the_optimal_arc_of_each_objective(self):
        sample = sample_at(self.router)
        by_edge = {edge_id: sample.action_edge_x[i] for i, (_, _, edge_id, _) in enumerate(self.router.arcs[1])}
        time_regret, exposure_regret = EDGE_FEATURES - 2, EDGE_FEATURES - 1
        self.assertEqual(by_edge[12][time_regret], 0)       # 1-2-4 is the fastest route
        self.assertGreater(by_edge[13][time_regret], 0)
        self.assertEqual(by_edge[13][exposure_regret], 0)   # 1-3-4 is the cleanest route
        self.assertGreater(by_edge[12][exposure_regret], 0)

    def test_the_horizon_bounds_the_search(self):
        coords = {i: (0.001 * i, 0) for i in range(100)}
        edges = [(Edge(i, i, i + 1, 10, 10, f"LINESTRING({0.001 * i} 0,{0.001 * (i + 1)} 0)"), True) for i in range(99)]
        router = RoadRouter("v", "bike", coords, edges)
        settled = cost_to_go(router, 50, 45, lambda seconds, edge_id: seconds)
        self.assertEqual(settled[45], 50)
        self.assertLessEqual(max(settled.values()), 100)  # twice the origin's own cost
        self.assertLess(len(settled), 100)

    def test_collate_offsets_edges_and_actions_per_sample(self):
        a, b = sample_at(self.router), sample_at(self.router, node=2, visited={1, 2})
        batch = collate([a, b])
        self.assertEqual(batch["roots"].tolist(), [0, len(a.node_x)])
        self.assertEqual(batch["action_batch"].tolist(), [0] * len(a.action_node) + [1] * len(b.action_node))
        self.assertGreaterEqual(batch["action_node"][len(a.action_node):].min(), len(a.node_x))
        self.assertLess(batch["edge_index"].max(), len(a.node_x) + len(b.node_x))


class ModelTests(unittest.TestCase):
    def setUp(self):
        torch.manual_seed(0)
        self.router = two_route_router()
        self.model = ActorCritic(SMALL).eval()

    def batch(self, *samples):
        return to_tensors(collate(list(samples)))

    def test_probabilities_sum_to_one_per_sample_and_masked_actions_are_impossible(self):
        batch = self.batch(sample_at(self.router), sample_at(self.router, visited={1, 2}))
        log_probs, values = self.model(batch)
        for i in range(2):
            self.assertAlmostEqual(log_probs[batch["action_batch"] == i].exp().sum().item(), 1.0, places=5)
        self.assertTrue(torch.isinf(log_probs[~batch["action_mask"]]).all())
        self.assertEqual(values.shape, (2,))

    def test_sampling_and_greedy_only_pick_legal_actions(self):
        sample = sample_at(self.router, visited={1, 2})
        legal = sample.action_mask.nonzero()[0].tolist()
        batch = self.batch(sample)
        log_probs, _ = self.model(batch)
        for _ in range(50):
            action = sample_actions(log_probs, batch["action_batch"], 1)[0].item()
            self.assertIn(action, legal)
        self.assertIn(sample_actions(log_probs, batch["action_batch"], 1, greedy=True)[0].item(), legal)

    def test_entropy_ignores_masked_actions(self):
        batch = self.batch(sample_at(self.router, visited={1, 2}))
        log_probs, _ = self.model(batch)
        self.assertAlmostEqual(entropy(log_probs, batch["action_batch"], 1).item(), 0.0, places=5)

    def test_gradients_stay_finite_with_masked_actions(self):
        batch = self.batch(sample_at(self.router, visited={1, 2}))
        log_probs, values = self.model(batch)
        (entropy(log_probs, batch["action_batch"], 1).sum() + values.sum()).backward()
        for parameter in self.model.parameters():
            if parameter.grad is not None:
                self.assertTrue(torch.isfinite(parameter.grad).all())


class CheckpointTests(unittest.TestCase):
    """AC: a checkpoint reloads and gives the same inference on the same input."""

    def setUp(self):
        torch.manual_seed(1)
        self.router = two_route_router()
        self.model = ActorCritic(SMALL).eval()
        self.folder = tempfile.TemporaryDirectory()
        self.addCleanup(self.folder.cleanup)
        self.path = Path(self.folder.name) / "checkpoints" / "best.pt"

    def test_reload_gives_identical_outputs(self):
        save_checkpoint(self.path, self.model, run_id="r", update=3)
        loaded, checkpoint = load_policy(self.path)
        self.assertEqual((checkpoint["run_id"], checkpoint["update"]), ("r", 3))
        self.assertEqual(loaded.config, SMALL)
        batch = to_tensors(collate([sample_at(self.router)]))
        with torch.no_grad():
            before, after = self.model(batch), loaded(batch)
        for x, y in zip(before, after):
            self.assertTrue(torch.equal(x, y))

    def test_reload_keeps_the_route(self):
        save_checkpoint(self.path, self.model)
        loaded, _ = load_policy(self.path)
        def route(model):
            try:
                return decode_route(model, self.router, EXPOSURE, 1, 4)
            except PolicyFailure as failure:
                return failure.reason
        self.assertEqual(route(self.model), route(loaded))

    def test_other_formats_and_feature_versions_are_refused(self):
        save_checkpoint(self.path, self.model)
        checkpoint = torch.load(self.path, weights_only=True)
        for key, value in (("format", "other"), ("feature_version", "ego-0")):
            torch.save({**checkpoint, key: value}, self.path)
            with self.assertRaises(ValueError):
                load_policy(self.path)

    def test_a_pickled_object_in_a_checkpoint_is_refused(self):
        torch.save({"format": "morl-checkpoint-1", "payload": Path("x")}, self.path.parent.mkdir(parents=True) or self.path)
        with self.assertRaises(Exception):
            load_policy(self.path)

    def test_no_partial_file_is_left_behind(self):
        save_checkpoint(self.path, self.model)
        self.assertEqual([p.name for p in self.path.parent.iterdir()], ["best.pt"])


class FixedActionPolicy(ActorCritic):
    """Prefers the arc with the largest (or smallest) exposure feature: a known behaviour for decode tests."""

    def __init__(self, sign=1.0):
        super().__init__(SMALL)
        self.sign = sign

    def forward(self, batch):
        log_probs, values = super().forward(batch)
        scores = self.sign * batch["action_edge_x"][:, 1]
        scores = scores.masked_fill(~batch["action_mask"], float("-inf"))
        return torch.log_softmax(scores, dim=0), values


class InferenceTests(unittest.TestCase):
    """UT-MRM-07 with a small network of fixed weights."""

    def setUp(self):
        torch.manual_seed(2)
        self.router = two_route_router()

    def test_route_runs_from_origin_to_destination_on_legal_arcs(self):
        route = decode_route(FixedActionPolicy(-1.0), self.router, EXPOSURE, 1, 4)
        self.assertEqual(route.edge_ids, (13, 34))
        self.assertEqual((route.nodes[0], route.nodes[-1]), (1, 4))
        self.assertEqual(route.time_s, 30)
        self.assertEqual(route.exposure, 30)

    def test_a_dead_end_is_a_failure_not_a_route(self):
        # At node 2 the highest-exposure arc is 24, so force the dead end through exposure values.
        exposure = {**EXPOSURE, 25: 9.0}
        with self.assertRaises(PolicyFailure) as caught:
            decode_route(FixedActionPolicy(1.0), self.router, exposure, 1, 4)
        self.assertEqual(caught.exception.reason, "dead_end")

    def test_timeout_is_a_failure(self):
        with self.assertRaises(PolicyFailure) as caught:
            decode_route(ActorCritic(SMALL).eval(), self.router, EXPOSURE, 1, 4, timeout_ms=-1)
        self.assertEqual(caught.exception.reason, "timeout")

    def test_step_limit_is_a_failure(self):
        with self.assertRaises(PolicyFailure) as caught:
            decode_route(FixedActionPolicy(-1.0), self.router, EXPOSURE, 1, 4, step_limit=1)
        self.assertEqual(caught.exception.reason, "step_limit")

    def test_same_input_gives_the_same_route(self):
        model = FixedActionPolicy(-1.0).eval()
        first = decode_route(model, self.router, EXPOSURE, 1, 4)
        torch.rand(100)  # any random state: inference must not draw from it
        self.assertEqual(first, decode_route(model, self.router, EXPOSURE, 1, 4))

    def test_unreachable_and_same_node_are_failures(self):
        for start, end, reason in ((4, 1, "no_route"), (1, 1, "same_node")):
            with self.assertRaises(PolicyFailure) as caught:
                decode_route(ActorCritic(SMALL).eval(), self.router, EXPOSURE, start, end)
            self.assertEqual(caught.exception.reason, reason)

    def test_decoding_matches_a_greedy_walk_of_the_training_environment(self):
        """Inference has no environment, so its features must equal the environment's."""
        with tempfile.TemporaryDirectory() as folder:
            fields = write_fields(folder, self.router)
            envs, _ = build_envs(self.router, fields, [OD_PAIRS[0]], tiny_config()["env"], "train", 0)
        model = FixedActionPolicy(-1.0).eval()
        actor = Actor(envs, 0, SMALL, 0.5)
        actor.reset(od_id="train-00000")
        edges = []
        while True:
            batch = to_tensors(collate([actor.sample()]))
            with torch.no_grad():
                log_probs, _ = model(batch)
            action = sample_actions(log_probs, batch["action_batch"], 1, greedy=True)[0].item()
            edges.append(self.router.arcs[actor.env.node][action][2])
            *_, info = actor.env.step(action)
            if info["outcome"]:
                break
        self.assertEqual(tuple(edges), decode_route(model, self.router, EXPOSURE, 1, 4).edge_ids)

    def test_optimal_weighted_route_follows_the_preference(self):
        reference = (20.0, 80.0)
        self.assertEqual(optimal_weighted_route(self.router, EXPOSURE, 1, 4, 1.0, 0.0, *reference)[0], [12, 24])
        edges, cost = optimal_weighted_route(self.router, EXPOSURE, 1, 4, 0.2, 0.8, *reference)
        self.assertEqual(edges, [13, 34])
        self.assertAlmostEqual(cost, 0.6)  # the hand-calculated value of TRAINING_ENV.md
        self.assertEqual(optimal_weighted_route(self.router, EXPOSURE, 4, 1, 0.5, 0.5, *reference), (None, None))


class RunRecordTests(unittest.TestCase):
    """AC: identity, configuration and data references; failures recorded with their reason."""

    def setUp(self):
        self.router = two_route_router()
        self.folder = tempfile.TemporaryDirectory()
        self.addCleanup(self.folder.cleanup)
        self.fields = write_fields(Path(self.folder.name) / "fields", self.router)
        self.out = Path(self.folder.name) / "runs"

    def train(self, config=None):
        return train(config or tiny_config(), self.fields, self.out, router=self.router, od_pairs=OD_PAIRS)

    def manifest(self, run_dir=None):
        run_dir = run_dir or next(self.out.iterdir())
        return json.loads((Path(run_dir) / "manifest.json").read_text(encoding="utf-8"))

    def test_completed_run_identifies_itself_and_its_data(self):
        run = self.train()
        manifest = self.manifest(run.dir)
        self.assertEqual(manifest["status"], "completed")
        self.assertEqual(manifest["experiment"], "morl-route-policy")
        self.assertRegex(manifest["run_id"], r"^\d{8}T\d{6}Z-tiny-[0-9a-f]{8}$")
        self.assertEqual(len(manifest["config_sha256"]), 64)
        self.assertEqual(manifest["config"]["seed"], 42)
        self.assertEqual(manifest["data"]["graph_version"], "test-v1")
        self.assertEqual(manifest["data"]["train"]["windows"], [TRAIN_WINDOW])
        self.assertEqual(manifest["data"]["val"]["windows"], [VAL_WINDOW])
        self.assertEqual(len(manifest["data"]["train"]["window_sha256"][TRAIN_WINDOW]), 64)
        self.assertEqual(len(manifest["data"]["od_pairs_sha256"]), 64)
        self.assertIn("torch", manifest["versions"])
        self.assertIn("commit", manifest["code"])
        self.assertEqual(manifest["progress"]["updates"], 2)
        for artifact in ("config.json", "od_pairs.json", "metrics.jsonl", "curves.csv", "checkpoints/best.pt"):
            self.assertIn(artifact, manifest["artifacts"])
            self.assertTrue((run.dir / artifact).exists())

    def test_best_checkpoint_loads_and_names_its_run(self):
        run = self.train()
        model, checkpoint = load_policy(run.dir / "checkpoints" / "best.pt")
        self.assertEqual(checkpoint["run_id"], run.run_id)
        self.assertEqual(checkpoint["update"], self.manifest(run.dir)["best"]["update"])
        self.assertEqual(model.config, SMALL)

    def test_final_evaluation_reloads_the_checkpoint_and_repeats_its_routes(self):
        run = self.train()
        evaluation = json.loads((run.dir / "evaluation.json").read_text(encoding="utf-8"))
        manifest = self.manifest(run.dir)
        self.assertEqual(evaluation["split"], "val")
        self.assertEqual(evaluation["checkpoint"]["sha256"], manifest["best"]["sha256"])
        self.assertTrue(evaluation["reload_check"]["identical"])
        self.assertEqual(evaluation["reload_check"]["od_pairs"], 1)
        self.assertEqual(manifest["evaluation"]["success_rate"], evaluation["summary"]["success_rate"])
        self.assertIn("evaluation.json", manifest["artifacts"])

    def test_selection_uses_validation_and_never_reads_test_data(self):
        # write_fields names a test window whose file does not exist: reading it would fail the run.
        self.assertFalse((self.fields / (TEST_WINDOW.replace(":", "") + ".csv")).exists())
        run = self.train()
        manifest = self.manifest(run.dir)
        self.assertEqual(manifest["checkpoint_selection"]["split"], "val")
        self.assertFalse(manifest["checkpoint_selection"]["test_split_loaded"])
        pairs = json.loads((run.dir / "od_pairs.json").read_text(encoding="utf-8"))["pairs"]
        self.assertEqual({p["split"] for p in pairs}, {"train", "val"})
        records = [json.loads(line) for line in (run.dir / "metrics.jsonl").read_text(encoding="utf-8").splitlines()]
        best = max((r for r in records if r["phase"] == "val"), key=lambda r: r["mean_return"])
        self.assertAlmostEqual(manifest["best"]["val_mean_return"], best["mean_return"])

    def test_a_test_od_pair_or_window_cannot_enter_training(self):
        test_pair = ODPair("test-00000", 1, 4, "test")
        with self.assertRaises(ValueError):
            build_envs(self.router, self.fields, [test_pair], tiny_config()["env"], "train", 0)
        with self.assertRaises(FileNotFoundError):  # its data would have to be read first
            build_envs(self.router, self.fields, [test_pair], tiny_config()["env"], "test", 0)

    def test_early_stop_is_recorded_with_its_reason(self):
        run = self.train(tiny_config(max_updates=50, early_stop_patience=1))
        manifest = self.manifest(run.dir)
        self.assertEqual(manifest["status"], "early_stopped")
        self.assertIn("No validation improvement", manifest["stop_reason"])
        self.assertLess(manifest["progress"]["updates"], 50)
        self.assertTrue((run.dir / "checkpoints" / "best.pt").exists())

    def test_divergence_is_recorded_and_artifacts_stay(self):
        with mock.patch.object(train_module, "ppo_update", side_effect=TrainingDiverged("Non-finite loss")):
            with self.assertRaises(TrainingDiverged):
                self.train()
        manifest = self.manifest()
        self.assertEqual(manifest["status"], "failed")
        self.assertIn("Non-finite loss", manifest["stop_reason"])
        self.assertIn("checkpoints/best.pt", manifest["artifacts"])  # from the evaluation before training
        self.assertIsNotNone(manifest["finished_at"])

    def test_a_crash_is_recorded_with_its_traceback(self):
        with mock.patch.object(train_module, "collect_rollout", side_effect=OSError("disk full")):
            with self.assertRaises(OSError):
                self.train()
        manifest = self.manifest()
        self.assertEqual(manifest["status"], "failed")
        self.assertIn("disk full", manifest["stop_reason"])
        self.assertIn("OSError", manifest["traceback"])

    def test_an_interruption_is_recorded_and_not_an_error(self):
        with mock.patch.object(train_module, "collect_rollout", side_effect=KeyboardInterrupt()):
            run = self.train()
        self.assertEqual(self.manifest(run.dir)["status"], "interrupted")

    def test_a_scheduler_termination_is_recorded(self):
        with mock.patch.object(train_module, "collect_rollout", side_effect=train_module._Terminated("signal 15")):
            run = self.train()
        manifest = self.manifest(run.dir)
        self.assertEqual(manifest["status"], "interrupted")
        self.assertIn("signal 15", manifest["stop_reason"])

    def test_time_budget_stop_is_recorded(self):
        run = train(tiny_config(max_updates=50), self.fields, self.out, router=self.router, od_pairs=OD_PAIRS,
                    max_seconds=0)
        manifest = self.manifest(run.dir)
        self.assertEqual(manifest["status"], "stopped")
        self.assertIn("Time budget", manifest["stop_reason"])

    def test_same_seed_gives_the_same_training(self):
        first, second = self.train(), self.train()
        read = lambda run: [
            {k: r[k] for k in ("update", "mean_return", "success_rate")}
            for r in map(json.loads, (run.dir / "metrics.jsonl").read_text(encoding="utf-8").splitlines())
            if r["phase"] == "val"]
        self.assertEqual(read(first), read(second))
        a = torch.load(first.dir / "checkpoints" / "best.pt", weights_only=True)["model_state"]
        b = torch.load(second.dir / "checkpoints" / "best.pt", weights_only=True)["model_state"]
        self.assertTrue(all(torch.equal(a[k], b[k]) for k in a))


    def test_a_memory_limit_stops_the_run_with_its_reason_and_keeps_checkpoints(self):
        guard = ResourceGuard(ResourceLimits(max_rss_gb=1.0), self.out, rss=lambda: 2 * GB, disk=lambda: 100 * GB)
        run = train(tiny_config(max_updates=50), self.fields, self.out, router=self.router, od_pairs=OD_PAIRS, guard=guard)
        manifest = self.manifest(run.dir)
        self.assertEqual(manifest["status"], "stopped")
        self.assertIn("Memory limit", manifest["stop_reason"])
        self.assertEqual(manifest["progress"]["updates"], 1)
        for name in ("best.pt", "last.pt"):
            self.assertTrue((run.dir / "checkpoints" / name).exists())

    def test_memory_use_is_logged_per_update(self):
        run = self.train()
        rows = [json.loads(line) for line in (run.dir / "metrics.jsonl").read_text(encoding="utf-8").splitlines()]
        self.assertTrue(all(r["rss_mb"] > 0 for r in rows if r["phase"] == "train"))
        self.assertIn("rss_mb", (run.dir / "curves.csv").read_text(encoding="utf-8").splitlines()[0])

    def test_only_the_newest_periodic_checkpoints_stay(self):
        config = tiny_config(max_updates=6)
        config["resources"] = {"keep_checkpoints": 2}
        run = self.train(config)
        names = sorted(p.name for p in (run.dir / "checkpoints").iterdir())
        self.assertEqual(names, ["best.pt", "last.pt", "update_00005.pt", "update_00006.pt"])

    def test_an_interrupted_run_saves_its_state_and_resumes(self):
        config = tiny_config(max_updates=3, eval_every=100)
        real, calls = train_module.collect_rollout, []

        def interrupt_on_second(*args, **kwargs):
            calls.append(1)
            if len(calls) == 2:
                raise KeyboardInterrupt()
            return real(*args, **kwargs)

        with mock.patch.object(train_module, "collect_rollout", side_effect=interrupt_on_second):
            run = self.train(config)
        manifest = self.manifest(run.dir)
        self.assertEqual(manifest["status"], "interrupted")
        saved = torch.load(run.dir / "checkpoints" / "last.pt", weights_only=True)
        self.assertEqual(saved["progress"]["update"], 1)

        resumed = train(config, self.fields, router=self.router, resume=run.dir)
        self.assertEqual(resumed.dir, run.dir)
        manifest = self.manifest(run.dir)
        self.assertEqual(manifest["status"], "completed")
        self.assertEqual(manifest["progress"]["updates"], 3)
        self.assertEqual(manifest["resumes"][0]["previous_status"], "interrupted")
        self.assertEqual(len(manifest["resumes"]), 1)
        rows = (run.dir / "curves.csv").read_text(encoding="utf-8").splitlines()[1:]
        self.assertEqual([row.split(",")[0] for row in rows], ["1", "2", "3"])
        # Same OD pairs: a resume never resamples.
        self.assertEqual(manifest["data"]["od_pairs_sha256"], train_module.file_sha256(run.dir / "od_pairs.json"))

    def test_resume_refuses_another_configuration_or_a_missing_state(self):
        config = tiny_config()
        run = self.train(config)
        with self.assertRaises(ValueError):
            train(tiny_config(max_updates=9), self.fields, router=self.router, resume=run.dir)
        (run.dir / "checkpoints" / "last.pt").unlink()
        with self.assertRaises(ValueError):
            train(config, self.fields, router=self.router, resume=run.dir)


class AugmentationTests(unittest.TestCase):
    def setUp(self):
        self.router = two_route_router()
        self.augmentation = Augmentation(probability=1.0, max_log_std=0.5)

    def test_the_field_is_positive_smooth_and_reproducible(self):
        field = SmoothField(self.augmentation, random.Random(1))
        other = SmoothField(self.augmentation, random.Random(1))
        self.assertEqual(field.factor(106.8, -6.2), other.factor(106.8, -6.2))
        self.assertGreater(field.factor(106.8, -6.2), 0)
        # 10 m apart barely differs; the pattern's scale is at least a kilometre.
        self.assertAlmostEqual(field.factor(106.8, -6.2), field.factor(106.8001, -6.2), delta=0.05)

    def test_perturbation_scales_the_real_value_and_never_changes_the_table(self):
        field = SmoothField(self.augmentation, random.Random(2))
        table = dict(EXPOSURE)
        perturbed = PerturbedExposure(table, self.router, field)
        (x1, y1), (x2, y2) = self.router.coords[1], self.router.coords[2]
        self.assertAlmostEqual(perturbed[12], 4.0 * field.factor((x1 + x2) / 2, (y1 + y2) / 2))
        self.assertEqual(table, EXPOSURE)

    def test_invalid_settings_are_refused(self):
        for settings in ({"probability": 2.0}, {"max_log_std": -1.0}, {"components": 0}, {"min_wavelength_m": 0.0},
                         {"min_wavelength_m": 5.0, "max_wavelength_m": 1.0}):
            with self.assertRaises(ValueError):
                Augmentation(**settings)

    def test_an_actor_perturbs_episodes_but_the_real_field_survives(self):
        with tempfile.TemporaryDirectory() as folder:
            fields = write_fields(folder, self.router)
            envs, _ = build_envs(self.router, fields, [OD_PAIRS[0]], tiny_config()["env"], "train", 0)
        actor = Actor(envs, 0, SMALL, 0.5, self.augmentation)
        self.assertIsInstance(actor.env.exposure, PerturbedExposure)
        self.assertEqual(envs[0].exposure, EXPOSURE)   # the shared original is untouched
        plain = Actor(envs, 0, SMALL, 0.5)
        self.assertEqual(plain.env.exposure, EXPOSURE)
        actor.augmentation = None
        actor.reset()
        self.assertEqual(actor.env.exposure, EXPOSURE)

    def test_selection_ignores_perturbed_validation(self):
        with tempfile.TemporaryDirectory() as folder:
            fields = write_fields(Path(folder) / "fields", self.router)
            config = tiny_config()
            config["augmentation"] = {"probability": 1.0, "max_log_std": 0.5}
            run = train(config, fields, Path(folder) / "runs", router=self.router, od_pairs=OD_PAIRS)
            manifest = json.loads((run.dir / "manifest.json").read_text(encoding="utf-8"))
            rows = [json.loads(line) for line in (run.dir / "metrics.jsonl").read_text(encoding="utf-8").splitlines()]
            evaluation = json.loads((run.dir / "evaluation.json").read_text(encoding="utf-8"))
        real = {r["update"]: r["mean_return"] for r in rows if r["phase"] == "val"}
        self.assertTrue(any(r["phase"] == "val_perturbed" for r in rows))
        self.assertEqual(manifest["best"]["val_mean_return"], max(real.values()))
        self.assertTrue(manifest["data"]["train"]["augmentation"]["synthetic"])
        self.assertIn("Synthetic", evaluation["perturbed_fields"]["note"])
        self.assertIn("oracle_exposure_ratio", evaluation["perturbed_fields"]["summary"]["by_alpha"]["0.5"])


class ResourceGuardTests(unittest.TestCase):
    def guard(self, values, free=100 * GB, **limits):
        series = iter(values)
        return ResourceGuard(ResourceLimits(**limits), ".", rss=lambda: next(series), disk=lambda: free)

    def test_within_limits_nothing_stops(self):
        guard = self.guard([1 * GB] * 30, growth_window_updates=10)
        for _ in range(30):
            usage, reason = guard.check()
            self.assertIsNone(reason)
        self.assertEqual(usage["rss_mb"], 1024.0)

    def test_memory_and_disk_limits_stop_with_a_reason(self):
        self.assertIn("Memory limit", self.guard([17 * GB]).check()[1])
        self.assertIn("Disk limit", self.guard([1 * GB], free=1 * GB).check()[1])

    def test_sustained_growth_stops_before_the_absolute_limit(self):
        guard = self.guard([1 * GB + i * 100 * MB for i in range(20)], growth_window_updates=10, growth_limit_mb=500)
        reasons = [guard.check()[1] for _ in range(20)]
        stops = [r for r in reasons if r]
        self.assertTrue(stops and "Memory growth" in stops[0])

    def test_a_temporary_peak_that_comes_back_is_not_growth(self):
        """Measured on the M4: RSS rose about 1 GB for 25 updates, then fell back to where it was."""
        values = [int(1.7 * GB)] * 60 + [int(2.5 * GB)] * 25 + [int(1.7 * GB)] * 60
        guard = self.guard(values, growth_window_updates=90, growth_limit_mb=512)
        self.assertEqual([guard.check()[1] for _ in values], [None] * len(values))

    def test_a_slow_leak_under_a_noisy_signal_is_caught(self):
        wave = [0, 300, 600, 900, 600, 300]
        values = [1 * GB + i * 10 * MB + wave[i % 6] * MB for i in range(150)]
        guard = self.guard(values, growth_window_updates=90, growth_limit_mb=512)
        stops = [r for r in (guard.check()[1] for _ in values) if r]
        self.assertTrue(stops and "floor rose" in stops[0])

    def test_a_plateau_after_startup_is_not_growth(self):
        values = [1 * GB + min(i, 5) * 200 * MB for i in range(60)]
        guard = self.guard(values, growth_window_updates=30, growth_limit_mb=500)
        reasons = [guard.check()[1] for _ in range(60)]
        self.assertEqual(reasons, [None] * 60)

    def test_only_a_bounded_window_of_samples_is_kept(self):
        guard = self.guard([1 * GB] * 100, growth_window_updates=10)
        for _ in range(100):
            guard.check()
        self.assertEqual(len(guard._samples), 10)

    def test_threads_leave_cores_for_the_machine(self):
        with mock.patch("os.cpu_count", return_value=10):
            self.assertEqual(thread_limit(64), 8)
            self.assertEqual(thread_limit(4), 4)
        with mock.patch("os.cpu_count", return_value=2):
            self.assertEqual(thread_limit(4), 1)

    def test_current_rss_is_a_plausible_size(self):
        self.assertGreater(current_rss_bytes(), 10 * MB)

    def test_pruning_never_touches_best_or_last(self):
        with tempfile.TemporaryDirectory() as folder:
            for name in ("best.pt", "last.pt", "update_00001.pt", "update_00002.pt", "update_00003.pt"):
                (Path(folder) / name).touch()
            prune_checkpoints(folder, 1)
            self.assertEqual(sorted(p.name for p in Path(folder).iterdir()), ["best.pt", "last.pt", "update_00003.pt"])

    def test_invalid_limits_are_refused(self):
        with self.assertRaises(ValueError):
            ResourceLimits(growth_window_updates=2)
        with self.assertRaises(ValueError):
            ResourceLimits(keep_checkpoints=0)


class CriticWarmupTests(unittest.TestCase):
    def test_during_the_critic_warmup_the_actor_does_not_move(self):
        router = two_route_router()
        with tempfile.TemporaryDirectory() as folder:
            fields = write_fields(Path(folder) / "fields", router)
            config = tiny_config(max_updates=3, eval_every=1)
            config["ppo"]["critic_warmup_updates"] = 3
            first = train(config, fields, Path(folder) / "a", router=router, od_pairs=OD_PAIRS)
            checkpoints = first.dir / "checkpoints"
            start = torch.load(checkpoints / "best.pt", weights_only=True)["model_state"]
            end = torch.load(checkpoints / "update_00003.pt", weights_only=True)["model_state"]
        # Everything the actor's decision depends on stands still, the encoders included.
        fixed = [k for k in start if not k.startswith("critic.")]
        self.assertTrue(any(k.startswith("graph_encoder.") for k in fixed))
        self.assertTrue(all(torch.equal(start[k], end[k]) for k in fixed))
        self.assertTrue(any(not torch.equal(start[k], end[k]) for k in start if k.startswith("critic.")))


class NearestAlphaTests(unittest.TestCase):
    def test_a_preference_goes_to_the_nearest_member(self):
        members = [0.0, 0.25, 0.5, 0.75, 1.0]
        for alpha, expected in ((0.0, 0.0), (0.1, 0.0), (0.2, 0.25), (0.6, 0.5), (0.7, 0.75), (1.0, 1.0)):
            self.assertEqual(nearest_alpha(members, alpha), expected)

    def test_a_tie_goes_to_the_lower_alpha(self):
        self.assertEqual(nearest_alpha([0.0, 0.25, 0.5], 0.125), 0.0)
        self.assertEqual(nearest_alpha([0.5, 0.25], 0.375), 0.25)


class PolicySetTests(unittest.TestCase):
    """The set: one policy per preference, the same data, and one record of it all."""

    def setUp(self):
        self.router = two_route_router()
        self.folder = tempfile.TemporaryDirectory()
        self.addCleanup(self.folder.cleanup)
        self.fields = write_fields(Path(self.folder.name) / "fields", self.router)
        self.out = Path(self.folder.name) / "sets"

    def train_set(self, config=None, **kwargs):
        return train_set(config or tiny_set_config(), self.fields, self.out, router=self.router, od_pairs=OD_PAIRS,
                         **kwargs)

    @staticmethod
    def manifest(run):
        return json.loads((run.dir / "set_manifest.json").read_text(encoding="utf-8"))

    def test_member_configuration_differs_only_in_preference_name_and_seed(self):
        config = tiny_set_config()
        first, second = member_config(config, 0.0, 0), member_config(config, 1.0, 1)
        self.assertEqual((first["alpha"], second["alpha"]), (0.0, 1.0))
        self.assertEqual((first["seed"], second["seed"]), (42, 43))
        self.assertNotIn("policy_set", first)
        for key in ("model", "env", "od", "ppo", "schedule"):
            self.assertEqual(first[key], second[key])

    def test_the_set_trains_one_member_per_alpha_on_the_same_data(self):
        run = self.train_set()
        manifest = self.manifest(run)
        self.assertEqual(manifest["status"], "completed")
        self.assertEqual(manifest["approach"], "multi-policy")
        self.assertEqual([m["alpha"] for m in manifest["members"]], [0.0, 1.0])
        self.assertEqual({m["status"] for m in manifest["members"]}, {"completed"})
        members = [json.loads((run.dir / m["run_dir"] / "manifest.json").read_text(encoding="utf-8"))
                   for m in manifest["members"]]
        self.assertEqual([m["alpha"] for m in members], [0.0, 1.0])
        self.assertEqual(len({m["data"]["od_pairs_sha256"] for m in members}), 1)
        self.assertEqual(members[0]["data"]["train"]["window_sha256"], members[1]["data"]["train"]["window_sha256"])
        self.assertFalse(manifest["data"]["test_split_loaded"])

    def test_every_episode_of_a_member_uses_its_own_preference(self):
        run = self.train_set()
        for member in self.manifest(run)["members"]:
            evaluation = json.loads((run.dir / member["run_dir"] / "evaluation.json").read_text(encoding="utf-8"))
            self.assertEqual(list(evaluation["summary"]["by_alpha"]), [f"{member['alpha']:g}"])

    def test_the_set_reloads_and_repeats_its_routes(self):
        run = self.train_set()
        evaluation = json.loads((run.dir / "evaluation.json").read_text(encoding="utf-8"))
        self.assertTrue(evaluation["reload_check"]["identical"])
        self.assertEqual(evaluation["reload_check"]["routes"], 2)  # one validation pair x two members
        self.assertEqual(evaluation["served_by"], {"0": [0.0, 0.3, 0.5], "1": [0.7, 1.0]})  # 0.5 is a tie
        self.assertEqual(sorted(a for served in evaluation["served_by"].values() for a in served),
                         [0.0, 0.3, 0.5, 0.7, 1.0])
        self.assertEqual(evaluation["split"], "val")
        self.assertEqual(sorted(load_policy_set(run.dir)), [0.0, 1.0])

    def test_a_request_is_served_by_the_nearest_member(self):
        policies = {0.0: FixedActionPolicy(-1.0).eval(), 1.0: FixedActionPolicy(-1.0).eval()}
        member, route = decode_for_preference(policies, self.router, EXPOSURE, 1, 4, (0.2, 0.8))
        self.assertEqual(member, 0.0)
        self.assertEqual(route.edge_ids, (13, 34))
        self.assertEqual(decode_for_preference(policies, self.router, EXPOSURE, 1, 4, (0.9, 0.1))[0], 1.0)

    def test_an_incomplete_set_cannot_be_loaded(self):
        with mock.patch.object(multi_policy_module, "train",
                               side_effect=train_module.TrainingDiverged("Non-finite loss")):
            with self.assertRaises(TrainingDiverged):
                self.train_set()
        run_dir = next(self.out.iterdir())
        with self.assertRaises(ValueError):
            load_policy_set(run_dir)

    def test_a_failed_member_is_recorded_and_stops_the_set(self):
        with mock.patch.object(train_module, "ppo_update", side_effect=TrainingDiverged("Non-finite loss")):
            with self.assertRaises(TrainingDiverged):
                self.train_set()
        run_dir = next(self.out.iterdir())
        manifest = json.loads((run_dir / "set_manifest.json").read_text(encoding="utf-8"))
        self.assertEqual(manifest["status"], "failed")
        self.assertIn("Non-finite loss", manifest["stop_reason"])
        self.assertEqual(manifest["members"][0]["status"], "failed")
        self.assertEqual(manifest["members"][1]["status"], "pending")
        self.assertIn("traceback", manifest)

    def test_a_stopped_member_stops_the_set_with_its_reason(self):
        guards = lambda alpha: ResourceGuard(ResourceLimits(max_rss_gb=1.0), self.out, rss=lambda: 2 * GB,
                                             disk=lambda: 100 * GB)
        run = self.train_set(tiny_set_config(max_updates=50), guard_factory=guards)
        manifest = self.manifest(run)
        self.assertEqual(manifest["status"], "stopped")
        self.assertIn("alpha=0", manifest["stop_reason"])
        self.assertIn("Memory limit", manifest["stop_reason"])
        self.assertEqual(manifest["members"][1]["status"], "pending")

    def test_an_interrupted_set_resumes_and_keeps_finished_members(self):
        config = tiny_set_config(max_updates=3, eval_every=100)
        real, calls = train_module.collect_rollout, []

        def interrupt_during_second_member(*args, **kwargs):
            calls.append(1)
            if len(calls) == 5:  # member one takes three rollouts, this is the second of member two
                raise KeyboardInterrupt()
            return real(*args, **kwargs)

        with mock.patch.object(train_module, "collect_rollout", side_effect=interrupt_during_second_member):
            run = self.train_set(config)
        manifest = self.manifest(run)
        self.assertEqual(manifest["status"], "interrupted")
        self.assertEqual([m["status"] for m in manifest["members"]], ["completed", "interrupted"])
        finished_run = manifest["members"][0]["run_id"]
        finished_checkpoint = manifest["members"][0]["best"]["sha256"]

        resumed = train_set(config, self.fields, resume=run.dir, router=self.router)
        manifest = self.manifest(resumed)
        self.assertEqual(resumed.dir, run.dir)
        self.assertEqual(manifest["status"], "completed")
        self.assertEqual(manifest["resumes"][0]["previous_status"], "interrupted")
        self.assertEqual(manifest["members"][0]["run_id"], finished_run)
        self.assertEqual(manifest["members"][0]["best"]["sha256"], finished_checkpoint)
        self.assertEqual(manifest["members"][1]["progress"]["updates"], 3)
        self.assertTrue((run.dir / "evaluation.json").exists())

    def test_resume_refuses_another_configuration(self):
        run = self.train_set()
        with self.assertRaises(ValueError):
            train_set(tiny_set_config(max_updates=9), self.fields, resume=run.dir, router=self.router)

    def test_invalid_alphas_are_refused(self):
        for alphas in ((), (0.5, 0.5), (0.5, 1.5)):
            with self.assertRaises(ValueError):
                self.train_set(tiny_set_config(alphas=alphas))

    def test_the_members_learn_different_routes_for_different_preferences(self):
        """Design doc: a short polluted route and a long clean one, with a hand-known optimum per preference."""
        config = tiny_set_config(max_updates=100, eval_every=20)
        config["ppo"].update(actors=8, rollout_steps=16)
        run = self.train_set(config)
        policies = load_policy_set(run.dir)
        self.assertEqual(decode_route(policies[1.0], self.router, EXPOSURE, 1, 4).edge_ids, (12, 24))
        self.assertEqual(decode_route(policies[0.0], self.router, EXPOSURE, 1, 4).edge_ids, (13, 34))


class FieldTests(unittest.TestCase):
    def test_windows_stay_in_their_block_and_skip_unlabelled_hours(self):
        spec = {"blocks": {"train": {"first_window": "2026-09-13T00:00:00Z", "end_window_exclusive": "2026-09-14T00:00:00Z"},
                           "val": {"first_window": "2026-09-15T00:00:00Z", "end_window_exclusive": "2026-09-15T12:00:00Z"}}}
        available = {"2026-09-13T00:00:00Z", "2026-09-13T16:00:00Z", "2026-09-15T00:00:00Z", "2026-09-16T00:00:00Z"}
        chosen = fields_module.select_windows(spec, {"train": 8, "val": 6}, available)
        self.assertEqual(chosen, {"train": ["2026-09-13T00:00:00Z", "2026-09-13T16:00:00Z"],
                                  "val": ["2026-09-15T00:00:00Z"]})

    def test_excluded_and_missing_values_do_not_reach_the_interpolation(self):
        import pandas as pd
        window = "2026-09-13T00:00:00Z"
        labels = pd.DataFrame({
            "window": [window, window, window, "other"], "station_uuid": ["a", "b", "c", "a"],
            "pm25_ugm3": [10.0, 20.0, None, 99.0], "pm25_exclusion_reason": [None, "stuck", None, None],
            "no2_ugm3": [30.0, None, None, 99.0], "no2_exclusion_reason": [None, None, None, None]})
        stations = pd.DataFrame({"station_id": ["a", "b", "c"], "lat": [1.0, 2.0, 3.0], "lon": [4.0, 5.0, 6.0]})
        frame = fields_module.ground_truth_frame(labels, stations, window)
        self.assertEqual(sorted(zip(frame["station_id"], frame["pollutant"], frame["value"])),
                         [("a", "no2", 30.0), ("a", "pm25", 10.0)])
        self.assertEqual(set(frame.columns), {"station_id", "lon", "lat", "pollutant", "value"})


class EvaluationTests(unittest.TestCase):
    def test_every_validation_pair_runs_at_every_preference(self):
        router = two_route_router()
        with tempfile.TemporaryDirectory() as folder:
            fields = write_fields(folder, router)
            envs, _ = build_envs(router, fields, [OD_PAIRS[1]], tiny_config()["env"], "val", 0)
        torch.manual_seed(3)
        summary, records = evaluate(ActorCritic(SMALL).eval(), envs, [OD_PAIRS[1]], [0.0, 0.5, 1.0], SMALL, "cpu", 2,
                                    oracle=True)
        self.assertEqual(summary["episodes"], 3)
        self.assertEqual(sorted(r["alpha"] for r in records), [0.0, 0.5, 1.0])
        self.assertTrue(all(r["split"] == "val" for r in records))
        self.assertEqual(set(summary["by_alpha"]), {"0", "0.5", "1"})
        self.assertTrue(all(isinstance(r["return"], float) and math.isfinite(r["return"]) for r in records))
        self.assertIsInstance(envs[0], RouteEnv)


if __name__ == "__main__":
    unittest.main()
