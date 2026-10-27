"""Shared temporal/local encoders, graph attention, and scalar node regression."""

from dataclasses import dataclass

import torch
from torch import nn
from torch.nn import functional as F
from torch_geometric.nn import GATConv


@dataclass(frozen=True)
class ModelConfig:
    temporal_channels: int = 58
    local_channels: int = 8
    hidden_channels: int = 16
    local_hidden_channels: int = 8
    heads: int = 2
    kernel_size: int = 3
    dilations: tuple[int, ...] = (1, 2, 4)
    dropout: float = 0.2
    use_graph: bool = True
    use_local: bool = True

    def __post_init__(self):
        if min(self.temporal_channels, self.local_channels, self.hidden_channels,
               self.local_hidden_channels, self.heads, self.kernel_size) < 1:
            raise ValueError("Model dimensions must be positive")
        if self.hidden_channels % self.heads or not 0 <= self.dropout < 1:
            raise ValueError("Hidden width must divide into heads; dropout must be in [0, 1)")
        if not self.dilations or any(d < 1 for d in self.dilations):
            raise ValueError("Require positive temporal dilations")


class CausalConv1d(nn.Conv1d):
    def forward(self, x):
        left = self.dilation[0] * (self.kernel_size[0] - 1)
        return super().forward(F.pad(x, (left, 0)))


class TemporalBlock(nn.Module):
    def __init__(self, width, kernel_size, dilation, dropout):
        super().__init__()
        self.layers = nn.Sequential(
            CausalConv1d(width, width, kernel_size, dilation=dilation), nn.ReLU(), nn.Dropout(dropout),
            CausalConv1d(width, width, kernel_size, dilation=dilation), nn.ReLU(), nn.Dropout(dropout))

    def forward(self, x):
        return F.relu(x + self.layers(x))


class TemporalEncoder(nn.Module):
    """Input/output [batch, nodes, hours, channels]; convolution touches time only."""

    def __init__(self, config):
        super().__init__()
        self.projection = nn.Linear(config.temporal_channels, config.hidden_channels)
        self.blocks = nn.Sequential(*[
            TemporalBlock(config.hidden_channels, config.kernel_size, d, config.dropout)
            for d in config.dilations])

    def forward(self, x):
        b, n, hours, _ = x.shape
        h = self.projection(x).reshape(b * n, hours, -1).transpose(1, 2)
        return self.blocks(h).transpose(1, 2).reshape(b, n, hours, -1)


def batch_edges(edge_index, nodes, batches):
    """Disjoint copies of one graph, with one self loop per node in each copy."""
    if edge_index.dtype != torch.long or edge_index.ndim != 2 or edge_index.shape[0] != 2:
        raise ValueError("edge_index must be int64 [2, E]")
    if edge_index.numel() and (edge_index.min() < 0 or edge_index.max() >= nodes):
        raise ValueError("Edge endpoint outside graph")
    edges = edge_index[:, edge_index[0] != edge_index[1]]
    loops = torch.arange(nodes, device=edges.device).repeat(2, 1)
    edges = torch.unique(torch.cat([edges, loops], dim=1), dim=1)
    offsets = torch.arange(batches, device=edges.device) * nodes
    return (edges[:, None, :] + offsets[None, :, None]).reshape(2, -1)


class STGNN(nn.Module):
    """Returns standardized IDW correction [B, N, 1]; no data access or target scaling."""

    def __init__(self, config=ModelConfig()):
        super().__init__()
        self.config = config
        self.temporal = TemporalEncoder(config)
        h, s = config.hidden_channels, config.local_hidden_channels
        self.local = nn.Sequential(nn.Linear(config.local_channels, h), nn.ReLU(),
                                   nn.Dropout(config.dropout), nn.Linear(h, s), nn.ReLU())
        self.fusion = nn.Sequential(nn.Linear(h + s, h), nn.ReLU())
        self.graph = (GATConv(h, h // config.heads, heads=config.heads, concat=True,
                              dropout=config.dropout, add_self_loops=False)
                      if config.use_graph else None)
        self.norm = nn.LayerNorm(h)
        self.regression = nn.Sequential(nn.Linear(h, 16), nn.ReLU(), nn.Linear(16, 1))

    def forward(self, temporal, local, edge_index):
        c = self.config
        if temporal.ndim != 4 or temporal.shape[-1] != c.temporal_channels:
            raise ValueError("Invalid temporal shape")
        b, n, hours, _ = temporal.shape
        if min(b, n, hours) < 1 or local.shape != (b, n, c.local_channels):
            raise ValueError("Invalid local shape or empty batch")
        if not torch.isfinite(temporal).all() or not torch.isfinite(local).all():
            raise ValueError("Preprocess missing/nonfinite values before model inference")
        dynamic = self.temporal(temporal)[:, :, -1]
        static = self.local(local) if c.use_local else dynamic.new_zeros(b, n, c.local_hidden_channels)
        fused = self.fusion(torch.cat([dynamic, static], dim=-1))
        if self.graph is not None:
            edges = batch_edges(edge_index, n, b)
            spatial = self.graph(fused.reshape(b * n, -1), edges).reshape(b, n, -1)
            fused = fused + F.elu(spatial)
        return self.regression(self.norm(fused))
