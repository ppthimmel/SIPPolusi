"""TI-AI-11: packaging a spatial model bundle and gating it against the Worker's contract."""
import json
from pathlib import Path

import pytest

from spatial_model.artifacts.bundles import digest
from spatial_model.artifacts.model_package import PACKAGE_FILE, PackageError, PackageIncompatible, validate_package
from spatial_model.artifacts.packaging import DATA_CONTRACTS_VERSION, package_bundle, runtime_contract, verify_package
from spatial_model.inference.serving import check_package



@pytest.fixture
def bundle(tmp_path):
    root = tmp_path / "bundle"
    root.mkdir()
    (root / "stations.parquet").write_bytes(b"stations")
    (root / "model.txt").write_text("tree model")
    (root / "metadata.json").write_text(json.dumps({"columns": ["a"]}))
    (root / "metrics.json").write_text(json.dumps({"test": {"rmse": 27.4, "r2": 0.7, "note": "x"}}))
    hashes = {p.name: digest(p) for p in root.iterdir()}
    (root / "bundle.json").write_text(json.dumps({
        "format_version": 1, "model_family": "lightgbm", "target": "no2", "dataset": "ds-v0.3.0", "unit": "ug/m3",
        "metadata": {}, "snapshot_hashes": {"observations.parquet": "s" * 64}, "code_revision": "abc123",
        "python": "3.11.9", "config_hashes": {"lightgbm.json": "c" * 64},
        "libraries": {"lightgbm": "4.5.0"}, "source_hashes": {}, "hashes": hashes}))
    return root


def test_package_wraps_a_valid_bundle(bundle):
    manifest = package_bundle(bundle, grid_spec_id="g1")
    assert manifest["model_kind"] == "spatial_downscaling" and manifest["model_id"] == "spatial-downscaling-no2"
    assert manifest["contract"] == {"data_contracts_version": DATA_CONTRACTS_VERSION,
                                    "grid_spec_id": "g1", "feature_matrix_version": "ds-v0.3.0"}
    assert manifest["lineage"]["code_revision"] == "abc123" and manifest["lineage"]["upstream_model"] is None
    assert manifest["evaluation"]["headline_metrics"] == {"test": {"rmse": 27.4, "r2": 0.7}}
    assert "bundle.json" in manifest["artifacts"] and PACKAGE_FILE not in manifest["artifacts"]
    assert verify_package(bundle, "no2")["model_version"] == manifest["model_version"]


def test_bundle_still_validates_with_a_package_inside(bundle):
    from spatial_model.artifacts.bundles import validate_bundle
    package_bundle(bundle)
    assert validate_bundle(bundle, "no2")["target"] == "no2"


def test_altered_or_extra_file_fails_verification(bundle):
    package_bundle(bundle)
    (bundle / "model.txt").write_text("swapped")
    with pytest.raises(ValueError, match="integrity failure: model.txt"):
        verify_package(bundle)
    (bundle / "model.txt").write_text("tree model")
    verify_package(bundle)
    (bundle / "extra.csv").write_text("a")
    with pytest.raises(PackageError, match="does not list: extra.csv"):
        verify_package(bundle)


def test_an_invalid_bundle_is_not_packaged(bundle):
    (bundle / "model.txt").write_text("swapped")
    with pytest.raises(ValueError, match="integrity"):
        package_bundle(bundle)
    assert not (bundle / PACKAGE_FILE).exists()


def test_credentials_in_the_bundle_block_packaging(bundle):
    (bundle / "metadata.json").write_text(json.dumps({"columns": ["a"], "AWS_SECRET_ACCESS_KEY": "abcdef123456"}))
    hashes = json.loads((bundle / "bundle.json").read_text())
    hashes["hashes"]["metadata.json"] = digest(bundle / "metadata.json")
    (bundle / "bundle.json").write_text(json.dumps(hashes))
    with pytest.raises(PackageError, match="credentials"):
        package_bundle(bundle)


def test_check_package_compatible_flagged_and_rejected(bundle):
    package_bundle(bundle, grid_spec_id="g1")
    assert check_package(bundle, runtime_contract()).status == "compatible"
    assert check_package(bundle).status == "compatible"
    minor = {**runtime_contract(), "data_contracts_version": "1.9.0"}
    assert check_package(bundle, minor).status == "flagged"
    assert check_package(bundle, {**runtime_contract(), "grid_spec_id": "g2"}).status == "flagged"
    with pytest.raises(PackageIncompatible, match="major version"):
        check_package(bundle, {**runtime_contract(), "data_contracts_version": "2.0.0"})


def test_a_spatial_package_loads_on_a_different_road_graph(bundle):
    """The spatial model predicts per grid cell; a new OSM graph must not take it out of service."""
    package_bundle(bundle)
    for graph in ("jakarta-20261005", "jakarta-20270101"):
        runtime = {**runtime_contract(), "graph_version": graph}
        assert check_package(bundle, runtime).status == "compatible"


def test_bundle_without_package_loads_flagged_as_unpackaged(bundle):
    result = check_package(bundle, runtime_contract())
    assert result.status == "flagged" and "unpackaged" in result.reasons[0]


def test_incompatible_package_falls_back_to_idw_with_a_clear_reason(bundle, monkeypatch):
    import numpy as np
    import pandas as pd
    from types import SimpleNamespace
    from contracts import TimeWindow
    from spatial_model.data.grid import GridSpec
    from spatial_model.inference import serving
    from spatial_model.inference.runtime import infer_registered

    package_bundle(bundle, grid_spec_id="not-this-grid")
    spec = GridSpec.from_bbox((106.8, -6.2, 106.801, -6.199))
    gt = pd.DataFrame(dict(station_id=["s", "s"], lon=[106.8] * 2, lat=[-6.2] * 2, pollutant=["pm25", "no2"], value=[10., 20.]))
    start = pd.Timestamp("2026-09-29T12:00Z").to_pydatetime()
    window = TimeWindow(start, start + pd.Timedelta(hours=1))
    inputs = SimpleNamespace(readings=pd.DataFrame({"x": [1]}), fingerprint="f")
    monkeypatch.setattr(serving, "predict_grid", lambda *args: np.full(spec.n_cells, 12.))
    monkeypatch.setattr(serving, "validate_bundle", lambda *args: {"target": "no2"})

    def run(runtime):
        return infer_registered(window, gt, spec, {"power": 2, "neighbors": 8}, inputs=inputs,
                                resolver=lambda target, selector: (bundle, f"{target}:1"), runtime_contract=runtime)[1]

    meta = run({"data_contracts_version": "2.0.0"})
    assert meta["no2"]["source"] == "idw"
    assert meta["no2"]["fallback_reason"] == "load_bundle:PackageIncompatible"
    assert meta["no2"]["requested_model_version"] == "no2:1"
