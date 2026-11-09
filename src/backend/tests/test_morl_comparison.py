"""Focused tests of paired evaluation; no experiment checkpoints or test data are run."""
import tempfile
import unittest
import json
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from src.backend.route_model.morl import compare_candidates as comparison
from src.backend.route_model.morl import evaluate_candidate as evaluation
from src.backend.route_model.morl.infer import PolicyFailure
from src.backend.route_model.training_env import ODPair, file_sha256
from src.backend.tests.test_morl_policy import EXPOSURE, FixedActionPolicy, two_route_router


class ComparisonTests(unittest.TestCase):
    def setUp(self):
        self.router = two_route_router()
        self.od = ODPair("pair", 1, 4, "val")
        self.config = {"max_steps": 20, "max_steps_factor": 3.0}

    def test_each_method_gets_identical_inputs_and_requested_preference(self):
        calls = []

        def conditioned(model, alpha, router, exposure, od, env_config):
            calls.append((model, alpha, router, exposure, od, env_config))
            return {"reached": False, "valid": None, "failure": "dead_end", "inference_ms": 10}

        policies = {0.0: FixedActionPolicy(-1.0).eval(), 1.0: FixedActionPolicy(1.0).eval()}
        with patch.object(comparison, "decode_conditioned", side_effect=conditioned):
            rows = comparison.evaluate_scenario(policies, "a", "prior", self.router, EXPOSURE, self.od,
                                                "window", self.config, [0.0, 0.5, 1.0])
        self.assertEqual([r["methods"]["fixed_preference_set"]["member"] for r in rows], [0.0, 0.0, 1.0])
        self.assertEqual([(c[0], c[1]) for c in calls], [(m, a) for a in (0.0, 0.5, 1.0) for m in ("a", "prior")])
        self.assertTrue(all(c[2:] == (self.router, EXPOSURE, self.od, self.config) for c in calls))
        self.assertEqual({(r["od_id"], r["window"]) for r in rows}, {("pair", "window")})

    def test_conditioned_failure_is_not_a_success_and_fallback_is_separate(self):
        model = SimpleNamespace(config=object())
        with patch.object(comparison, "decode_route", side_effect=PolicyFailure("dead_end", "dead end")):
            failed = comparison.decode_conditioned(model, 0.5, self.router, EXPOSURE, self.od, self.config)
        self.assertEqual((failed["reached"], failed["failure"]), (False, "dead_end"))
        base = {"reached": True, "valid": True, "time_s": 10, "exposure": 10, "length_m": 10,
                "route_sha256": "base", "inference_ms": 1}
        rows = [{"od_id": "p", "window": "w", "alpha": 0.5, "fastest": base, "optimal": base,
                 "methods": {"scalarized": {"member": 0.5, "route": failed}}}]
        summary = evaluation.summarize(comparison.method_records(rows, "scalarized"), 4500)
        self.assertEqual(summary["success_rate"], 0)
        self.assertEqual(summary["failures"], {"dead_end": 1})
        self.assertIsNone(summary["by_alpha"]["0.5"]["policy"]["time"])
        self.assertEqual(summary["by_alpha"]["0.5"]["policy_with_fallback"]["time"]["median"], 0)

    def test_bound_preference_and_route_validity(self):
        model = SimpleNamespace(config=object())
        route = SimpleNamespace(edge_ids=(12, 24))
        with patch.object(comparison, "decode_route", return_value=route) as decode:
            result = comparison.decode_conditioned(model, 0.3, self.router, EXPOSURE, self.od, self.config)
        self.assertEqual(decode.call_args.args[0].alpha, 0.3)
        self.assertTrue(result["valid"])
        self.assertGreaterEqual(result["inference_ms"], 0)

    def test_checkpoint_checksum_is_enforced(self):
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / "weights"
            path.write_bytes(b"original")
            digest = file_sha256(path)
            self.assertEqual(comparison.checked_checkpoint(path, digest), digest)
            path.write_bytes(b"changed")
            with self.assertRaisesRegex(ValueError, "hash mismatch"):
                comparison.checked_checkpoint(path, digest)

    def test_test_split_refuses_dirty_or_unidentified_commit_before_loading(self):
        for state in ({"dirty": True, "commit": "abc"}, {"dirty": None, "commit": None},
                      {"dirty": False, "commit": None}):
            with self.assertRaises(evaluation.DirtyTree):
                comparison.run({}, "unused", "unused", split="test", git=lambda: state)

    def test_synthetic_run_records_paired_results_and_model_specific_summaries(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            fields, graph, out = root / "fields", root / "graph", root / "out"
            fields.mkdir()
            graph.mkdir()
            for name in comparison.GRAPH_FILES:
                (graph / name).write_bytes(name.encode())
            (fields / "one.csv").write_text("synthetic", encoding="utf-8")
            (fields / "manifest.json").write_text(json.dumps({"window_splits": {"val": ["w"]},
                "windows": {"w": {"file": "one.csv"}}}), encoding="utf-8")
            sm = {"config": {"mode": self.router.mode, "env": self.config, "seed": 42},
                  "data": {"graph_version": self.router.version, "mode": self.router.mode}, "set_id": "set"}
            am = {"run_id": "scalar", "best": {"update": 0}}
            policies = {0.0: FixedActionPolicy(-1.0).eval(), 1.0: FixedActionPolicy(1.0).eval()}
            candidates = (policies, "scalar", "prior", sm, am, {"0": "hash"}, "scalar-hash")
            env = SimpleNamespace(exposure=EXPOSURE, weights=SimpleNamespace(time_window="w"))
            failure = {"reached": False, "valid": None, "failure": "dead_end", "inference_ms": 5.0}
            config = {"set_dir": "unused", "criteria": {"default_alpha": 0.5, "route_validity_min": 1.0,
                "success_rate_min": 0.95, "inference_budget_ms": 4500, "inference_p95_max_ms": 4500,
                "eai04_median_exposure_reduction_min_pct": 4.0, "eai04_median_added_time_max_pct": 20.0,
                "eai05_fulfilled_min": 0.8}, "strata": [{"name": "s", "primary": True}],
                "alphas": [0.0, 0.5, 1.0]}
            with patch.object(comparison, "load_candidates", return_value=candidates), \
                 patch.object(comparison, "build_envs", return_value=([env], {})), \
                 patch.object(comparison, "decode_conditioned", return_value=failure):
                result = comparison.run(config, fields, out, split="val", graph_dir=graph, router=self.router,
                                        strata={"s": ([self.od], {})}, git=lambda: {"commit": "abc", "dirty": True})
            rows = evaluation.read_scenarios(out)
            self.assertEqual(len(rows), 3)
            self.assertTrue(all(set(r["methods"]) == set(comparison.METHODS) for r in rows))
            self.assertEqual(result["summary"]["s"]["real"]["scalarized"]["success_rate"], 0)
            self.assertEqual(result["decision"]["scalarized"]["outcome"], "not_a_candidate")
            self.assertEqual(result["manifest"]["candidates"]["scalarized"]["checkpoint_sha256"], "scalar-hash")
            self.assertTrue((out / "scenario_od_pairs.json").exists())
            with self.assertRaises(FileExistsError):
                comparison.run(config, fields, out, split="val", git=lambda: {})


if __name__ == "__main__":
    unittest.main()
