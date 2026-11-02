import json
import math
import tempfile
import unittest
from pathlib import Path

from src.backend.route_model.astar_osm import Edge, RoadRouter
from src.backend.route_model.route_metrics import ExposureSnapshot
from src.backend.route_model.training_env import (
    SYNTHETIC_WINDOW, EnvConfig, EpisodeLogger, InvalidEpisode, ODPair, PreferenceSpec, RouteEnv, SplitSpec,
    load_od_pairs, load_weights, sample_od_pairs, save_od_pairs, split_windows,
)

try:
    import numpy as np
except ImportError:  # From requirements-test.txt; CI's check_junit rejects this skip
    np = None


def router(coords, specs, mode="bike"):
    edges = []
    for edge_id, u, v, seconds, two_way in specs:
        a, b = coords[u], coords[v]
        wkt = f"LINESTRING({a[0]} {a[1]},{b[0]} {b[1]})"
        edges.append((Edge(edge_id, u, v, seconds, seconds, wkt), two_way))
    return RoadRouter("test-v1", mode, coords, edges)


def snapshot(values, window=SYNTHETIC_WINDOW, version="test-v1"):
    return ExposureSnapshot(version, window, "exposure_index", values)


def make_env(*args, window_splits=None, **kwargs):
    # Synthetic snapshots may opt out of the time-window split explicitly.
    return RouteEnv(*args, window_splits=window_splits, **kwargs)


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
        return make_env(self.r, self.weights, [self.od], EnvConfig(**config))

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
        env = make_env(r, snapshot({1: 1, 2: 1}), [ODPair("train-0", 1, 3, "train")])
        obs, _ = env.reset(preference=(1, 0))
        obs, *_ = env.step(action_for(obs, 1))
        back = next(i for i, a in enumerate(obs["actions"]) if a["neighbor"] == 1)
        self.assertFalse(obs["action_mask"][back])
        self.assertTrue(obs["actions"][back]["visited"])
        obs, _, _, _, info = env.step(back)
        self.assertTrue(info["illegal_action"])
        self.assertEqual(obs["node"], 2)

    def test_numpy_integer_actions_are_legal(self):
        if np is None:
            self.skipTest("numpy not installed")
        env = self.env()
        obs, _ = env.reset(preference=(0.5, 0.5))
        for edge_id in (12, 24):
            scores = np.array([a["edge_id"] == edge_id for a in obs["actions"]], dtype=float)
            action = np.argmax(np.where(obs["action_mask"], scores, -np.inf))
            self.assertIsInstance(action, np.int64)
            obs, _, _, _, info = env.step(action)
            self.assertFalse(info["illegal_action"])
        self.assertEqual(info["episode"]["outcome"], "reached")
        self.assertEqual(info["episode"]["illegal_actions"], 0)
        env.reset(preference=(0.5, 0.5))
        self.assertTrue(env.step(np.bool_(True))[4]["illegal_action"])

    def test_dead_end_settles_at_failure_return(self):
        env = self.env(failure_return=4.0)
        obs, _ = env.reset(preference=(1, 0))
        obs, _, _, _, _ = env.step(action_for(obs, 12))  # vector (-0.5, -0.5)
        _, reward, terminated, truncated, info = env.step(action_for(obs, 25))  # node 5 is a sink
        self.assertEqual(info["outcome"], "dead_end")
        self.assertTrue(terminated)
        self.assertFalse(truncated)
        self.assertEqual(info["reward_vector"], [-3.5, -3.5])
        self.assertAlmostEqual(reward, -3.5)
        record = info["episode"]
        self.assertEqual((record["return"], record["return_time"], record["return_exposure"]), (-4, -4, -4))
        with self.assertRaises(RuntimeError):
            env.step(0)

    def test_truncation_at_step_limit(self):
        env = self.env(max_steps=1)  # clamped up to the 2-edge reference
        obs, info = env.reset(preference=(1, 0))
        self.assertEqual(info["step_limit"], 2)
        obs, *_ = env.step(action_for(obs, 12))
        _, reward, terminated, truncated, info = env.step(99)
        self.assertTrue(truncated)
        self.assertFalse(terminated)
        self.assertEqual(info["episode"]["outcome"], "truncated")
        self.assertAlmostEqual(info["episode"]["return"], -4)

    def test_failing_early_is_not_cheaper_than_failing_late(self):
        # Every failure returns exactly -failure_return, whatever happened before it.
        coords = {i: (.001 * i, 0) for i in range(1, 8)}
        coords[8] = (0, .001)
        r = router(coords, [(i, i, i + 1, 10, False) for i in range(1, 7)] + [(7, 1, 8, 10, False)])
        weights = snapshot({e: 1 for e in range(1, 8)})
        returns = []
        for config, actions in ((EnvConfig(max_steps=1), [99] * 6),        # illegal until truncated
                                (EnvConfig(), [8]),                         # immediate dead end
                                (EnvConfig(max_steps_factor=1), [2, 3, 4, 5, 6, 99])):  # long walk, truncated
            env = make_env(r, weights, [ODPair("train-0", 1, 7, "train")], config)
            obs, _ = env.reset(preference=(0.3, 0.7))
            info = {}
            for target in actions:
                action = next((i for i, a in enumerate(obs["actions"]) if a["neighbor"] == target), target)
                obs, _, _, _, info = env.step(action)
                if "episode" in info:
                    break
            returns.append(info["episode"]["return"])
        self.assertEqual([round(x, 9) for x in returns], [-4.0, -4.0, -4.0])

    def test_failure_settlement_is_determined_by_the_observation(self):
        # The final reward cancels accumulated costs and penalties, so a value
        # function can only predict it if the observation carries all of them.
        for illegal in (0, 1, 3):
            env = self.env(illegal_action_penalty=0.1, failure_return=4.0)
            obs, _ = env.reset(preference=(0.5, 0.5))
            for _ in range(illegal):
                obs, *_ = env.step(99)
            obs, *_ = env.step(action_for(obs, 12))
            self.assertEqual(obs["illegal_actions"], illegal)
            *_, info = env.step(action_for(obs, 25))
            penalties = 0.1 * obs["illegal_actions"]
            self.assertAlmostEqual(info["reward_vector"][0], -4.0 + obs["time_ratio"] + penalties)
            self.assertAlmostEqual(info["reward_vector"][1], -4.0 + obs["exposure_ratio"] + penalties)
            self.assertAlmostEqual(info["episode"]["return"], -4.0)

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
        env = make_env(self.r, snapshot({12: 4, 24: 4, 13: 1}), [self.od])
        self.assertEqual(env.imputed_edges, {34, 25})
        self.assertEqual(env.imputed_value, 4)
        record = run(env, [13, 34], preference=(0, 1))["episode"]
        self.assertEqual(record["imputed_edges_traversed"], 1)
        self.assertEqual(record["exposure"], 15 * 1 + 15 * 4)

    def test_rejects_unusable_weights(self):
        for weights in (snapshot({12: 1}, version="other"), snapshot({}), snapshot({999: 1})):
            with self.assertRaises(ValueError):
                make_env(self.r, weights, [self.od])
        for bad in (-1, math.inf, math.nan):
            with self.assertRaises(ValueError):
                make_env(self.r, snapshot({12: bad}), [self.od])

    def test_zero_exposure_reference_is_normalised_by_one(self):
        env = make_env(self.r, snapshot({12: 0, 24: 0, 13: 0, 34: 0, 25: 0}), [self.od])
        record = run(env, [12, 24], preference=(0, 1))["episode"]
        self.assertEqual(record["reference_exposure"], 0)
        self.assertEqual(record["return"], 0)

    def test_disconnected_od_is_rejected(self):
        coords = {1: (0, 0), 2: (.001, 0), 3: (.01, 0), 4: (.011, 0)}
        r = router(coords, [(1, 1, 2, 10, True), (2, 3, 4, 10, True)])
        env = make_env(r, snapshot({1: 1, 2: 1}), [ODPair("train-0", 1, 4, "train")])
        with self.assertRaises(InvalidEpisode) as caught:
            env.reset()
        self.assertEqual(caught.exception.reason, "no_route")

    def test_same_node_and_unknown_nodes_are_rejected(self):
        with self.assertRaises(InvalidEpisode) as caught:
            make_env(self.r, self.weights, [ODPair("train-0", 4, 1, "train")])  # 4 has no outgoing arc
        self.assertEqual(caught.exception.reason, "unknown_node")
        coords = {1: (0, 0), 2: (.001, 0)}
        r = router(coords, [(1, 1, 2, 10, True)])
        env = make_env(r, snapshot({1: 1}), [ODPair("train-0", 1, 1, "train")])
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
            make_env(self.r, self.weights, pairs)
        self.assertEqual(caught.exception.reason, "split_violation")
        train = [p for p in pairs if p.split == "train"]
        test_id = next(p.od_id for p in pairs if p.split == "test")
        env = make_env(self.r, self.weights, train)
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
        values = self.weights.concentration_ug_m3
        make_env(self.r, snapshot(values, window=windows[0]), train, window_splits=splits)
        overlapping = dict(splits, test=splits["test"] + (windows[0],))
        for window, window_splits in ((windows[-1], splits),            # test window
                                      ("2026-11-01T00:00:00Z", splits),  # window in no split
                                      (windows[0], overlapping),         # window in two splits
                                      (windows[0], None)):               # real window, no split given
            with self.assertRaises(InvalidEpisode) as caught:
                make_env(self.r, snapshot(values, window=window), train, window_splits=window_splits)
            self.assertEqual(caught.exception.reason, "split_violation")
        with self.assertRaises(TypeError):  # the argument itself is required
            RouteEnv(self.r, self.weights, train)

    def test_split_windows_validates_fractions(self):
        windows = [f"2026-10-{d:02d}T00:00:00Z" for d in range(1, 11)]
        for fractions in ((0.5, 0.5, 0.5), (0, 0.5, 0.5), (0.7, 0.3), (1.2, -0.1, -0.1)):
            with self.assertRaises(ValueError):
                split_windows(windows, fractions)
        self.assertEqual([len(v) for v in split_windows(windows, (0.6, 0.2, 0.2)).values()], [6, 2, 2])

    def test_weight_loaders(self):
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / "edge_weights.csv"
            path.write_text("edge_id,exposure_index,pm25_ugm3\n1,1.5,20\n2,,30\n", encoding="utf-8")
            weights = load_weights(path, "test-v1", "2026-10-07T16:00:00Z")
            self.assertEqual(dict(weights.concentration_ug_m3), {1: 1.5})
            self.assertEqual(load_weights(path, "test-v1", "w", "pm25_ugm3").concentration_ug_m3, {1: 20, 2: 30})
            try:
                import pandas as pd
                pd.DataFrame({"edge_id": [1, 2], "exposure_index": [1.5, None]}).to_parquet(
                    Path(folder) / "edge_weights.parquet", index=False)
            except ImportError:
                self.skipTest("pandas with a Parquet engine is not installed")
            weights = load_weights(Path(folder) / "edge_weights.parquet", "test-v1", "w")
            self.assertEqual(dict(weights.concentration_ug_m3), {1: 1.5})

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
            env = make_env(self.r, self.weights, train, EnvConfig(seed=seed))
            trace = []
            for _ in range(5):
                obs, info = env.reset()
                trace.append((info["od_id"], tuple(info["preference"])))
            return trace

        self.assertEqual(rollout(7), rollout(7))
        self.assertNotEqual(rollout(7), rollout(8))
        grid = make_env(self.r, self.weights, train, EnvConfig(preference=PreferenceSpec("grid", (0.0, 1.0))))
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
            env = make_env(r, weights, [ODPair("train-0", 1, 4, "train")], EnvConfig(seed=5), logger=logger)
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
            self.assertEqual(record["time_window"], SYNTHETIC_WINDOW)
        summary = logger.summary()
        self.assertEqual(summary["outcomes"], {"reached": 2, "truncated": 0, "dead_end": 1})
        self.assertAlmostEqual(summary["success_rate"], 2 / 3)
        self.assertAlmostEqual(summary["reached_mean_weighted_cost"], (1 + 0.6) / 2)
        manifest = env.manifest()
        self.assertEqual(manifest["weights"]["imputed_edges"], 0)
        self.assertEqual(manifest["config"]["seed"], 5)


if __name__ == "__main__":
    unittest.main()
