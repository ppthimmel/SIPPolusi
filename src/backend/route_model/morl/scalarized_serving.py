"""Load and decode the conditioned policy without importing training code."""
import torch

from .features import FEATURE_VERSION
from .infer import CHECKPOINT_FORMAT, PolicyRoute, decode_route
from .scalarized_policy import BoundPreference, PreferenceActorCritic, ScalarizedConfig


def load_scalarized_policy(path, device="cpu"):
    """Rebuild the network from a checkpoint alone."""
    checkpoint = torch.load(path, map_location=device, weights_only=True)
    if checkpoint.get("format") != CHECKPOINT_FORMAT:
        raise ValueError(f"Unknown checkpoint format: {checkpoint.get('format')!r}")
    if checkpoint.get("feature_version") != FEATURE_VERSION:
        raise ValueError(f"Checkpoint expects features {checkpoint.get('feature_version')!r}, "
                         f"this code builds {FEATURE_VERSION!r}")
    model = PreferenceActorCritic(ScalarizedConfig(**checkpoint["model_config"])).to(device)
    model.load_state_dict(checkpoint["model_state"])
    return model.eval(), checkpoint


def decode_preference_route(model, router, exposure, start, end, preference, **kwargs) -> PolicyRoute:
    """Decode the requested preference; propagate cancellation and policy failures."""
    alpha, beta = preference
    if abs(alpha + beta - 1) > 1e-6:
        raise ValueError("alpha and beta must sum to 1")
    return decode_route(BoundPreference(model, alpha), router, exposure, start, end, **kwargs)
