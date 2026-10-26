"""build_solver_input with fake caches: domain errors, mode, partial cache."""
from __future__ import annotations

import pytest

from osm.errors import CacheUnavailable, InvalidRequest, NoRouteFound, OutsideStudyArea, PointNotOnNetwork
from planner.assembler import build_solver_input

from .conftest import EMPTY, VERSION, FakePollutionRepository, node_id, request_body, weight


async def plan(body, osm_repo, pollution_repo, settings):
    return await build_solver_input(body, osm=osm_repo, pollution=pollution_repo, settings=settings,
                                    request_id="req-1")


async def test_valid_request_gives_connected_subgraph(osm_repo, pollution_repo, settings):
    si = await plan(request_body(), osm_repo, pollution_repo, settings)
    assert (si.origin.node_id, si.destination.node_id) == (node_id(0, 0), node_id(4, 4))
    assert len(si.nodes) == 25 and len(si.edges) == 40  # island excluded
    assert si.weights.missing_edge_count == 0 and si.source.graph_version == VERSION
    assert si.source.estimation_sources == {"idw": 40} and si.request_id == "req-1"
    # Both caches read the same active graph version.
    assert pollution_repo.calls[0]["graph_version"] == VERSION and pollution_repo.calls[0]["mode"].value == "walk"


async def test_mode_changes_available_edges(osm_repo, pollution_repo, settings):
    # Make the first grid edges walk-only; the bike subgraph must not contain them.
    from osm.tests.conftest import edge
    g = osm_repo.graph
    g.edges = [edge(e.edge_id, e.u, e.v, bike=False) if e.edge_id in (1, 2) else e for e in g.edges]
    walk = await plan(request_body(mode="walk"), osm_repo, pollution_repo, settings)
    bike = await plan(request_body(mode="bike"), osm_repo, pollution_repo, settings)
    assert {1, 2} <= {e.edge_id for e in walk.edges}
    assert not {1, 2} & {e.edge_id for e in bike.edges}
    assert bike.speed_mps == 4.2 and walk.speed_mps == 1.3


async def test_partial_pollution_cache_is_flagged(osm_repo, settings):
    repo = FakePollutionRepository([weight(i, exposure=1.0 + i / 100) for i in range(1, 41) if i % 10])
    si = await plan(request_body(), osm_repo, repo, settings)
    assert si.weights.missing_edge_count == 4 and si.weights.estimated_edge_count == 36
    imputed = [e for e in si.edges if e.weight_source == "imputed_median"]
    assert {e.edge_id for e in imputed} == {10, 20, 30, 40}
    assert all(e.confidence_score == 0 and e.exposure_index > 0 for e in imputed)


async def test_invalid_and_outside_requests_do_not_touch_the_caches(osm_repo, pollution_repo, settings):
    with pytest.raises(InvalidRequest):
        await plan(request_body(alpha=0.7, beta=0.7), osm_repo, pollution_repo, settings)
    body = request_body()
    body["destination"] = {"lat": -5.75, "lon": 106.55}
    with pytest.raises(OutsideStudyArea):
        await plan(body, osm_repo, pollution_repo, settings)
    assert osm_repo.calls == [] and pollution_repo.calls == []


async def test_point_that_cannot_be_snapped(osm_repo, pollution_repo, settings):
    body = request_body()
    body["destination"] = {"lat": body["destination"]["lat"] + 0.01, "lon": body["destination"]["lon"]}  # ~1 km off
    with pytest.raises(PointNotOnNetwork, match="tujuan"):
        await plan(body, osm_repo, pollution_repo, settings)


async def test_disconnected_destination(osm_repo, pollution_repo, settings):
    body = request_body()
    island = osm_repo.graph.nodes[900]
    body["destination"] = {"lat": island.lat, "lon": island.lon}
    with pytest.raises(NoRouteFound):
        await plan(body, osm_repo, pollution_repo, settings)


async def test_empty_pollution_cache_is_503_cache_empty(osm_repo, settings):
    with pytest.raises(CacheUnavailable) as exc:
        await plan(request_body(), osm_repo, FakePollutionRepository(fail=EMPTY), settings)
    assert exc.value.reason == CacheUnavailable.CACHE_EMPTY


async def test_stale_pollution_is_used_and_flagged(osm_repo, settings):
    si = await plan(request_body(), osm_repo, FakePollutionRepository(stale=True), settings)
    assert si.weights.data_stale is True and len(si.edges) == 40


async def test_trip_errors_come_before_an_empty_pollution_cache(osm_repo, settings):
    body = request_body()
    body["destination"] = {"lat": body["destination"]["lat"] + 0.01, "lon": body["destination"]["lon"]}
    with pytest.raises(PointNotOnNetwork):
        await plan(body, osm_repo, FakePollutionRepository(fail=EMPTY), settings)
