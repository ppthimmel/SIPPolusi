"""Training-only perturbation of exposure fields, so that a time-exposure trade-off exists.

An IDW field is smooth at trip scale: on ds-v0.3.0 the exact minimum-exposure route
is only 0.3% cleaner than the fastest route (0.5-3 km trips), so the reward barely
depends on the preference and a policy never learns to use it. Each augmented
episode therefore multiplies the field by a random smooth pattern of hot and clean
areas, a kilometre or so across. These fields are synthetic: they only teach the
policy how to weigh time against exposure. Checkpoint selection stays on the real
validation fields, and results on perturbed fields are reported separately, labelled.
"""
from __future__ import annotations

import math
from dataclasses import asdict, dataclass

METRES_PER_DEGREE = 111_000.0


@dataclass(frozen=True)
class Augmentation:
    probability: float = 0.75        # share of training episodes on a perturbed field
    max_log_std: float = 0.5         # the pattern's standard deviation in log scale is U[0, this]
    min_wavelength_m: float = 1000.0
    max_wavelength_m: float = 4000.0
    components: int = 4

    def __post_init__(self):
        if not 0 <= self.probability <= 1:
            raise ValueError("probability must lie in [0, 1]")
        if self.max_log_std < 0 or self.components < 1:
            raise ValueError("max_log_std must be nonnegative and components at least 1")
        if not 0 < self.min_wavelength_m <= self.max_wavelength_m:
            raise ValueError("Require 0 < min_wavelength_m <= max_wavelength_m")

    def as_dict(self):
        return asdict(self)


class SmoothField:
    """exp(s * sum of random plane waves): positive, smooth, with log-scale standard deviation s."""

    def __init__(self, augmentation: Augmentation, rng):
        self.log_std = rng.uniform(0.0, augmentation.max_log_std)
        self.waves = []
        for _ in range(augmentation.components):
            wavelength = rng.uniform(augmentation.min_wavelength_m, augmentation.max_wavelength_m)
            direction = rng.uniform(0.0, 2 * math.pi)
            k = 2 * math.pi / wavelength * METRES_PER_DEGREE
            self.waves.append((k * math.cos(direction), k * math.sin(direction), rng.uniform(0.0, 2 * math.pi)))
        # A sine's variance is 1/2, so this normalises the sum to unit variance.
        self.norm = math.sqrt(2.0 / augmentation.components)

    def factor(self, lon, lat):
        total = sum(math.sin(kx * lon + ky * lat + phase) for kx, ky, phase in self.waves)
        return math.exp(self.log_std * self.norm * total)


class PerturbedExposure:
    """edge_id -> base value x the field at the edge's midpoint, computed on demand.

    An episode touches a few thousand of the graph's 300k edges; the memo lives only
    as long as the episode's environment holds this object.
    """

    def __init__(self, base, router, field: SmoothField):
        self.base, self.router, self.field = base, router, field
        self._memo = {}

    def __getitem__(self, edge_id):
        value = self._memo.get(edge_id)
        if value is None:
            edge = self.router.edges[edge_id]
            (x1, y1), (x2, y2) = self.router.coords[edge.u], self.router.coords[edge.v]
            value = self._memo[edge_id] = self.base[edge_id] * self.field.factor((x1 + x2) / 2, (y1 + y2) / 2)
        return value
