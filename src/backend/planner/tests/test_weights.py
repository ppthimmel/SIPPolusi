"""attach_edge_weights (UT-BE-07): estimates, median fill, no silent zero."""
from __future__ import annotations

from statistics import median

import pytest

from osm.errors import CacheUnavailable
from osm.graph import assemble_request_subgraph
from osm.models import Mode, ModeProfile
from osm.tests.conftest import edge, grid_graph
from osm.models import BBox, RoadGraph, RoadNode
from planner.models import ESTIMATE, IMPUTED_MEDIAN
from planner.weights import attach_edge_weights, downscaling_fallbacks

from .conftest import ORIGIN_LAT, ORIGIN_LON, VERSION, pollution_read, weight

WALK = ModeProfile(Mode.WALK, 1.3)
BIKE = ModeProfile(Mode.BIKE, 4.2)


def ten_edge_subgraph(mode=Mode.WALK, oneway_first=False):
    """A line of 11 nodes and 10 edges of 110 m."""
    nodes = {i: RoadNode(i, ORIGIN_LON + i * 0.001, ORIGIN_LAT) for i in range(11)}
    edges = [edge(i + 1, i, i + 1, oneway=(oneway_first and i == 0)) for i in range(10)]
    g = RoadGraph(VERSION, mode, BBox(106.7, -6.3, 106.9, -6.1), nodes, edges)
    return assemble_request_subgraph(g, nodes[0].point, nodes[10].point)


def test_ut_be_07a_every_edge_has_an_estimate():
    sub = ten_edge_subgraph()
    read = pollution_read([weight(i, exposure=0.1 * i, confidence=0.5) for i in range(1, 11)])
    edges, status = attach_edge_weights(sub, read, WALK)
    by_id = {e.edge_id: e for e in edges}
    assert by_id[3].exposure_index == pytest.approx(0.3) and by_id[3].confidence_score == 0.5
    assert all(e.weight_source == ESTIMATE for e in edges)
    assert (status.edge_count, status.estimated_edge_count, status.missing_edge_count) == (10, 10, 0)
    assert status.imputed_exposure_index is None


def test_ut_be_07b_missing_edges_get_bbox_median_and_zero_confidence():
    sub = ten_edge_subgraph()
    present = [weight(i, exposure=float(i)) for i in range(1, 11) if i not in (4, 7)]
    # A row outside the subgraph but inside the bbox also counts for the median.
    bbox_only = weight(99, exposure=50.0)
    read = pollution_read(present + [bbox_only])
    edges, status = attach_edge_weights(sub, read, WALK)
    fill = median([w.exposure_index for w in present + [bbox_only]])
    by_id = {e.edge_id: e for e in edges}
    for missing in (4, 7):
        e = by_id[missing]
        assert (e.exposure_index, e.confidence_score, e.weight_source) == (fill, 0.0, IMPUTED_MEDIAN)
        assert e.exposure_index != 0 and e.concentration_ugm3 is None
    assert status.missing_edge_count == 2 and status.imputed_exposure_index == pytest.approx(fill)


def test_row_without_exposure_index_counts_as_missing_but_keeps_concentration():
    sub = ten_edge_subgraph()
    rows = [weight(i) for i in range(1, 11)]
    rows[0] = weight(1, exposure=None, pm25=18.0, no2=None)
    edges, status = attach_edge_weights(sub, pollution_read(rows), WALK, pollutant="pm25")
    first = next(e for e in edges if e.edge_id == 1)
    assert first.weight_source == IMPUTED_MEDIAN and first.confidence_score == 0.0
    assert first.concentration_ugm3 == 18.0  # what is known is kept, never imputed
    assert status.missing_edge_count == 1


def test_concentration_follows_requested_pollutant_and_is_never_imputed():
    sub = ten_edge_subgraph()
    rows = [weight(i, pm25=20.0, no2=None if i == 2 else 40.0) for i in range(1, 11)]
    edges, status = attach_edge_weights(sub, pollution_read(rows), WALK, pollutant="no2")
    by_id = {e.edge_id: e for e in edges}
    assert by_id[1].concentration_ugm3 == 40.0 and by_id[2].concentration_ugm3 is None
    assert status.concentration_missing_count == 1 and status.missing_edge_count == 0


def test_no_usable_weight_in_bbox_is_cache_empty_not_zero():
    sub = ten_edge_subgraph()
    with pytest.raises(CacheUnavailable) as exc:
        attach_edge_weights(sub, pollution_read([]), WALK)
    assert exc.value.reason == CacheUnavailable.CACHE_EMPTY
    with pytest.raises(CacheUnavailable):
        attach_edge_weights(sub, pollution_read([weight(1, exposure=None)]), WALK)


def test_stale_and_excluded_are_reported():
    sub = ten_edge_subgraph()
    read = pollution_read([weight(i) for i in range(1, 11)], stale=True, age_s=5 * 3600,
                          excluded={"other_graph_version": 3})
    _, status = attach_edge_weights(sub, read, WALK)
    assert status.data_stale is True and status.age_minutes == 300.0
    assert status.excluded_rows == {"other_graph_version": 3}


def test_travel_time_and_direction_follow_the_mode():
    walk_sub = ten_edge_subgraph(Mode.WALK, oneway_first=True)
    bike_sub = ten_edge_subgraph(Mode.BIKE, oneway_first=True)
    read = pollution_read([weight(i) for i in range(1, 11)])
    walk_edges, _ = attach_edge_weights(walk_sub, read, WALK)
    bike_edges, _ = attach_edge_weights(bike_sub, read, BIKE)
    w1 = next(e for e in walk_edges if e.edge_id == 1)
    b1 = next(e for e in bike_edges if e.edge_id == 1)
    assert w1.travel_time_s == pytest.approx(110 / 1.3, abs=1e-3) and w1.bidirectional is True
    assert b1.travel_time_s == pytest.approx(110 / 4.2, abs=1e-3) and b1.bidirectional is False


def test_downscaling_fallbacks_lists_pollutants_estimated_without_the_registered_model():
    """D-07 (TI-SE-10): only estimates on the subgraph count, and only a recorded fallback."""
    import dataclasses
    sub = ten_edge_subgraph()
    meta = {"pm25": {"source": "idw_stgnn_residual", "model_version": "3"},
            "no2": {"source": "idw", "model_version": "idw-p2-k8", "fallback_reason": "load_bundle:FileNotFoundError"}}
    rows = [dataclasses.replace(weight(i), model_metadata=meta) for i in range(1, 11)]
    edges, _ = attach_edge_weights(sub, pollution_read(rows), WALK)
    assert downscaling_fallbacks(edges, {w.edge_id: w for w in rows}) == ("no2",)
    plain = [weight(i) for i in range(1, 11)]  # legacy rows: no per-pollutant metadata
    edges, _ = attach_edge_weights(sub, pollution_read(plain), WALK)
    assert downscaling_fallbacks(edges, {w.edge_id: w for w in plain}) == ()
