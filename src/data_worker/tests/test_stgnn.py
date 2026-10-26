"""Behavioural invariants retained from TI-AI-05 for the correction model."""
from dataclasses import replace
import numpy as np
import pytest
import torch
from spatial_model.models.stgnn.model import ModelConfig, STGNN, batch_edges
from spatial_model.models.stgnn.preprocessing import LOCAL_COLUMNS, TEMPORAL_COLUMNS, Preprocessor

@pytest.fixture(autouse=True)
def cpu_threads():
    old = torch.get_num_threads()
    torch.set_num_threads(2)
    yield
    torch.set_num_threads(old)


def chain(nodes):
    forward = torch.stack([torch.arange(nodes - 1), torch.arange(1, nodes)])
    return torch.cat([forward, forward.flip(0)], dim=1)


def prepared(nodes=5):
    rng = np.random.default_rng(3)
    temporal = np.abs(rng.normal(size=(2, nodes, 24, len(TEMPORAL_COLUMNS)))).astype("float32")
    local = np.abs(rng.normal(size=(2, nodes, len(LOCAL_COLUMNS)))).astype("float32")
    p = Preprocessor().fit(temporal, local, np.array([10., 20., 30.]))
    t, s = p.transform(temporal, local)
    return p, temporal, local, torch.from_numpy(t), torch.from_numpy(s)


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
    x = torch.randn(1, 3, 24, 58)
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
