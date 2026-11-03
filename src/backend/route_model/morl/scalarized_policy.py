"""The preference-conditioned Actor-Critic of scalarized MORL (PIC A of TI-AI-09).

One network serves every preference. The Preference Encoder turns w = (alpha, beta)
into a vector z that modulates the Graph Encoder's output and joins the Actor's and
the Critic's input, so a new preference needs no retraining. Apart from that and the
regret path below, the network matches the multi-policy member in policy.py.

The preference travels at the end of `global_x`, which keeps batches in the format of
features.collate.

An arc's score also subtracts its scalarized regret, alpha * time regret + beta * exposure
regret, times a learned scale. An MLP on concatenated inputs learns that product only
slowly: without this path the warm-start policy took nearly the same routes at alpha = 0
and alpha = 1, even where a trade-off existed. The MLP learns the correction to this
prior, so `verify` in scalarized.py reports the policy against the prior alone (RegretPrior).
PIC B has no such path, so the path must be separated from the approach when the two are
compared: `regret_path = False` trains this network without it.
"""
from __future__ import annotations

import math
from dataclasses import dataclass

import torch
from torch import nn

from .features import EDGE_FEATURES, GLOBAL_FEATURES
from .policy import GraphEncoder, ModelConfig, _mlp, masked_log_softmax

PREFERENCE_FEATURES = 2
# Positions of the two cost-to-go regrets in features.edge_features.
TIME_REGRET, EXPOSURE_REGRET = 5, 6
# A regret of 1% of the reference route is 0.1 in the features, so this starts it at -2 logits.
INITIAL_REGRET_SCALE = 20.0


@dataclass(frozen=True)
class ScalarizedConfig(ModelConfig):
    preference_dim: int = 16
    fourier: int = 4
    regret_path: bool = True  # False only for the comparison that separates this path from the approach

    def __post_init__(self):
        super().__post_init__()
        if self.preference_dim < 1 or self.fourier < 0:
            raise ValueError("preference_dim must be positive and fourier nonnegative")


def scalarized_regret(batch):
    """alpha * time regret + beta * exposure regret of every action in the batch."""
    preference, owner, arcs = batch["global_x"][:, GLOBAL_FEATURES:], batch["action_batch"], batch["action_edge_x"]
    return preference[owner, 0] * arcs[:, TIME_REGRET] + preference[owner, 1] * arcs[:, EXPOSURE_REGRET]


class PreferenceEncoder(nn.Module):
    """w = (alpha, beta) -> z; Fourier features of alpha let z vary finely along the preference line."""

    def __init__(self, out_dim, fourier):
        super().__init__()
        self.register_buffer("frequencies", math.pi * torch.arange(1, fourier + 1, dtype=torch.float32))
        self.net = _mlp(PREFERENCE_FEATURES + 2 * fourier, out_dim, out_dim)

    def forward(self, preference):
        angles = preference[:, :1] * self.frequencies
        return self.net(torch.cat([preference, torch.sin(angles), torch.cos(angles)], dim=-1))


class PreferenceActorCritic(nn.Module):
    def __init__(self, config: ScalarizedConfig = ScalarizedConfig()):
        super().__init__()
        self.config = config
        hidden, z_dim = config.hidden_dim, config.preference_dim
        context = hidden + GLOBAL_FEATURES + z_dim
        self.graph_encoder = GraphEncoder(config)
        self.preference_encoder = PreferenceEncoder(z_dim, config.fourier)
        self.film = nn.Linear(z_dim, 2 * hidden)
        self.actor = _mlp(hidden + EDGE_FEATURES + 1 + context, hidden, 1)
        self.critic = _mlp(hidden + context, hidden, 1)
        if config.regret_path:
            self.log_regret_scale = nn.Parameter(torch.tensor(INITIAL_REGRET_SCALE).log())

    def forward(self, batch):
        """(action log-probabilities [actions], value [samples]); `global_x` ends with (alpha, beta)."""
        global_x, preference = batch["global_x"][:, :GLOBAL_FEATURES], batch["global_x"][:, GLOBAL_FEATURES:]
        size = batch["roots"].size(0)
        z = self.preference_encoder(preference)
        h = self.graph_encoder(batch["node_x"], batch["edge_index"], batch["edge_x"])
        scale, shift = self.film(z)[batch["node_batch"]].chunk(2, dim=-1)
        h = h * (1 + scale) + shift
        context = torch.cat([h[batch["roots"]], global_x, z], dim=-1)
        arcs, owner = batch["action_edge_x"], batch["action_batch"]
        regret = scalarized_regret(batch)
        logits = self.actor(torch.cat([h[batch["action_node"]], arcs, regret.unsqueeze(-1), context[owner]],
                                      dim=-1)).squeeze(-1)
        if self.config.regret_path:
            logits = logits - self.log_regret_scale.exp() * regret
        log_probs = masked_log_softmax(logits, batch["action_mask"], owner, size)
        pooled = torch.zeros(size, h.size(1), device=h.device, dtype=h.dtype).index_add_(0, batch["node_batch"], h)
        counts = torch.bincount(batch["node_batch"], minlength=size).to(h.dtype).unsqueeze(-1)
        # As in policy.py: the critic reads the representation but does not train it.
        return log_probs, self.critic(torch.cat([pooled / counts, context], dim=-1).detach()).squeeze(-1)


class RegretPrior(nn.Module):
    """The arc score without its learned part, as a baseline: greedy on the scalarized regret."""

    def __init__(self, config: ScalarizedConfig):
        super().__init__()
        self.config = config

    def forward(self, batch):
        size = batch["roots"].size(0)
        log_probs = masked_log_softmax(-scalarized_regret(batch), batch["action_mask"], batch["action_batch"], size)
        return log_probs, torch.zeros(size, device=log_probs.device, dtype=log_probs.dtype)


class BoundPreference:
    """The model at one fixed preference, usable wherever batches come straight from features.collate."""

    def __init__(self, model: PreferenceActorCritic, alpha: float):
        if not 0 <= alpha <= 1:
            raise ValueError("alpha must lie in [0, 1]")
        self.model, self.config, self.alpha = model, model.config, alpha

    def __call__(self, batch):
        global_x = batch["global_x"]
        preference = global_x.new_tensor([self.alpha, 1.0 - self.alpha]).expand(global_x.size(0), PREFERENCE_FEATURES)
        return self.model({**batch, "global_x": torch.cat([global_x, preference], dim=-1)})
