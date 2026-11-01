import json
import math
import tempfile
import unittest
from pathlib import Path

from src.backend.route_model.astar_osm import Edge, RoadRouter
from src.backend.route_model.route_metrics import ExposureSnapshot
from src.backend.route_model.training_env import (
    EnvConfig, EpisodeLogger, InvalidEpisode, ODPair, PreferenceSpec, RouteEnv, SplitSpec,
    load_od_pairs, sample_od_pairs, save_od_pairs, split_windows,
)


def router(coords, specs, mode="bike"):
    edges = []
    for edge_id, u, v, seconds, two_way in specs:
        a, b = coords[u], coords[v]
        wkt = f"LINESTRING({a[0]} {a[1]},{b[0]} {b[1]})"
        edges.append((Edge(edge_id, u, v, seconds, seconds, wkt), two_way))
    return RoadRouter("test-v1", mode, coords, edges)


def snapshot(values, window="2026-10-07T16:00:00Z", version="test-v1"):
    return ExposureSnapshot(version, window, "exposure_index", values)


def action_for(obs, edge_id):
    return next(i for i, a in enumerate(obs["actions"]) if a["edge_id"] == edge_id)


def run(env, edge_ids, **reset):
    obs, _ = env.reset(**reset)
    for edge_id in edge_ids:
        obs, reward, terminated, truncated, info = env.step(action_for(obs, edge_id))
    return info


class TwoRouteTests(unittest.TestCase):
    """Fastest 1-2-4 (T=20 s, E=80 index·s) vs cleaner 1-3-4 (T=30 s, E=30 index·s)."""

    def setUp(self):
        coords = {1: (0, 0), 2: (.001, .001), 3: (.001, -.001), 4: (.002, 0), 5: (.003, 0)}
        self.r = router(coords, [(12, 1, 2, 10, False), (24, 2, 4, 10, False),
                                 (13, 1, 3, 15, False), (34, 3, 4, 15, False),
                                 (25, 2, 5, 5, False)])
        self.weights = snapshot({12: 4, 24: 4, 13: 1, 34: 1, 25: 1})
        self.od = ODPair("train-00000", 1, 4, "train")

    def env(self, **config):
        return RouteEnv(self.r, self.weights, [self.od], EnvConfig(**config))

    def test_reference_route_returns_minus_one_for_any_preference(self):
        for alpha in (0.0, 0.3, 1.0):
            info = run(self.env(), [12, 24], preference=(alpha, 1 - alpha))
            record = info["episode"]
            self.assertEqual(record["outcome"], "reached")
            self.assertAlmostEqual(record["return"], -1)
            self.assertAlmostEqual(record["return_time"], -1)
            self.assertAlmostEqual(record["return_exposure"], -1)
            self.assertEqual((record["travel_time_s"], record["exposure"]), (20, 80))

    def test_hand_calculated_preference_tradeoff(self):
        # Normalised cleaner route: (30/20, 30/80) = (1.5, 0.375).
        for alpha, expected in ((0.8, 1.275), (0.2, 0.6)):
            record = run(self.env(), [13, 34], preference=(alpha, 1 - alpha))["episode"]
            self.assertAlmostEqual(record["weighted_cost"], expected)
            self.assertAlmostEqual(record["return"], -expected)
        # alpha=0.8 prefers the fastest route (cost 1), alpha=0.2 the cleaner one.

    def test_step_reward_vector_and_scalarisation(self):
        env = self.env()
        obs, _ = env.reset(preference=(0.25, 0.75))
        _, reward, terminated, truncated, info = env.step(action_for(obs, 13))
        self.assertEqual(info["reward_vector"], [-15 / 20, -15 / 80])
        self.assertAlmostEqual(reward, 0.25 * -15 / 20 + 0.75 * -15 / 80)
        self.assertFalse(terminated or truncated)

    def test_illegal_actions_are_penalised_without_moving(self):
        env = self.env(illegal_action_penalty=0.5)
        obs, _ = env.reset(preference=(0.5, 0.5))
        for action in (-1, len(obs["actions"]), 1.0, True, None):
            obs, reward, terminated, truncated, info = env.step(action)
            self.assertTrue(info["illegal_action"])
            self.assertEqual(reward, -0.5)
            self.assertEqual(obs["node"], 1)
        self.assertEqual(env.illegal_actions, 5)

    def test_masked_revisit_is_illegal(self):
        coords = {1: (0, 0), 2: (.001, 0), 3: (.002, 0)}
        r = router(coords, [(1, 1, 2, 10, True), (2, 2, 3, 10, True)])
        env = RouteEnv(r, snapshot({1: 1, 2: 1}), [ODPair("train-0", 1, 3, "train")])
        obs, _ = env.reset(preference=(1, 0))
        obs, *_ = env.step(action_for(obs, 1))
        back = next(i for i, a in enumerate(obs["actions"]) if a["neighbor"] == 1)
        self.assertFalse(obs["action_mask"][back])
        self.assertTrue(obs["actions"][back]["visited"])
        obs, _, _, _, info = env.step(back)
        self.assertTrue(info["illegal_action"])
        self.assertEqual(obs["node"], 2)

    def test_dead_end_terminates_with_failure_penalty(self):
        env = self.env(failure_penalty=2.0)
        obs, _ = env.reset(preference=(1, 0))
        obs, _, _, _, _ = env.step(action_for(obs, 12))
        _, reward, terminated, truncated, info = env.step(action_for(obs, 25))  # node 5 is a sink
        self.assertEqual(info["outcome"], "dead_end")
        self.assertTrue(terminated)
        self.assertFalse(truncated)
        self.assertAlmostEqual(reward, -5 / 20 - 2.0)
        with self.assertRaises(RuntimeError):
            env.step(0)

    def test_truncation_at_step_limit(self):
        env = self.env(max_steps=1)
        obs, info = env.reset(preference=(1, 0))
        self.assertEqual(info["step_limit"], 1)
        _, reward, terminated, truncated, info = env.step(action_for(obs, 12))
        self.assertTrue(truncated)
        self.assertFalse(terminated)
        self.assertEqual(info["episode"]["outcome"], "truncated")
        self.assertAlmostEqual(reward, -10 / 20 - 2.0)

    def test_step_limit_scales_with_reference(self):
        _, info = self.env(max_steps=100, max_steps_factor=3).reset(preference=(1, 0))
        self.assertEqual(info["step_limit"], 6)

    def test_invalid_preferences(self):
        env = self.env()
        for preference in ((0.6, 0.5), (-0.1, 1.1), (True, 0), (math.nan, 1)):
            with self.assertRaises(InvalidEpisode) as caught:
                env.reset(preference=preference)
            self.assertEqual(caught.exception.reason, "invalid_preference")

    def test_missing_weights_are_imputed_with_median_and_counted(self):
        env = RouteEnv(self.r, snapshot({12: 4, 24: 4, 13: 1}), [self.od])
        self.assertEqual(env.imputed_edges, {34, 25})
        self.assertEqual(env.imputed_value, 4)
        record = run(env, [13, 34], preference=(0, 1))["episode"]
        self.assertEqual(record["imputed_edges_traversed"], 1)
        self.assertEqual(record["exposure"], 15 * 1 + 15 * 4)

    def test_rejects_unusable_weights(self):
        for weights in (snapshot({12: 1}, version="other"), snapshot({}), snapshot({999: 1})):
            with self.assertRaises(ValueError):
                RouteEnv(self.r, weights, [self.od])
        for bad in (-1, math.inf, math.nan):
            with self.assertRaises(ValueError):
                RouteEnv(self.r, snapshot({12: bad}), [self.od])

    def test_zero_exposure_reference_is_normalised_by_one(self):
        env = RouteEnv(self.r, snapshot({12: 0, 24: 0, 13: 0, 34: 0, 25: 0}), [self.od])
        record = run(env, [12, 24], preference=(0, 1))["episode"]
        self.assertEqual(record["reference_exposure"], 0)
        self.assertEqual(record["return"], 0)

    def test_disconnected_od_is_rejected(self):
        coords = {1: (0, 0), 2: (.001, 0), 3: (.01, 0), 4: (.011, 0)}
        r = router(coords, [(1, 1, 2, 10, True), (2, 3, 4, 10, True)])
        env = RouteEnv(r, snapshot({1: 1, 2: 1}), [ODPair("train-0", 1, 4, "train")])
        with self.assertRaises(InvalidEpisode) as caught:
            env.reset()
        self.assertEqual(caught.exception.reason, "no_route")

    def test_same_node_and_unknown_nodes_are_rejected(self):
        with self.assertRaises(InvalidEpisode) as caught:
            RouteEnv(self.r, self.weights, [ODPair("train-0", 4, 1, "train")])  # 4 has no outgoing arc
        self.assertEqual(caught.exception.reason, "unknown_node")
        coords = {1: (0, 0), 2: (.001, 0)}
        r = router(coords, [(1, 1, 2, 10, True)])
        env = RouteEnv(r, snapshot({1: 1}), [ODPair("train-0", 1, 1, "train")])
        with self.assertRaises(InvalidEpisode) as caught:
            env.reset()
        self.assertEqual(caught.exception.reason, "same_node")


class SplitTests(unittest.TestCase):
    def setUp(self):
        # 12 x 12 bidirectional grid, 0.01 deg apart; 0.02 deg blocks -> 36 blocks.
        coords, specs = {}, []
        n = 12
        for i in range(n):
            for j in range(n):
                coords[i * n + j] = (106.7 + .01 * i, -6.3 + .01 * j)
        for i in range(n):
            for j in range(n):
                if i + 1 < n:
                    specs.append((len(specs) + 1, i * n + j, (i + 1) * n + j, 60, True))
                if j + 1 < n:
                    specs.append((len(specs) + 1, i * n + j, i * n + j + 1, 60, True))
        self.r = router(coords, specs)
        self.weights = snapshot({e: 1 + (e % 7) for e in self.r.edges})
        self.spec = SplitSpec(block_deg=.02, seed=3)

    def sample(self, seed=1):
        return sample_od_pairs(self.r, self.spec, {"train": 20, "val": 4, "test": 4}, seed=seed,
                               min_distance_m=500, max_distance_m=10_000)

    def test_od_sampling_is_reproducible_and_split_by_block(self):
        pairs, stats = self.sample()
        self.assertEqual(pairs, self.sample()[0])
        self.assertNotEqual(pairs, self.sample(seed=2)[0])
        self.assertEqual(sum(p.split == "train" for p in pairs), 20)
        blocks = {name: set() for name in ("train", "val", "test")}
        for p in pairs:
            for node in (p.start, p.end):
                self.assertEqual(self.spec.split_of(self.r.coords[node]), p.split)
                blocks[p.split].add(self.spec.block(self.r.coords[node]))
        self.assertFalse(blocks["train"] & blocks["test"])
        self.assertFalse(blocks["train"] & blocks["val"])
        self.assertEqual(stats["splits"]["test"]["pairs"], 4)

    def test_environment_never_sees_other_splits(self):
        pairs, _ = self.sample()
        with self.assertRaises(InvalidEpisode) as caught:
            RouteEnv(self.r, self.weights, pairs)
        self.assertEqual(caught.exception.reason, "split_violation")
        train = [p for p in pairs if p.split == "train"]
        test_id = next(p.od_id for p in pairs if p.split == "test")
        env = RouteEnv(self.r, self.weights, train)
        with self.assertRaises(InvalidEpisode) as caught:
            env.reset(od_id=test_id)
        self.assertEqual(caught.exception.reason, "unknown_od")
        seen = {env.reset(seed=s)[1]["od_id"] for s in range(50)}
        self.assertTrue(seen <= {p.od_id for p in train})

    def test_time_window_split(self):
        windows = [f"2026-10-{d:02d}T00:00:00Z" for d in range(1, 21)]
        splits = split_windows(reversed(windows))
        self.assertEqual(splits["train"], tuple(windows[:14]))
        self.assertEqual(splits["test"], tuple(windows[17:]))
        train = [p for p in self.sample()[0] if p.split == "train"]
        test_weights = snapshot(self.weights.concentration_ug_m3, window=windows[-1])
        with self.assertRaises(InvalidEpisode):
            RouteEnv(self.r, test_weights, train, window_splits=splits)
        RouteEnv(self.r, snapshot(self.weights.concentration_ug_m3, window=windows[0]), train, window_splits=splits)

    def test_od_file_round_trip(self):
        pairs, stats = self.sample()
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / "od.json"
            save_od_pairs(path, pairs, self.spec, stats, self.r.version)
            self.assertEqual(load_od_pairs(path), pairs)
            self.assertEqual(load_od_pairs(path, split="val"), [p for p in pairs if p.split == "val"])
            with self.assertRaises(ValueError):
                load_od_pairs(path, graph_version="other")

    def test_seeded_episodes_are_reproducible(self):
        train = [p for p in self.sample()[0] if p.split == "train"]

        def rollout(seed):
            env = RouteEnv(self.r, self.weights, train, EnvConfig(seed=seed))
            trace = []
            for _ in range(5):
                obs, info = env.reset()
                trace.append((info["od_id"], tuple(info["preference"])))
            return trace

        self.assertEqual(rollout(7), rollout(7))
        self.assertNotEqual(rollout(7), rollout(8))
        grid = RouteEnv(self.r, self.weights, train, EnvConfig(preference=PreferenceSpec("grid", (0.0, 1.0))))
        self.assertTrue(all(grid.reset()[1]["preference"] in ([0.0, 1.0], [1.0, 0.0]) for _ in range(10)))


class LoggingSmokeTests(unittest.TestCase):
    def test_episode_metrics_are_logged(self):
        coords = {1: (0, 0), 2: (.001, .001), 3: (.001, -.001), 4: (.002, 0), 5: (.003, 0)}
        r = router(coords, [(12, 1, 2, 10, False), (24, 2, 4, 10, False), (13, 1, 3, 15, False),
                            (34, 3, 4, 15, False), (25, 2, 5, 5, False)])
        weights = snapshot({12: 4, 24: 4, 13: 1, 34: 1, 25: 1})
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / "episodes.jsonl"
            logger = EpisodeLogger(path)
            env = RouteEnv(r, weights, [ODPair("train-0", 1, 4, "train")], EnvConfig(seed=5), logger=logger)
            run(env, [12, 24], preference=(0.5, 0.5))           # reached, fastest
            run(env, [13, 34], preference=(0.2, 0.8))           # reached, cleaner
            obs, _ = env.reset(preference=(0.5, 0.5))           # illegal, then dead end
            env.step(99)
            obs, *_ = env.step(action_for(obs, 12))
            env.step(action_for(obs, 25))
            lines = [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines()]
        self.assertEqual([r["outcome"] for r in lines], ["reached", "reached", "dead_end"])
        self.assertEqual([r["episode"] for r in lines], [0, 1, 2])
        self.assertEqual(lines[2]["illegal_actions"], 1)
        self.assertEqual(lines[2]["steps"], 3)
        self.assertAlmostEqual(lines[1]["return"], -0.6)
        for record in lines:
            self.assertEqual(record["graph_version"], "test-v1")
            self.assertEqual(record["time_window"], "2026-10-07T16:00:00Z")
        summary = logger.summary()
        self.assertEqual(summary["outcomes"], {"reached": 2, "truncated": 0, "dead_end": 1})
        self.assertAlmostEqual(summary["success_rate"], 2 / 3)
        self.assertAlmostEqual(summary["reached_mean_weighted_cost"], (1 + 0.6) / 2)
        manifest = env.manifest()
        self.assertEqual(manifest["weights"]["imputed_edges"], 0)
        self.assertEqual(manifest["config"]["seed"], 5)


if __name__ == "__main__":
    unittest.main()
