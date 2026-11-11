"""TI-AI-11: package manifest, compatibility gate and route policy packages."""
import json
import re
import tempfile
import unittest
from pathlib import Path

import torch

from src.backend.route_model.morl.features import FEATURE_VERSION
from src.backend.route_model.morl.infer import load_policy_set, save_checkpoint
from src.backend.route_model.morl.packaging import (
    load_packaged_policy_set, package_policy_set, runtime_contract,
)
from src.backend.route_model.morl.policy import ActorCritic
from src.backend.route_model.model_package import (
    PACKAGE_FILE, PackageError, PackageIncompatible, build_package, check_compatibility, require_compatible,
    scan_for_secrets, sha256_file, validate_package,
)

REPO = Path(__file__).resolve().parents[3]
CONTRACT = {"data_contracts_version": "1.0.0", "graph_version": "jakarta-20261005"}


def package(root, **overrides):
    fields = dict(model_kind="route_policy", model_id="m", model_version="1", contract=CONTRACT, lineage={},
                  environment={}, inference={}, evaluation={})
    fields.update(overrides)
    return build_package(root, **fields)


def make_package(root, **overrides):
    (root / "model.bin").write_bytes(b"weights")
    (root / "sub").mkdir()
    (root / "sub" / "notes.txt").write_text("plain notes")
    return package(root, **overrides)


class PackageManifestTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        self.addCleanup(self.tmp.cleanup)

    def test_round_trip_lists_every_file_hash(self):
        manifest = make_package(self.root)
        self.assertEqual(set(manifest["artifacts"]), {"model.bin", "sub/notes.txt"})
        self.assertEqual(validate_package(self.root, "route_policy")["model_id"], "m")

    def test_changed_file_is_rejected(self):
        make_package(self.root)
        (self.root / "model.bin").write_bytes(b"other")
        with self.assertRaisesRegex(PackageError, "changed: model.bin"):
            validate_package(self.root)

    def test_missing_unlisted_and_wrong_kind_are_rejected(self):
        make_package(self.root)
        (self.root / "extra.json").write_text("{}")
        with self.assertRaisesRegex(PackageError, "does not list: extra.json"):
            validate_package(self.root)
        (self.root / "extra.json").unlink()
        with self.assertRaisesRegex(PackageError, "kind mismatch"):
            validate_package(self.root, "spatial_downscaling")
        (self.root / "model.bin").unlink()
        with self.assertRaisesRegex(PackageError, "missing"):
            validate_package(self.root)

    def test_path_outside_the_package_is_rejected(self):
        make_package(self.root)
        manifest = json.loads((self.root / PACKAGE_FILE).read_text())
        manifest["artifacts"]["../outside.txt"] = "0" * 64
        (self.root / PACKAGE_FILE).write_text(json.dumps(manifest))
        with self.assertRaisesRegex(PackageError, "outside"):
            validate_package(self.root)

    def test_unknown_format_and_missing_fields_are_rejected(self):
        make_package(self.root)
        manifest = json.loads((self.root / PACKAGE_FILE).read_text())
        (self.root / PACKAGE_FILE).write_text(json.dumps({**manifest, "package_format": "model-package-9"}))
        with self.assertRaisesRegex(PackageError, "Unsupported package format"):
            validate_package(self.root)
        del manifest["lineage"]
        (self.root / PACKAGE_FILE).write_text(json.dumps(manifest))
        with self.assertRaisesRegex(PackageError, "lacks: lineage"):
            validate_package(self.root)

    def test_credentials_stop_the_build_and_the_validation(self):
        (self.root / "model.bin").write_bytes(b"w")
        cases = {"config.json": '{"AWS_SECRET_ACCESS_KEY": "abcdef123456"}', "notes.txt": "key AKIAABCDEFGHIJKLMNOP",
                 "db.toml": 'url = "postgresql://user:hunter2@host:5432/db"', ".env": "X=1",
                 "k.txt": "-----BEGIN PRIVATE KEY-----"}
        for name, text in cases.items():
            (self.root / name).write_text(text)
            self.assertTrue(scan_for_secrets(self.root), name)
            with self.assertRaisesRegex(PackageError, "credentials"):
                package(self.root)
            (self.root / name).unlink()
        self.assertEqual(scan_for_secrets(self.root), [])
        package(self.root)
        (self.root / "late.json").write_text('{"password": "hunter2hunter2"}')
        with self.assertRaisesRegex(PackageError, "credentials|does not list"):
            validate_package(self.root)

    def test_findings_never_contain_the_secret(self):
        (self.root / "a.json").write_text('{"password": "s3cr3t-value"}')
        self.assertNotIn("s3cr3t", " ".join(scan_for_secrets(self.root)))

    def test_contract_must_be_complete(self):
        (self.root / "model.bin").write_bytes(b"w")
        for contract in ({"data_contracts_version": "1.0.0"}, {"data_contracts_version": "one", "graph_version": "g"}):
            with self.assertRaisesRegex(PackageError, "contract"):
                package(self.root, contract=contract)  # a route policy needs its graph version
        manifest = package(self.root, model_kind="spatial_downscaling", contract={"data_contracts_version": "1.0.0"})
        self.assertNotIn("graph_version", manifest["contract"])


class CompatibilityTest(unittest.TestCase):
    manifest = {"model_kind": "route_policy", "contract": dict(CONTRACT, grid_spec_id="g1"),
                "lineage": {"upstream_model": {"bundle_sha256": "a"}}}

    def check(self, **runtime):
        return check_compatibility(self.manifest, {**CONTRACT, **runtime})

    def test_same_contract_is_compatible(self):
        self.assertEqual(self.check().status, "compatible")

    def test_other_major_or_graph_is_rejected(self):
        self.assertEqual(self.check(data_contracts_version="2.0.0").status, "rejected")
        self.assertEqual(self.check(graph_version="jakarta-20270101").status, "rejected")
        self.assertEqual(self.check(data_contracts_version="x").status, "rejected")
        with self.assertRaises(PackageIncompatible) as caught:
            require_compatible(self.manifest, {**CONTRACT, "graph_version": "other"})
        self.assertIn("Graph version differs", str(caught.exception))

    def test_a_spatial_model_is_not_bound_to_the_road_graph(self):
        spatial = {"model_kind": "spatial_downscaling", "lineage": {},
                   "contract": {"data_contracts_version": "1.0.0", "grid_spec_id": "g1"}}
        for graph in ("jakarta-20261005", "jakarta-20270101", None):
            runtime = {"data_contracts_version": "1.0.0", "grid_spec_id": "g1"}
            if graph:
                runtime["graph_version"] = graph
            self.assertEqual(check_compatibility(spatial, runtime).status, "compatible", graph)
        self.assertEqual(check_compatibility(spatial, {"data_contracts_version": "2.0.0"}).status, "rejected")
        self.assertEqual(check_compatibility(spatial, {"data_contracts_version": "1.0.0", "grid_spec_id": "g2"}).status,
                         "flagged")

    def test_minor_drift_grid_and_upstream_are_flagged_not_rejected(self):
        for change in ({"data_contracts_version": "1.1.0"}, {"grid_spec_id": "g2"},
                       {"upstream_model": {"bundle_sha256": "b"}}):
            self.assertEqual(self.check(**change).status, "flagged", change)
            self.assertEqual(require_compatible(self.manifest, {**CONTRACT, **change}).status, "flagged")

    def test_rejection_outranks_a_flag(self):
        result = self.check(data_contracts_version="2.0.0", graph_version="other")
        self.assertEqual(result.status, "rejected")
        self.assertEqual(len(result.reasons), 2)


class RepositoryConsistencyTest(unittest.TestCase):
    def test_the_two_copies_are_identical(self):
        backend = REPO / "src/backend/route_model/model_package.py"
        worker = REPO / "src/data_worker/spatial_model/artifacts/model_package.py"
        self.assertEqual(backend.read_bytes().replace(b"\r\n", b"\n"), worker.read_bytes().replace(b"\r\n", b"\n"))

    def test_worker_contract_version_equals_the_backend_contract(self):
        pattern = re.compile(r'^(?:CONTRACT_VERSION|DATA_CONTRACTS_VERSION) = "([^"]+)"', re.M)
        backend = pattern.search((REPO / "src/backend/public_api/data_contracts.py").read_text(encoding="utf-8"))
        worker = pattern.search((REPO / "src/data_worker/spatial_model/artifacts/packaging.py").read_text(encoding="utf-8"))
        self.assertEqual(backend.group(1), worker.group(1))


def make_set(root, graph="jakarta-20261005", status="completed", feature_version=FEATURE_VERSION, member_graph=None):
    members = []
    for index, alpha in enumerate((0.0, 1.0)):
        run = f"members/run-a{alpha:g}"
        directory = root / run
        (directory / "checkpoints").mkdir(parents=True, exist_ok=True)
        torch.manual_seed(index)
        save_checkpoint(directory / "checkpoints/best.pt", ActorCritic())
        (directory / "config.json").write_text("{}")
        (directory / "manifest.json").write_text(json.dumps({
            "data": {"graph_version": member_graph or graph, "fields": {"dataset": {"version": "ds-v0.3.0"}}},
            "versions": {"python": "3.14.3", "torch": "2.15.0"}}))
        members.append({"alpha": alpha, "run_dir": run, "status": "completed",
                        "best": {"checkpoint": "checkpoints/best.pt", "sha256": sha256_file(directory / "checkpoints/best.pt")}})
    (root / "set_manifest.json").write_text(json.dumps({
        "set_id": "set-1", "status": status, "feature_version": feature_version, "algorithm": "ppo",
        "selection_rule": "nearest alpha", "config_sha256": "c", "config": {}, "code": {"commit": "abc", "dirty": False},
        "members": members, "data": {"graph_version": graph, "mode": "bike", "od_pairs_sha256": "o"}, "evaluation": {}}))
    (root / "od_pairs.json").write_text("{}")


class RoutePackageTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.set_dir, self.out = Path(self.tmp.name) / "set", Path(self.tmp.name) / "package"
        self.set_dir.mkdir()

    def build(self):
        return package_policy_set(self.set_dir, self.out, data_contracts_version="1.0.0")

    def test_same_set_with_fixed_created_at_gives_the_same_package(self):
        make_set(self.set_dir)
        stamp = "2026-11-02T16:31:34+00:00"
        first = package_policy_set(self.set_dir, self.out, data_contracts_version="1.0.0", created_at=stamp)
        second_dir = Path(self.tmp.name) / "again"
        package_policy_set(self.set_dir, second_dir, data_contracts_version="1.0.0", created_at=stamp)
        self.assertEqual(first["created_at"], stamp)
        self.assertEqual((self.out / PACKAGE_FILE).read_bytes(), (second_dir / PACKAGE_FILE).read_bytes())

    def test_package_reloads_to_identical_weights(self):
        make_set(self.set_dir)
        self.build()
        original = load_policy_set(self.set_dir)
        packaged, manifest, compatibility = load_packaged_policy_set(
            self.out, runtime_contract("jakarta-20261005", "1.0.0"))
        self.assertEqual(compatibility.status, "compatible")
        self.assertEqual(manifest["model_kind"], "route_policy")
        self.assertEqual(sorted(packaged), [0.0, 1.0])
        for alpha, model in original.items():
            for name, value in model.state_dict().items():
                self.assertTrue(torch.equal(value, packaged[alpha].state_dict()[name]))

    def test_the_package_names_its_loader_family_and_takes_the_decision_as_given(self):
        make_set(self.set_dir)
        manifest = package_policy_set(self.set_dir, self.out, data_contracts_version="1.0.0")
        self.assertEqual(manifest["inference"]["entrypoint"], "route_model.morl.packaging.load_packaged_policy_set")
        self.assertEqual(manifest["inference"]["policy_family"], "fixed_preference_set")
        self.assertIsNone(manifest["evaluation"]["decision"])
        second = Path(self.tmp.name) / "second"
        manifest = package_policy_set(self.set_dir, second, data_contracts_version="1.0.0", decision="candidate: x")
        self.assertEqual(manifest["evaluation"]["decision"], "candidate: x")

    def test_only_selected_checkpoints_are_packaged(self):
        make_set(self.set_dir)
        (self.set_dir / "members/run-a0/checkpoints/last.pt").write_bytes(b"x")
        (self.set_dir / "members/run-a0/metrics.jsonl").write_text("{}")
        manifest = self.build()
        self.assertFalse([name for name in manifest["artifacts"] if "last.pt" in name or "metrics" in name])

    def test_wrong_graph_is_rejected_and_other_minor_is_flagged(self):
        make_set(self.set_dir)
        self.build()
        with self.assertRaisesRegex(PackageIncompatible, "Graph version"):
            load_packaged_policy_set(self.out, runtime_contract("jakarta-other", "1.0.0"))
        *_, compatibility = load_packaged_policy_set(self.out, runtime_contract("jakarta-20261005", "1.3.0"))
        self.assertEqual(compatibility.status, "flagged")

    def test_altered_checkpoint_is_rejected(self):
        make_set(self.set_dir)
        self.build()
        (self.out / "members/run-a0/checkpoints/best.pt").write_bytes(b"tampered")
        with self.assertRaisesRegex(PackageError, "changed"):
            load_packaged_policy_set(self.out, runtime_contract("jakarta-20261005", "1.0.0"))

    def test_unfinished_set_wrong_features_and_mixed_graphs_are_refused(self):
        make_set(self.set_dir, status="stopped")
        with self.assertRaisesRegex(PackageError, "not completed"):
            self.build()
        make_set(self.set_dir, feature_version="ego-0")
        with self.assertRaisesRegex(PackageError, "features"):
            self.build()
        make_set(self.set_dir, member_graph="jakarta-old")
        with self.assertRaisesRegex(PackageError, "trained on graph"):
            self.build()

    def test_non_empty_target_is_refused(self):
        make_set(self.set_dir)
        self.out.mkdir()
        (self.out / "x").write_text("x")
        with self.assertRaisesRegex(PackageError, "not empty"):
            self.build()

    def test_recorded_checkpoint_hash_must_match(self):
        make_set(self.set_dir)
        (self.set_dir / "members/run-a1/checkpoints/best.pt").write_bytes(b"swapped")
        with self.assertRaisesRegex(PackageError, "SHA-256"):
            self.build()


if __name__ == "__main__":
    unittest.main()
