"""TI-AI-10: paired route evaluation, failure handling and the decision rule."""
import json
import tempfile
import unittest
from pathlib import Path

from src.backend.route_model.morl import evaluate_candidate as evaluation
from src.backend.route_model.morl.multi_policy import train_set
from src.backend.route_model.training_env import ODPair

from src.backend.tests.test_morl_policy import (
    EXPOSURE, FixedActionPolicy, tiny_set_config, two_route_router, write_fields,
)

CRITERIA = {"default_alpha": 0.5, "route_validity_min": 1.0, "success_rate_min": 0.95, "inference_budget_ms": 4500,
            "inference_p95_max_ms": 4500, "eai04_median_exposure_reduction_min_pct": 4.0,
            "eai04_median_added_time_max_pct": 20.0, "eai05_fulfilled_min": 0.8}
OD = ODPair("test-00000", 1, 4, "test")
CLEAN_GIT = lambda: {"commit": "abc", "branch": "b", "dirty": False}  # noqa: E731


def route(time_s, exposure, sha="r", length_m=100.0):
    return {"reached": True, "valid": True, "time_s": time_s, "exposure": exposure, "length_m": length_m,
            "route_sha256": sha, "inference_ms": 10.0}


FAILED = {"reached": False, "valid": None, "failure": "dead_end", "inference_ms": 10.0}
BASE = route(100.0, 100.0, "fast")


def record(od, alpha, policy, member=None, optimal=None):
    return {"od_id": od, "window": "w", "alpha": alpha, "member": alpha if member is None else member, "policy": policy,
            "fastest": BASE, "optimal": optimal or BASE}


class RouteTests(unittest.TestCase):
    def setUp(self):
        self.router = two_route_router()

    def test_validity_follows_the_arcs_from_origin_to_destination(self):
        self.assertTrue(evaluation.route_is_valid(self.router, [12, 24], 1, 4))
        self.assertFalse(evaluation.route_is_valid(self.router, [12], 1, 4))         # stops short
        self.assertFalse(evaluation.route_is_valid(self.router, [13, 24], 1, 4))     # not connected
        self.assertFalse(evaluation.route_is_valid(self.router, [24, 12], 4, 1))     # against the one-way arcs
        self.assertFalse(evaluation.route_is_valid(self.router, [], 1, 1))

    def test_measure_matches_the_hand_calculated_route(self):
        fast = evaluation.measure(self.router, EXPOSURE, [12, 24], 1, 4)
        self.assertEqual((fast["time_s"], fast["exposure"], fast["length_m"], fast["edges"]), (20, 80, 20, 2))

    def test_every_method_sees_the_same_scenario(self):
        policies = {0.0: FixedActionPolicy(-1.0).eval(), 1.0: FixedActionPolicy(1.0).eval()}
        exposure = {**EXPOSURE, 25: 0.1}
        records = evaluation.evaluate_scenario(policies, self.router, exposure, OD, "w",
                                               {"max_steps": 20, "max_steps_factor": 3.0}, [0.0, 0.5, 1.0])
        self.assertEqual([r["alpha"] for r in records], [0.0, 0.5, 1.0])
        self.assertEqual({(r["od_id"], r["window"]) for r in records}, {("test-00000", "w")})
        self.assertEqual({r["fastest"]["time_s"] for r in records}, {20})
        clean = records[0]
        self.assertEqual(clean["member"], 0.0)
        self.assertEqual((clean["policy"]["time_s"], clean["policy"]["exposure"]), (30, 30))
        self.assertEqual((clean["optimal"]["time_s"], clean["optimal"]["exposure"]), (30, 30))
        self.assertEqual(records[2]["optimal"]["time_s"], 20)

    def test_a_policy_failure_is_recorded_not_replaced(self):
        policies = {0.5: FixedActionPolicy(1.0).eval()}
        exposure = {**EXPOSURE, 25: 9.0}   # forces the arc into the dead end at node 2
        record_ = evaluation.evaluate_scenario(policies, self.router, exposure, OD, "w",
                                               {"max_steps": 20, "max_steps_factor": 3.0}, [0.5])[0]
        self.assertFalse(record_["policy"]["reached"])
        self.assertEqual(record_["policy"]["failure"], "dead_end")
        self.assertIn("time_s", record_["fastest"])


class SummaryTests(unittest.TestCase):
    def test_changes_are_paired_percentages_with_spread(self):
        rows = [record("a", 0.5, route(110.0, 90.0)), record("b", 0.5, route(120.0, 95.0)), record("c", 0.5, route(130.0, 100.0))]
        result = evaluation.changes(rows, "policy")
        self.assertAlmostEqual(result["time"]["median"], 20.0)
        self.assertAlmostEqual(result["exposure"]["median"], -5.0)
        self.assertEqual(result["time"]["n"], 3)
        self.assertLessEqual(result["time"]["median_ci95"][0], result["time"]["median_ci95"][1])

    def test_failures_are_left_out_or_counted_as_fallback_never_hidden(self):
        rows = [record("a", 0.5, route(120.0, 90.0)), record("b", 0.5, FAILED)]
        self.assertEqual(evaluation.changes(rows, "policy")["exposure"]["n"], 1)
        with_fallback = evaluation.changes(rows, "policy", fallback=True)["exposure"]
        self.assertEqual(with_fallback["n"], 2)
        self.assertAlmostEqual(with_fallback["median"], -5.0)
        summary = evaluation.summarize(rows, 4500)
        self.assertEqual(summary["success_rate"], 0.5)
        self.assertEqual(summary["failures"], {"dead_end": 1})

    def test_all_failed_routes_leave_no_change_to_report(self):
        self.assertIsNone(evaluation.changes([record("a", 0.5, FAILED)], "policy")["time"])

    def test_alphas_served_by_one_member_count_one_decoded_route(self):
        same = route(100.0, 100.0)
        rows = [record("a", 0.1, same, member=0.0), record("a", 0.0, same, member=0.0)]
        self.assertEqual(evaluation.summarize(rows, 4500)["decoded_routes"], 1)

    def test_inference_over_budget_is_counted(self):
        slow = {**route(100.0, 100.0), "inference_ms": 5000.0}
        rows = [record("a", 0.5, slow), record("b", 0.5, route(100.0, 100.0))]
        self.assertEqual(evaluation.summarize(rows, 4500)["inference_ms"]["over_budget"], 1)

    def test_direction_counts_separate_trade_offs_from_routes_worse_on_both(self):
        rows = [record("a", 0.5, route(100.0, 100.0, "fast")),     # the fastest route itself
                record("b", 0.5, route(110.0, 90.0, "clean")),     # a real trade-off
                record("c", 0.5, route(110.0, 110.0, "bad"))]      # worse on both
        counts = evaluation.direction_counts(rows)
        self.assertEqual(counts["same_as_fastest"], 1)
        self.assertEqual(counts["less_exposure_and_slower_or_equal"], 1)
        self.assertEqual(counts["worse_on_both"], 1)

    def test_identical_routes_fulfil_the_preference_but_do_not_show_a_shift(self):
        rows = []
        # Moves the right way; identical routes; moves the wrong way; failed at one end.
        for od, clean, fast in (("a", route(110.0, 90.0, "c"), route(100.0, 100.0, "f")),
                                ("b", route(100.0, 100.0, "f"), route(100.0, 100.0, "f")),
                                ("c", route(110.0, 110.0, "w"), route(100.0, 100.0, "f")),
                                ("d", FAILED, route(100.0, 100.0, "f"))):
            rows += [record(od, 0.0, clean), record(od, 1.0, fast)]
        result = evaluation.preference_fulfilment(rows)
        self.assertEqual((result["od_pairs"], result["usable"], result["fulfilled"], result["shifted"]), (4, 3, 2, 1))
        self.assertAlmostEqual(result["fulfilled_rate"], 2 / 3)


def passing_summary():
    good = route(105.0, 90.0, "clean")
    rows = [row for i in range(30) for row in (record(f"od{i}", 0.0, good), record(f"od{i}", 0.5, good),
                                               record(f"od{i}", 1.0, route(100.0, 100.0, "fast")))]
    return rows


class DecisionTests(unittest.TestCase):
    def decide(self, rows):
        return evaluation.decide(evaluation.summarize(rows, 4500), CRITERIA)

    def test_all_gates_and_targets_make_a_candidate(self):
        decision = self.decide(passing_summary())
        self.assertEqual(decision["outcome"], "candidate")
        self.assertAlmostEqual(decision["research_targets"]["eai04"]["median_exposure_reduction_pct"], 10.0)

    def test_a_missed_target_is_conditional_and_says_so(self):
        same = route(100.0, 100.0, "fast")
        rows = [row for i in range(30) for row in (record(f"od{i}", 0.0, same), record(f"od{i}", 0.5, same),
                                                   record(f"od{i}", 1.0, same))]
        decision = self.decide(rows)
        self.assertEqual(decision["outcome"], "conditional_candidate")
        self.assertFalse(decision["research_targets"]["eai04"]["passed"])
        self.assertTrue(decision["research_targets"]["eai05"]["passed"])   # identical routes fulfil it...
        self.assertEqual(decision["research_targets"]["eai05"]["shifted_rate"], 0.0)  # ...and this shows it means little

    def test_too_much_added_time_misses_eai04(self):
        slow = route(125.0, 90.0, "clean")
        rows = [row for i in range(30) for row in (record(f"od{i}", 0.5, slow), record(f"od{i}", 1.0, BASE),
                                                   record(f"od{i}", 0.0, slow))]
        self.assertFalse(self.decide(rows)["research_targets"]["eai04"]["passed"])

    def test_any_failed_gate_means_not_a_candidate(self):
        low_success = passing_summary()
        for row in low_success[:15]:
            row["policy"] = FAILED
        self.assertEqual(self.decide(low_success)["outcome"], "not_a_candidate")

        invalid = passing_summary()
        invalid[0]["policy"] = {**invalid[0]["policy"], "valid": False}
        decision = self.decide(invalid)
        self.assertEqual(decision["outcome"], "not_a_candidate")
        self.assertFalse(decision["gates"]["route_validity"]["passed"])

        slow = [{**row, "policy": {**row["policy"], "inference_ms": 9000.0}} for row in passing_summary()]
        self.assertFalse(self.decide(slow)["gates"]["inference_p95_ms"]["passed"])


class RunTests(unittest.TestCase):
    def setUp(self):
        self.router = two_route_router()
        self.folder = tempfile.TemporaryDirectory()
        self.addCleanup(self.folder.cleanup)
        base = Path(self.folder.name)
        self.fields = write_fields(base / "fields", self.router, test_file=True)
        od = [ODPair("train-00000", 1, 4, "train"), ODPair("val-00000", 1, 4, "val")]
        set_config = tiny_set_config()
        set_config["augmentation"] = {"probability": 0.5, "max_log_std": 0.5}
        self.set_run = train_set(set_config, self.fields, base / "sets", router=self.router, od_pairs=od)
        self.out = base / "results"
        self.config = {"name": "t", "seed": 42, "threads": 1, "set_dir": str(self.set_run.dir), "perturbed_seed": 7,
                       "strata": [{"name": "s", "primary": True}], "alphas": [0.0, 0.5, 1.0], "criteria": CRITERIA}
        self.strata = {"s": ([OD], {})}

    def run_evaluation(self, git=CLEAN_GIT):
        return evaluation.run(self.config, self.fields, self.out, split="test", router=self.router, strata=self.strata,
                              git=git)

    def test_the_test_split_is_refused_on_a_dirty_or_unknown_tree(self):
        for dirty in (True, None):
            with self.assertRaises(evaluation.DirtyTree):
                self.run_evaluation(git=lambda value=dirty: {"commit": "abc", "branch": "b", "dirty": value})
        self.assertFalse(self.out.exists())

    def test_the_run_leaves_everything_later_tasks_need(self):
        self.run_evaluation()
        for name in ("manifest.json", "summary.json", "decision.json", "candidate.json", "scenarios.jsonl",
                     "scenario_od_pairs.json"):
            self.assertTrue((self.out / name).exists(), name)
        manifest = json.loads((self.out / "manifest.json").read_text(encoding="utf-8"))
        self.assertEqual(manifest["split"], "test")
        self.assertEqual(manifest["code"]["commit"], "abc")
        self.assertEqual(manifest["candidate"]["set_id"], self.set_run.dir.name)
        candidate = json.loads((self.out / "candidate.json").read_text(encoding="utf-8"))
        self.assertEqual(candidate["model_name"], "morl-route-policy")
        self.assertEqual(sorted(m["alpha"] for m in candidate["members"]), [0.0, 1.0])
        for member in candidate["members"]:
            self.assertTrue(Path(member["checkpoint"]).exists())
        self.assertTrue(candidate["constraints_for_integration"])

    def test_real_and_perturbed_results_are_kept_apart_and_only_real_decides(self):
        result = self.run_evaluation()
        self.assertEqual(set(result["summary"]["s"]), {"real", "perturbed"})
        rows = [json.loads(line) for line in (self.out / "scenarios.jsonl").read_text(encoding="utf-8").splitlines()]
        self.assertEqual({r["fields"] for r in rows}, {"real", "perturbed"})
        self.assertEqual(len(rows), 6)  # one OD pair x three alphas x two kinds of field
        expected = evaluation.decide(result["summary"]["s"]["real"], CRITERIA)
        self.assertEqual(result["decision"], expected)

    def test_policy_and_baseline_read_one_exposure_table_per_scenario(self):
        self.run_evaluation()
        rows = [json.loads(line) for line in (self.out / "scenarios.jsonl").read_text(encoding="utf-8").splitlines()]
        real = [r for r in rows if r["fields"] == "real"]
        self.assertEqual({r["window"] for r in real}, {"2026-09-29T00:00:00Z"})
        self.assertEqual({r["fastest"]["exposure"] for r in real}, {80.0})  # 4 x 20 s, from the same table


if __name__ == "__main__":
    unittest.main()
