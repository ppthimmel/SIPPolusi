"""The Actor-Critic of one member of the multi-policy set (PIC B of TI-AI-09).

Each member optimises one fixed (alpha, beta), so the network takes no preference
input: the preference is what its reward was weighted by. GraphEncoder produces the
representation; the Actor scores the outgoing arcs of the current node, with invalid
arcs masked; the Critic estimates the state's value under that member's reward.
Plain PyTorch: batches are disjoint graphs from features.collate.
"""
from __future__ import annotations

from dataclasses import asdict, dataclass

import torch
from torch import nn

from .features import EDGE_FEATURES, GLOBAL_FEATURES, NODE_FEATURES


@dataclass(frozen=True)
class ModelConfig:
    hidden_dim: int = 64
    graph_layers: int = 3
    ego_hops: int = 4
    ego_max_nodes: int = 64

    def __post_init__(self):
        if self.graph_layers < 1 or self.ego_hops < 1:
            raise ValueError("graph_layers and ego_hops must be at least 1")

    def as_dict(self):
        return asdict(self)


def _mlp(in_dim, hidden, out_dim):
    return nn.Sequential(nn.Linear(in_dim, hidden), nn.ReLU(), nn.Linear(hidden, out_dim))


class GraphLayer(nn.Module):
    """One round of edge-conditioned message passing.

    Messages run against the arc direction (v to u along u->v), so a node's state
    summarises the road ahead of it.
    """

    def __init__(self, hidden):
        super().__init__()
        self.message = _mlp(hidden + EDGE_FEATURES, hidden, hidden)
        self.update = _mlp(2 * hidden, hidden, hidden)
        self.norm = nn.LayerNorm(hidden)

    def forward(self, h, edge_index, edge_x):
        source, target = edge_index
        messages = self.message(torch.cat([h[target], edge_x], dim=-1))
        total = torch.zeros_like(h).index_add_(0, source, messages)
        counts = torch.zeros(h.size(0), device=h.device, dtype=h.dtype).index_add_(
            0, source, torch.ones(source.size(0), device=h.device, dtype=h.dtype))
        mean = total / counts.clamp(min=1).unsqueeze(-1)
        return self.norm(h + self.update(torch.cat([h, mean], dim=-1)))


class GraphEncoder(nn.Module):
    def __init__(self, config: ModelConfig):
        super().__init__()
        self.embed = nn.Linear(NODE_FEATURES, config.hidden_dim)
        self.layers = nn.ModuleList(GraphLayer(config.hidden_dim) for _ in range(config.graph_layers))

    def forward(self, node_x, edge_index, edge_x):
        h = torch.relu(self.embed(node_x))
        for layer in self.layers:
            h = layer(h, edge_index, edge_x)
        return h


def masked_log_softmax(logits, mask, batch, size):
    """log-softmax of each sample's actions; illegal actions get -inf.

    A sample with no legal action cannot occur: the environment ends the episode
    (dead_end) first, and the inference loop stops there too.
    """
    masked = logits.masked_fill(~mask, float("-inf"))
    peak = torch.full((size,), float("-inf"), device=logits.device, dtype=logits.dtype).scatter_reduce(
        0, batch, masked, reduce="amax", include_self=True)
    scaled = masked - peak[batch].detach()
    norm = torch.zeros(size, device=logits.device, dtype=logits.dtype).index_add_(0, batch, scaled.exp())
    return scaled - norm[batch].log()


class ActorCritic(nn.Module):
    def __init__(self, config: ModelConfig = ModelConfig()):
        super().__init__()
        self.config = config
        hidden = config.hidden_dim
        context = hidden + GLOBAL_FEATURES
        self.graph_encoder = GraphEncoder(config)
        self.actor = _mlp(hidden + EDGE_FEATURES + context, hidden, 1)
        self.critic = _mlp(hidden + context, hidden, 1)

    def forward(self, batch):
        """(action log-probabilities [actions], value [samples])."""
        h = self.graph_encoder(batch["node_x"], batch["edge_index"], batch["edge_x"])
        size = batch["roots"].size(0)
        context = torch.cat([h[batch["roots"]], batch["global_x"]], dim=-1)
        logits = self.actor(torch.cat([h[batch["action_node"]], batch["action_edge_x"],
                                       context[batch["action_batch"]]], dim=-1)).squeeze(-1)
        log_probs = masked_log_softmax(logits, batch["action_mask"], batch["action_batch"], size)
        pooled = torch.zeros(size, h.size(1), device=h.device, dtype=h.dtype).index_add_(0, batch["node_batch"], h)
        counts = torch.bincount(batch["node_batch"], minlength=size).to(h.dtype).unsqueeze(-1)
        # The critic reads the encoder's output but does not train it: otherwise its value
        # loss moves the features the actor depends on, even while the actor head stands still.
        return log_probs, self.critic(torch.cat([pooled / counts, context], dim=-1).detach()).squeeze(-1)


def to_tensors(batch, device="cpu"):
    """numpy batch from features.collate -> tensors on `device`."""
    return {key: torch.as_tensor(value, device=device) for key, value in batch.items()}


def sample_offsets(action_batch, size):
    """Index of each sample's first action in the flat action arrays."""
    counts = torch.bincount(action_batch, minlength=size)
    return torch.cat([torch.zeros(1, dtype=counts.dtype, device=counts.device), counts.cumsum(0)[:-1]])


def sample_actions(log_probs, action_batch, size, greedy=False):
    """Per-sample action (index within the sample's own arcs), by Gumbel-max or argmax."""
    scores = log_probs if greedy else log_probs - torch.log(-torch.log(torch.rand_like(log_probs).clamp(1e-9, 1 - 1e-9)))
    peak = torch.full((size,), float("-inf"), device=scores.device, dtype=scores.dtype).scatter_reduce(
        0, action_batch, scores, reduce="amax", include_self=True)
    offsets = sample_offsets(action_batch, size)
    positions = torch.arange(scores.size(0), device=scores.device)
    # First position per sample reaching the peak: ties resolve to the lowest action.
    best = torch.full((size,), scores.size(0), device=scores.device).scatter_reduce(
        0, action_batch[scores == peak[action_batch]], positions[scores == peak[action_batch]],
        reduce="amin", include_self=True)
    return best - offsets, best


def entropy(log_probs, action_batch, size):
    """Entropy of each sample's masked action distribution."""
    safe = log_probs.masked_fill(torch.isinf(log_probs), 0.0)
    return -torch.zeros(size, device=log_probs.device, dtype=log_probs.dtype).index_add_(
        0, action_batch, safe.exp() * safe)
