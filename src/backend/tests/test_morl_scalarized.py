"""TI-AI-09, PIC A: Preference Encoder, the preference-conditioned policy and its run record.

Small graphs with hand-known routes; the helpers and the shared run record are those of PIC B.
"""
import json
import tempfile
import unittest
from pathlib import Path
from unittest import mock

import numpy as np
import torch

from src.backend.route_model.morl import scalarized as scalarized_module
from src.backend.route_model.morl.features import GLOBAL_FEATURES, collate
from src.backend.route_model.morl.infer import PolicyFailure, load_policy, save_checkpoint
from src.backend.route_model.morl.policy import to_tensors
from src.backend.route_model.morl.resources import current_rss_bytes, replace_file
from src.backend.route_model.morl.scalarized import (
    PreferenceActor, Scalarized, decode_preference_route, export_evidence, load_scalarized_policy, train_scalarized,
    verify,
)
from src.backend.route_model.morl.scalarized_policy import (
    BoundPreference, PreferenceActorCritic, PreferenceEncoder, RegretPrior, ScalarizedConfig, scalarized_regret,
)
from src.backend.route_model.morl.scalarized_report import write_summary
from src.backend.route_model.morl.train import TrainingDiverged, build_envs, evaluate
from src.backend.tests.test_morl_policy import EXPOSURE, OD_PAIRS, tiny_config, two_route_router, write_fields

SMALL = ScalarizedConfig(hidden_dim=16, graph_layers=2, ego_hops=3, ego_max_nodes=32, preference_dim=8, fourier=2)
FAST, CLEAN = (12, 24), (13, 34)  # T=20 s, E=80 and T=30 s, E=30; the fast one is the reference


def tiny_scalarized_config(warm_episodes=0, **schedule):
    config = tiny_config(**schedule)
    del config["alpha"]
    config.update(algorithm=scalarized_module.ALGORITHM,
                  preference={"selection_alphas": [0.0, 1.0], "report_alphas": [0.0, 0.5, 1.0]})
    config["model"] |= {"preference_dim": 8, "fourier": 2}
    if warm_episodes:
        config["warm_start"] = {"teacher": "weighted", "episodes": warm_episodes, "epochs": 30, "minibatch_size": 32,
                                "learning_rate": 0.01}
    return config


class PreferenceEncoderTests(unittest.TestCase):
    def test_output_depends_on_the_preference_and_on_nothing_else(self):
        torch.manual_seed(0)
        encoder = PreferenceEncoder(8, 2)
        z = encoder(torch.tensor([[0.0, 1.0], [1.0, 0.0], [0.0, 1.0]]))
        self.assertEqual(z.shape, (3, 8))
        self.assertTrue(torch.equal(z[0], z[2]))
        self.assertFalse(torch.equal(z[0], z[1]))


class PolicyTests(unittest.TestCase):
    def setUp(self):
        torch.manual_seed(0)
        self.router = two_route_router()
        self.folder = tempfile.TemporaryDirectory()
        self.addCleanup(self.folder.cleanup)
        self.fields = write_fields(Path(self.folder.name) / "fields", self.router)
        self.envs, _ = build_envs(self.router, self.fields, OD_PAIRS[:1], tiny_config()["env"], "train", 42)

    def actor(self, seed=0):
        return PreferenceActor(self.envs, seed, SMALL, None)

    def test_each_episode_draws_a_preference_that_reaches_env_and_sample(self):
        actor = self.actor()
        alphas = []
        for _ in range(20):
            actor.reset()
            sample = actor.sample()
            alphas.append(actor.alpha)
            self.assertEqual(actor.env.alpha, actor.alpha)
            self.assertAlmostEqual(actor.env.beta, 1 - actor.alpha)
            self.assertEqual(sample.global_x.shape, (GLOBAL_FEATURES + 2,))
            self.assertAlmostEqual(float(sample.global_x[-2]), actor.alpha, places=6)
            self.assertAlmostEqual(float(sample.global_x[-1]), 1 - actor.alpha, places=6)
        self.assertGreater(len(set(alphas)), 15)
        self.assertTrue(all(0 <= a <= 1 for a in alphas))

    def test_preference_draws_are_seeded(self):
        def draws(seed):
            actor = self.actor(seed)
            result = []
            for _ in range(5):
                actor.reset()
                result.append(actor.alpha)
            return result
        self.assertEqual(draws(3), draws(3))
        self.assertNotEqual(draws(3), draws(4))

    def test_the_return_is_the_scalarized_objective_of_the_drawn_preference(self):
        """-(alpha T/T_ref + beta E/E_ref) on the clean route: T = 1.5 T_ref, E = 0.375 E_ref."""
        actor = self.actor()
        actor.reset()
        for edge_id in CLEAN:
            action = next(i for i, arc in enumerate(self.router.arcs[actor.env.node]) if arc[2] == edge_id)
            *_, info = actor.env.step(action)
        self.assertEqual(info["outcome"], "reached")
        self.assertAlmostEqual(info["episode"]["return"], -(actor.alpha * 1.5 + (1 - actor.alpha) * 0.375))

    def test_model_handles_a_batch_of_mixed_preferences(self):
        actor, model = self.actor(), PreferenceActorCritic(SMALL)
        first = actor.sample()
        actor.reset()
        second = actor.sample()
        batch = to_tensors(collate([first, second]))
        log_probs, values = model(batch)
        self.assertEqual(values.shape, (2,))
        self.assertEqual(log_probs.shape[0], batch["action_mask"].shape[0])
        # A sample's result does not depend on what else is in the batch.
        alone, value = model(to_tensors(collate([first])))
        self.assertTrue(torch.allclose(log_probs[:alone.shape[0]], alone))
        self.assertAlmostEqual(values[0].item(), value[0].item(), places=5)

    def test_action_probabilities_depend_on_the_preference(self):
        model = PreferenceActorCritic(SMALL).eval()
        sample = self.actor().sample()
        batch = to_tensors(collate([sample._replace(global_x=sample.global_x[:GLOBAL_FEATURES])]))
        at_0, _ = BoundPreference(model, 0.0)(batch)
        at_1, _ = BoundPreference(model, 1.0)(batch)
        self.assertFalse(torch.allclose(at_0, at_1))

    def test_bound_preference_equals_a_sample_carrying_that_preference(self):
        model = PreferenceActorCritic(SMALL).eval()
        actor = self.actor()
        sample = actor.sample()
        carried, _ = model(to_tensors(collate([sample])))
        bound, _ = BoundPreference(model, actor.alpha)(
            to_tensors(collate([sample._replace(global_x=sample.global_x[:GLOBAL_FEATURES])])))
        self.assertTrue(torch.allclose(carried, bound, atol=1e-5))

    def test_regret_prior_takes_the_route_of_each_objective(self):
        """At alpha = 1 only the time regret counts, at alpha = 0 only the exposure regret: hand-known routes."""
        actor = self.actor()

        def first_edge(alpha):
            actor.alpha = alpha
            actor.reset(env=actor.env)
            batch = to_tensors(collate([actor.sample()]))
            log_probs, values = RegretPrior(SMALL)(batch)
            self.assertEqual(values.shape, (1,))
            return self.router.arcs[1][int(log_probs.argmax())][2]

        self.assertEqual(first_edge(1.0), FAST[0])
        self.assertEqual(first_edge(0.0), CLEAN[0])

    def test_invalid_preference_is_refused(self):
        model = PreferenceActorCritic(SMALL).eval()
        with self.assertRaises(ValueError):
            BoundPreference(model, 1.5)
        with self.assertRaises(ValueError):
            decode_preference_route(model, self.router, EXPOSURE, 1, 4, (0.5, 0.6))


class RegretPathTests(unittest.TestCase):
    OFF = ScalarizedConfig(hidden_dim=16, graph_layers=2, ego_hops=3, ego_max_nodes=32, preference_dim=8, fourier=2,
                           regret_path=False)

    def batch(self, model_config):
        router = two_route_router()
        with tempfile.TemporaryDirectory() as folder:
            fields = write_fields(Path(folder) / "fields", router)
            envs, _ = build_envs(router, fields, OD_PAIRS[:1], tiny_config()["env"], "train", 42)
        return to_tensors(collate([PreferenceActor(envs, 0, model_config, None).sample()]))

    def test_the_path_is_one_parameter_that_can_be_left_out(self):
        on, off = PreferenceActorCritic(SMALL), PreferenceActorCritic(self.OFF)
        self.assertEqual(set(on.state_dict()) - set(off.state_dict()), {"log_regret_scale"})
        self.assertEqual(set(off.state_dict()) - set(on.state_dict()), set())

    def test_the_path_only_subtracts_the_scaled_regret(self):
        torch.manual_seed(0)
        on = PreferenceActorCritic(SMALL).eval()
        off = PreferenceActorCritic(self.OFF).eval()
        off.load_state_dict({k: v for k, v in on.state_dict().items() if k != "log_regret_scale"})
        batch = self.batch(SMALL)
        with mock.patch("src.backend.route_model.morl.scalarized_policy.masked_log_softmax",
                        lambda logits, *rest: logits):
            with_path, _ = on(batch)
            without_path, _ = off(batch)
        self.assertTrue(torch.allclose(with_path, without_path - on.log_regret_scale.exp() * scalarized_regret(batch)))

    def test_a_run_without_the_path_trains_reloads_and_says_so(self):
        config = tiny_scalarized_config()
        config["model"]["regret_path"] = False
        router = two_route_router()
        with tempfile.TemporaryDirectory() as folder:
            fields = write_fields(Path(folder) / "fields", router)
            run = train_scalarized(config, fields, Path(folder) / "runs", router=router, od_pairs=OD_PAIRS)
            model, checkpoint = load_scalarized_policy(run.dir / "checkpoints" / "best.pt")
        self.assertFalse(model.config.regret_path)
        self.assertFalse(checkpoint["model_config"]["regret_path"])
        self.assertFalse(hasattr(model, "log_regret_scale"))

    def test_a_checkpoint_from_before_the_option_keeps_its_path(self):
        """The committed run's model_config has no regret_path key."""
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / "best.pt"
            save_checkpoint(path, PreferenceActorCritic(SMALL))
            saved = torch.load(path, weights_only=True)
            del saved["model_config"]["regret_path"]
            torch.save(saved, path)
            model, _ = load_scalarized_policy(path)
        self.assertTrue(model.config.regret_path)
        self.assertTrue(hasattr(model, "log_regret_scale"))


class CheckpointTests(unittest.TestCase):
    def test_reload_gives_identical_routes_at_every_preference(self):
        torch.manual_seed(1)
        router = two_route_router()
        model = PreferenceActorCritic(SMALL).eval()
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / "best.pt"
            save_checkpoint(path, model, run_id="r", update=0)
            first, _ = load_scalarized_policy(path)
            second, _ = load_scalarized_policy(path)
        self.assertEqual(first.config, SMALL)
        for name, value in model.state_dict().items():
            self.assertTrue(torch.equal(value, first.state_dict()[name]), name)

        def route(policy, alpha):
            # An untrained policy may walk into the dead end at node 5; that must reproduce too.
            try:
                return decode_preference_route(policy, router, EXPOSURE, 1, 4, (alpha, 1 - alpha)).edge_ids
            except PolicyFailure as failure:
                return failure.reason

        for alpha in (0.0, 0.3, 1.0):
            self.assertEqual(route(model, alpha), route(first, alpha))
            self.assertEqual(route(first, alpha), route(second, alpha))

    def test_a_policy_set_loader_refuses_a_scalarized_checkpoint(self):
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / "best.pt"
            save_checkpoint(path, PreferenceActorCritic(SMALL))
            with self.assertRaises(TypeError):
                load_policy(path)

    def test_a_multi_policy_checkpoint_is_refused(self):
        from src.backend.route_model.morl.policy import ActorCritic, ModelConfig
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / "best.pt"
            save_checkpoint(path, ActorCritic(ModelConfig(hidden_dim=16, graph_layers=2, ego_hops=3, ego_max_nodes=32)))
            with self.assertRaises(RuntimeError):
                load_scalarized_policy(path)


class TrainingTests(unittest.TestCase):
    def setUp(self):
        self.folder = tempfile.TemporaryDirectory()
        self.addCleanup(self.folder.cleanup)
        self.router = two_route_router()
        self.fields = write_fields(Path(self.folder.name) / "fields", self.router)
        self.out = Path(self.folder.name) / "runs"

    def run_training(self, config=None, **kwargs):
        return train_scalarized(config or tiny_scalarized_config(), self.fields, self.out, router=self.router,
                                od_pairs=OD_PAIRS, **kwargs)

    def manifest(self, run):
        return json.loads((run.dir / "manifest.json").read_text(encoding="utf-8"))

    def test_one_policy_learns_to_follow_the_preference(self):
        """The acceptance of the whole approach: same network, opposite preferences, opposite routes."""
        run = self.run_training(tiny_scalarized_config(warm_episodes=20, max_updates=1))
        model, _ = load_scalarized_policy(run.dir / "checkpoints" / "best.pt")
        at = lambda alpha: decode_preference_route(model, self.router, EXPOSURE, 1, 4, (alpha, 1 - alpha)).edge_ids
        self.assertEqual(at(1.0), FAST)
        self.assertEqual(at(0.0), CLEAN)

    def test_run_record_has_identity_configuration_and_data_references(self):
        run = self.run_training()
        manifest = self.manifest(run)
        self.assertEqual(manifest["status"], "completed")
        self.assertIn("tiny", manifest["run_id"])
        self.assertEqual(manifest["algorithm"], scalarized_module.ALGORITHM)
        self.assertIsNone(manifest["alpha"])
        self.assertEqual(manifest["seed"], 42)
        self.assertEqual(manifest["config"], json.loads((run.dir / "config.json").read_text(encoding="utf-8")))
        data = manifest["data"]
        self.assertEqual(data["graph_version"], self.router.version)
        self.assertTrue(data["od_pairs_sha256"] and data["fields"]["manifest_sha256"])
        for split in ("train", "val"):
            self.assertTrue(data[split]["window_sha256"])
        self.assertTrue(all("\\" not in artifact for artifact in manifest["artifacts"]))

    def test_checkpoint_selection_uses_validation_across_the_preference_grid(self):
        run = self.run_training()
        manifest = self.manifest(run)
        selection = manifest["checkpoint_selection"]
        self.assertEqual(selection["split"], "val")
        self.assertFalse(selection["test_split_loaded"])
        self.assertEqual(selection["alphas"], [0.0, 1.0])
        records = [json.loads(line) for line in (run.dir / "metrics.jsonl").read_text(encoding="utf-8").splitlines()]
        val = [r for r in records if r["phase"] == "val"]
        self.assertEqual(set(val[0]["by_alpha"]), {"0", "1"})
        self.assertEqual(manifest["best"]["val_mean_return"], max(r["mean_return"] for r in val))

    def test_test_windows_are_never_opened_and_no_test_pair_is_sampled(self):
        # write_fields names the test window but writes no file for it: reading it would raise.
        run = self.run_training()
        self.assertEqual(self.manifest(run)["status"], "completed")
        payload = json.loads((run.dir / "od_pairs.json").read_text(encoding="utf-8"))
        self.assertEqual({p["split"] for p in payload["pairs"]}, {"train", "val"})

    def test_final_report_covers_the_report_grid_and_the_reload_check(self):
        run = self.run_training()
        evaluation = json.loads((run.dir / "evaluation.json").read_text(encoding="utf-8"))
        self.assertEqual(evaluation["split"], "val")
        self.assertEqual(set(evaluation["summary"]["by_alpha"]), {"0", "0.5", "1"})
        self.assertTrue(evaluation["reload_check"]["identical"])
        self.assertEqual(evaluation["reload_check"]["routes"], 2)

    def test_time_budget_stop_is_recorded_and_the_run_resumes(self):
        run = self.run_training(tiny_scalarized_config(max_updates=4), max_seconds=0)
        manifest = self.manifest(run)
        self.assertEqual(manifest["status"], "stopped")
        self.assertIn("Time budget", manifest["stop_reason"])
        self.assertIn("checkpoints/best.pt", manifest["artifacts"])
        resumed = train_scalarized(tiny_scalarized_config(max_updates=4), self.fields, resume=run.dir,
                                   router=self.router)
        manifest = self.manifest(resumed)
        self.assertEqual(manifest["status"], "completed")
        self.assertEqual(manifest["progress"]["updates"], 4)
        self.assertEqual(manifest["resumes"][0]["previous_status"], "stopped")

    def test_divergence_is_recorded_with_what_remains(self):
        def diverge(*args, **kwargs):
            raise TrainingDiverged("Non-finite loss")
        with mock.patch("src.backend.route_model.morl.train.ppo_update", diverge):
            with self.assertRaises(TrainingDiverged):
                self.run_training()
        run_dir = next(self.out.iterdir())
        manifest = json.loads((run_dir / "manifest.json").read_text(encoding="utf-8"))
        self.assertEqual(manifest["status"], "failed")
        self.assertIn("diverged", manifest["stop_reason"])
        self.assertIn("checkpoints/best.pt", manifest["artifacts"])

    def test_configs_of_the_wrong_approach_are_refused(self):
        with self.assertRaises(ValueError):
            self.run_training(tiny_config())

    def test_shipped_configs_are_complete(self):
        folder = Path(scalarized_module.__file__).parent / "configs"
        for name in ("scalarized-bike-v1.json", "scalarized-bike-smoke.json", "scalarized-bike-mlp-only-v1.json"):
            config = json.loads((folder / name).read_text(encoding="utf-8"))
            self.assertEqual(config["algorithm"], scalarized_module.ALGORITHM)
            self.assertNotIn("alpha", config)
            self.assertEqual(Scalarized.build_model(config).config.preference_dim, config["model"]["preference_dim"])

    def test_v1_shares_od_and_environment_with_the_multi_policy_set(self):
        """TI-AI-10 compares the approaches: they must train on the same pairs under the same rules."""
        folder = Path(scalarized_module.__file__).parent / "configs"
        ours = json.loads((folder / "scalarized-bike-v1.json").read_text(encoding="utf-8"))
        theirs = json.loads((folder / "multi-policy-bike-v1.json").read_text(encoding="utf-8"))
        for key in ("od", "env", "seed", "mode", "augmentation"):
            self.assertEqual(ours[key], theirs[key], key)
        for key in ("hidden_dim", "graph_layers", "ego_hops", "ego_max_nodes"):
            self.assertEqual(ours["model"][key], theirs["model"][key], key)

    def test_the_mlp_only_config_differs_from_v1_only_by_the_regret_path(self):
        folder = Path(scalarized_module.__file__).parent / "configs"
        v1 = json.loads((folder / "scalarized-bike-v1.json").read_text(encoding="utf-8"))
        mlp_only = json.loads((folder / "scalarized-bike-mlp-only-v1.json").read_text(encoding="utf-8"))
        self.assertIs(mlp_only["model"].pop("regret_path"), False)
        del v1["name"], mlp_only["name"]
        self.assertEqual(v1, mlp_only)

    def test_verify_and_export_work_from_the_run_files_alone(self):
        run = self.run_training()
        with mock.patch.object(scalarized_module, "get_router", return_value=self.router):
            report = verify(run.dir, self.fields)
        self.assertTrue(report["checkpoint_matches_manifest"])
        self.assertTrue(report["reload"]["identical"])
        self.assertEqual(report["reload"]["routes"], 2)
        self.assertEqual(set(report["regret_prior"]["real_fields"]["by_alpha"]), {"0", "1"})
        target = export_evidence(run.dir, Path(self.folder.name) / "evidence")
        for name in ("config.json", "manifest.json", "verification.json", "curves.csv", "checkpoints/best.pt"):
            self.assertTrue((target / name).exists(), name)
        self.assertFalse((target / "checkpoints" / "last.pt").exists())
        with mock.patch.object(scalarized_module, "get_router", return_value=self.router):
            again = verify(target, self.fields)
        self.assertTrue(again["checkpoint_matches_manifest"])

    def test_verify_notices_a_changed_checkpoint(self):
        run = self.run_training()
        path = run.dir / "checkpoints" / "best.pt"
        model, checkpoint = load_scalarized_policy(path)
        with torch.no_grad():
            next(model.parameters()).add_(1.0)
        save_checkpoint(path, model)
        with mock.patch.object(scalarized_module, "get_router", return_value=self.router):
            self.assertFalse(verify(run.dir, self.fields)["checkpoint_matches_manifest"])

    def test_greedy_evaluation_is_deterministic(self):
        torch.manual_seed(0)
        model = PreferenceActorCritic(SMALL)
        envs, _ = build_envs(self.router, self.fields, OD_PAIRS[1:], tiny_config()["env"], "val", 42)
        run = lambda: evaluate(model, envs, OD_PAIRS[1:], [0.0, 0.5, 1.0], SMALL, "cpu", 4, actor_type=PreferenceActor)[0]
        self.assertEqual(run(), run())


class SummaryTests(unittest.TestCase):
    def test_summary_compares_the_policy_with_the_regret_prior(self):
        router = two_route_router()
        with tempfile.TemporaryDirectory() as folder:
            fields = write_fields(Path(folder) / "fields", router)
            run = train_scalarized(tiny_scalarized_config(), fields, Path(folder) / "runs", router=router,
                                   od_pairs=OD_PAIRS)
            self.assertNotIn("regret prior", write_summary(run.dir).read_text(encoding="utf-8"))
            with mock.patch.object(scalarized_module, "get_router", return_value=router):
                verify(run.dir, fields)
            self.assertIn("Compared with the regret prior", write_summary(run.dir).read_text(encoding="utf-8"))

    def test_summary_says_when_the_warm_start_was_selected(self):
        router = two_route_router()
        with tempfile.TemporaryDirectory() as folder:
            fields = write_fields(Path(folder) / "fields", router)
            run = train_scalarized(tiny_scalarized_config(), fields, Path(folder) / "runs", router=router,
                                   od_pairs=OD_PAIRS)
            manifest = json.loads((run.dir / "manifest.json").read_text(encoding="utf-8"))
            text = write_summary(run.dir).read_text(encoding="utf-8")
        self.assertIn(manifest["run_id"], text)
        self.assertIn("| 0.5 | 0.5 |", text)
        self.assertEqual("the selected checkpoint is the warm start" in text, manifest["best"]["update"] == 0)


class WindowsFileTests(unittest.TestCase):
    def test_rss_is_positive_and_sees_an_allocation(self):
        before = current_rss_bytes()
        self.assertGreater(before, 0)
        block = np.ones(50_000_000, dtype=np.uint8)
        self.assertGreater(current_rss_bytes(), before + 30_000_000)
        del block

    def test_replace_retries_while_the_target_is_held_open(self):
        with tempfile.TemporaryDirectory() as folder:
            temporary, target = Path(folder) / "a.tmp", Path(folder) / "a.json"
            temporary.write_text("new", encoding="utf-8")
            real, calls = Path.replace, []

            def held_twice(self, destination):
                calls.append(destination)
                if len(calls) < 3:
                    raise PermissionError(5, "Access is denied")
                return real(self, destination)

            with mock.patch.object(Path, "replace", held_twice):
                replace_file(temporary, target)
            self.assertEqual(target.read_text(encoding="utf-8"), "new")
            self.assertEqual(len(calls), 3)

    def test_replace_gives_up_eventually(self):
        def always_held(self, destination):
            raise PermissionError(5, "Access is denied")

        with tempfile.TemporaryDirectory() as folder, mock.patch.object(Path, "replace", always_held):
            with self.assertRaises(PermissionError):
                replace_file(Path(folder) / "a.tmp", Path(folder) / "a.json", attempts=2)


if __name__ == "__main__":
    unittest.main()
