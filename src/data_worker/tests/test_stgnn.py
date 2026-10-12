"""Behavioural checks for causal, inductive ST-GNN estimation and fold-safe inputs."""

from dataclasses import replace
import json
from pathlib import Path

import numpy as np
import pandas as pd
import pytest
import torch

from spatial_model.stgnn.dataset import SnapshotData
from spatial_model.stgnn.model import ModelConfig, STGNN, batch_edges
from spatial_model.stgnn.preprocessing import LOCAL_COLUMNS, TEMPORAL_COLUMNS, Preprocessor
from spatial_model.stgnn.train import load_checkpoint, predict_raw, save_checkpoint, supervised
from spatial_model.stgnn.train import TrainingConfig, evaluate_checkpoint, train

SNAPSHOT = Path(__file__).resolve().parents[3] / "docs/experiments/ds-v0.1.0"


@pytest.fixture(autouse=True)
def cpu_threads():
    old = torch.get_num_threads()
    torch.set_num_threads(2)
    yield
    torch.set_num_threads(old)


def raw_inputs(nodes=5, hours=24):
    rng = np.random.default_rng(3)
    return (np.abs(rng.normal(size=(2, nodes, hours, 16))).astype("float32"),
            np.abs(rng.normal(size=(2, nodes, 5))).astype("float32"))


def chain(nodes):
    forward = torch.stack([torch.arange(nodes - 1), torch.arange(1, nodes)])
    return torch.cat([forward, forward.flip(0)], dim=1)


def prepared(nodes=5):
    temporal, local = raw_inputs(nodes)
    p = Preprocessor().fit(temporal, local, np.array([10.0, 20.0, 30.0]))
    t, s = p.transform(temporal, local)
    return p, temporal, local, torch.from_numpy(t), torch.from_numpy(s)


def test_missing_masks_wind_wrap_and_train_fitted_scaling():
    t, s = raw_inputs()
    t[..., 0] = np.nan
    t[..., 5] = 0.0
    t[1, ..., 5] = 360.0
    s[..., 0] = np.nan
    p = Preprocessor().fit(t[:1], s[:1], np.array([10.0, 30.0]))
    dynamic, static = p.transform(t, s)
    assert dynamic.shape == (2, 5, 24, 34) and static.shape == (2, 5, 10)
    assert np.isfinite(dynamic).all() and np.isfinite(static).all()
    assert (dynamic[..., 0] == 0).all() and (dynamic[..., 17] == 0).all()
    assert (static[..., 0] == 0).all() and (static[..., 5] == 0).all()
    np.testing.assert_allclose(dynamic[0, ..., 5:7], dynamic[1, ..., 5:7], atol=1e-5)
    state = p.state()
    p.transform(t * 100, s * 100)
    assert p.state() == state
    with pytest.raises(ValueError, match="order"):
        p.transform(t, s, temporal_columns=list(reversed(TEMPORAL_COLUMNS)))
    s[..., -1] = -1
    with pytest.raises(ValueError, match="Negative"):
        p.transform(t, s)


@pytest.mark.parametrize("use_graph", [True, False])
def test_variable_nodes_finite_outputs_and_gradients(use_graph):
    model = STGNN(replace(ModelConfig(), dropout=0.0, use_graph=use_graph))
    for nodes in (1, 5):
        _, _, _, t, s = prepared(nodes)
        result = model(t, s, chain(nodes))
        assert result.shape == (2, nodes, 1) and torch.isfinite(result).all()
        result.square().mean().backward()
    assert model.temporal.projection.weight.grad.abs().sum() > 0
    assert model.local[0].weight.grad.abs().sum() > 0


def test_temporal_encoder_is_causal():
    model = STGNN().eval()
    x = torch.randn(1, 3, 24, 34)
    changed = x.clone()
    changed[:, :, 12:] += 100
    with torch.no_grad():
        before, after = model.temporal(x), model.temporal(changed)
    torch.testing.assert_close(before[:, :, :12], after[:, :, :12])
    assert not torch.allclose(before[:, :, -1], after[:, :, -1])


def test_graph_batching_has_no_cross_hour_edges_and_one_self_loop():
    edges = torch.tensor([[0, 0, 0, 1], [0, 1, 1, 0]], dtype=torch.long)
    result = batch_edges(edges, 3, 2)
    assert torch.equal(result[0] // 3, result[1] // 3)
    loops = result[:, result[0] == result[1]]
    assert sorted(loops[0].tolist()) == list(range(6))
    assert torch.unique(result, dim=1).shape == result.shape
    with pytest.raises(ValueError, match="outside"):
        batch_edges(torch.tensor([[0], [3]]), 3, 1)


def test_node_permutation_and_batched_prediction_match_independent_graphs():
    _, _, _, t, s = prepared()
    model = STGNN().eval()
    edges = chain(5)
    permutation = torch.tensor([3, 0, 4, 1, 2])
    old_to_new = torch.argsort(permutation)
    with torch.no_grad():
        expected = model(t, s, edges)
        permuted = model(t[:, permutation], s[:, permutation], old_to_new[edges])
        independent = torch.cat([model(t[i:i+1], s[i:i+1], edges) for i in range(2)])
    torch.testing.assert_close(permuted, expected[:, permutation], atol=1e-6, rtol=1e-5)
    torch.testing.assert_close(independent, expected, atol=1e-6, rtol=1e-5)


def test_one_hop_halo_matches_full_graph_interior():
    _, _, _, t, s = prepared()
    model = STGNN().eval()
    with torch.no_grad():
        full = model(t, s, chain(5))
        tile = model(t[:, 1:4], s[:, 1:4], chain(3))
    torch.testing.assert_close(tile[:, 1], full[:, 2], atol=1e-6, rtol=1e-5)


def test_safe_checkpoint_reload_and_raw_prediction(tmp_path):
    p, t_raw, s_raw, t, s = prepared()
    model = STGNN().eval()
    save_checkpoint(tmp_path / "model.pt", model, p, dict(target="pm25"))
    restored, fitted, meta = load_checkpoint(tmp_path / "model.pt")
    assert meta["target"] == "pm25" and fitted.state() == p.state()
    with torch.no_grad():
        expected = p.inverse_target(model(t, s, chain(5)).numpy())
    got = predict_raw(restored, fitted, t_raw, s_raw, chain(5),
                      temporal_columns=TEMPORAL_COLUMNS, local_columns=LOCAL_COLUMNS)
    np.testing.assert_allclose(got, expected, rtol=1e-6, atol=1e-6)
    assert (got >= 0).all()


@pytest.fixture(scope="module")
def fixed_data():
    return SnapshotData(SNAPSHOT)


def test_snapshot_adapter_filters_protocol_and_fits_only_training(fixed_data):
    data = fixed_data
    p = data.fit_preprocessor()
    train = data.keys["train"]
    assert p.target_mean == pytest.approx(train.value_ugm3.mean())
    assert set(data.sensors.station_split) == {"train"}
    assert pd.Timestamp(data.fit_times[-1]) < data.keys["val"].time_utc.min()
    hours = data.hours("train")[:2]
    batch = data.batch(hours, "train", p)
    assert batch["temporal"].shape == (2, 1044, 24, 34)
    allowed = set(zip(train.station_uuid, train.time_utc))
    assert set(zip(batch["labels"].station_uuid, batch["labels"].time_utc)) <= allowed
    model = STGNN()
    prediction = supervised(model, batch)
    assert len(prediction) == len(batch["labels"])
    loss = ((prediction - p.target(batch["label_values"])) ** 2).mean()
    loss.backward()
    assert torch.isfinite(loss)


@pytest.mark.parametrize("target", ["pm25", "no2"])
def test_loso_excludes_held_out_group_from_training_and_sensor_features(target):
    data = SnapshotData(SNAPSHOT, target=target, protocol="loso", fold=0)
    held = data.stations[data.stations.group_id == data.held_out_group]
    assert not set(data.sensors.uuid) & set(held.uuid)
    assert not set(data.keys["train"].station_uuid) & set(held.uuid)
    assert not set(data.keys["val"].station_uuid) & set(held.uuid)
    assert set(data.keys["test"].station_uuid) <= set(held.uuid)
    assert not set(data.training_nodes) & set(held.node_index)
    saved = pd.read_parquet(SNAPSHOT / "splits/loso_sensor_distance.parquet")
    expected = saved[saved.held_out_group == data.held_out_group].sort_values("node_index")
    np.testing.assert_allclose(data.local[0, :, -1], expected.dist_nearest_sensor_m)
    p = data.fit_preprocessor()
    assert p.target_mean == pytest.approx(data.keys["train"].value_ugm3.mean())


def test_no2_cannot_accidentally_use_pm25_fixed_split():
    with pytest.raises(ValueError, match="PM2.5"):
        SnapshotData(SNAPSHOT, target="no2")


def test_training_and_explicit_evaluation_on_small_frozen_snapshot(tmp_path):
    from feature_matrix.prepare_stgnn import stgnn_hash
    from feature_matrix.grid import to_lonlat

    root = tmp_path / "snapshot"
    (root / "stgnn").mkdir(parents=True)
    (root / "splits").mkdir()
    nodes = pd.DataFrame(dict(node_index=[0, 1, 2], grid_id=["r0000_c0000", "r0010_c0010", "r0020_c0020"],
                              easting=[700000., 701000., 702000.], northing=[9300000.] * 3))
    lon, lat = to_lonlat(nodes.easting, nodes.northing)
    nodes["longitude"], nodes["latitude"] = lon, lat
    stations = pd.DataFrame(dict(uuid=["a", "b", "c"], group_id=["ga", "gb", "gc"],
                                 grid_id=nodes.grid_id, node_index=nodes.node_index,
                                 station_split=["train", "val", "test"], lng=lon, lat=lat))
    times = pd.date_range("2026-01-01", periods=77, freq="h", tz="UTC")
    columns = [*TEMPORAL_COLUMNS, *LOCAL_COLUMNS]
    features = pd.DataFrame(np.ones((len(times) * 3, len(columns)), dtype="float32"), columns=columns)
    features["time_utc"] = np.repeat(times, 3)
    features["node_index"] = np.tile(np.arange(3), len(times))
    features["grid_id"] = np.tile(nodes.grid_id, len(times))
    labels = pd.DataFrame(dict(station_uuid=["a", "a", "b", "c"], group_id=["ga", "ga", "gb", "gc"],
                               time_utc=times[[23, 24, 50, 76]], node_index=[0, 0, 1, 2],
                               period=["train", "train", "val", "test"], pm25_ugm3=[10., 12., 30., 1000.]))
    manifest = dict(status="complete", horizon_hours=0, window=24, start_utc=str(times[0]),
                    end_utc_exclusive=str(times[-1] + pd.Timedelta(hours=1)))
    (root / "stgnn/manifest.json").write_text(json.dumps(manifest), encoding="utf-8")
    (root / "split_spec.json").write_text("{}", encoding="utf-8")
    frames = {"stgnn/nodes.parquet": nodes, "stations.parquet": stations,
              "stgnn/features.parquet": features, "labels.parquet": labels,
              "stgnn/edges.parquet": pd.DataFrame({c: pd.Series(dtype="int64")
                                                  for c in ("source_node", "target_node")}),
              "splits/loso_folds.parquet": pd.DataFrame(dict(target=["pm25"], fold=[0], group_id=["ga"]))}
    for split in ("train", "val", "test"):
        keys = labels[labels.period == split].rename(columns={"pm25_ugm3": "value_ugm3"})
        frames[f"splits/{split}.parquet"] = keys.assign(target="pm25", protocol="fixed_split")
    for name, frame in frames.items():
        frame.to_parquet(root / name, index=False)
    checksums = [f"{stgnn_hash(p)}  {p.relative_to(root).as_posix()}" for p in root.rglob("*") if p.is_file()]
    (root / "CHECKSUMS.sha256").write_text("\n".join(checksums), encoding="utf-8")

    out = tmp_path / "run"
    metadata = train(root, out, training_config=TrainingConfig(epochs=2, batch_size=1))
    assert metadata["epochs_run"] == 2
    assert metadata["validation"]["pooled"]["n"] == 1
    _, preprocessing, _ = load_checkpoint(out / "best.pt")
    assert preprocessing.target_mean == 11.0  # Validation/test values never enter target fitting.
    assert not (out / "test_predictions.parquet").exists()
    result = evaluate_checkpoint(out / "best.pt", root, tmp_path / "test")
    assert result["pooled"]["n"] == 1
    with pytest.raises(FileExistsError):
        train(root, out, training_config=TrainingConfig(epochs=1))
