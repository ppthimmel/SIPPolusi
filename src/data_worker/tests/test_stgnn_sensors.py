"""Sensor interpolation contract retained from the superseded notebook tests."""
import numpy as np
import pytest
from spatial_model.models.idw import idw_interpolate
from spatial_model.models.stgnn.sensors import idw_features


def test_interpolation_matches_shared_idw():
    rng = np.random.default_rng(12)
    sources = rng.normal(size=(15, 2)) * 1000
    destinations = np.vstack([sources[0], rng.normal(size=(8, 2)) * 1000])
    values = rng.uniform(0, 100, 15)
    values[3] = np.nan
    distances = np.linalg.norm(destinations[:, None] - sources[None], axis=-1)
    actual = idw_features(distances, values)
    expected = idw_interpolate(sources, values, destinations, power=2, neighbors=8)
    np.testing.assert_allclose(actual[:, 0], expected["value"], rtol=1e-6)
    assert actual[0, 0] == pytest.approx(values[0])
    assert np.isnan(idw_features(distances, np.full(15, np.nan))).all()


