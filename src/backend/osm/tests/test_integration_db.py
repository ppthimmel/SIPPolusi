"""Read-only checks against the real OSM Cache DB (UT-OPS-03, IT-K-06 subset).

Uses DATABASE_URL from the environment or src/backend/.env and is skipped when
the database cannot be reached. Nothing here writes to the database.
"""
from __future__ import annotations

import pytest
from pydantic import ValidationError

from osm.config import OsmSettings
from osm.db import OsmDatabase
from osm.errors import CacheUnavailable
from osm.graph import assemble_request_subgraph, compute_request_bbox
from osm.models import BBox, Mode, Point
from osm.repository import OsmRepository

pytestmark = pytest.mark.integration

MONAS = Point(106.8272, -6.1754)
# About 1 x 1 km around Monas.
MONAS_BBOX = BBox(106.822, -6.180, 106.832, -6.170)


@pytest.fixture(scope="module")
async def db():
    try:
        settings = OsmSettings()
    except ValidationError:
        pytest.skip("DATABASE_URL is not set")
    database = OsmDatabase(settings)
    try:
        await database._ensure_pool()
    except CacheUnavailable as exc:
        pytest.skip(f"OSM Cache DB unreachable: {exc.message}")
    yield database
    await database.close()


@pytest.fixture(scope="module")
def repo(db) -> OsmRepository:
    return OsmRepository(db)


async def test_session_is_read_only_with_statement_timeout(db):
    async with db.connection() as conn:
        assert await conn.fetchval("SHOW transaction_read_only") == "on"
        assert await conn.fetchval("SHOW statement_timeout") == "1500ms"


async def test_slow_query_becomes_cache_unavailable(db):
    # IT-K-06: a query held past statement_timeout is cancelled and mapped to 503.
    with pytest.raises(CacheUnavailable, match="statement_timeout"):
        async with db.connection() as conn:
            await conn.fetchval("SELECT pg_sleep(2)")


async def test_active_version(repo):
    version = await repo.get_active_version()
    assert version.status == "active"
    assert version.version.startswith("jakarta-")
    assert version.edge_count and version.node_count


async def test_read_road_graph_walk_uses_active_version_only(repo, db):
    graph = await repo.read_road_graph(MONAS_BBOX, Mode.WALK)
    active = (await repo.get_active_version()).version
    assert graph.graph_version == active
    assert graph.edges and graph.nodes
    assert all(e.walk_allowed for e in graph.edges)
    assert all(e.u in graph.nodes and e.v in graph.nodes for e in graph.edges)
    # Edge ids all belong to the active version.
    ids = [e.edge_id for e in graph.edges]
    async with db.connection() as conn:
        n = await conn.fetchval(
            "SELECT count(*) FROM osm.road_edge WHERE graph_version = $1 AND edge_id = ANY($2::bigint[])",
            active, ids,
        )
    assert n == len(ids)
    first = graph.edges[0]
    assert first.coordinates[0] == pytest.approx([graph.nodes[first.u].lon, graph.nodes[first.u].lat], abs=1e-6)


async def test_read_road_graph_bike_excludes_non_bike_edges(repo):
    graph = await repo.read_road_graph(MONAS_BBOX, Mode.BIKE, include_geometry=False)
    assert graph.edges
    assert all(e.bike_allowed for e in graph.edges)
    assert all(e.coordinates is None for e in graph.edges)


async def test_nearest_node_monas(repo):
    node = await repo.find_nearest_node(MONAS, Mode.WALK, 200)
    assert node is not None
    assert node.distance_m < 50


async def test_nearest_edge_monas(repo):
    edge = await repo.find_nearest_edge(MONAS, Mode.BIKE, 200)
    assert edge is not None
    assert 0.0 <= edge.fraction_along <= 1.0


async def test_nothing_near_open_sea(repo):
    sea = Point(106.80, -6.05)  # Jakarta Bay
    assert await repo.find_nearest_node(sea, Mode.WALK, 200) is None


async def test_edge_and_node_detail(repo):
    graph = await repo.read_road_graph(MONAS_BBOX, Mode.WALK, include_geometry=False)
    first = next(e for e in graph.edges if e.u != e.v)
    edge = await repo.get_edge(first.edge_id)
    assert edge is not None and edge.geometry["type"] == "LineString"
    node = await repo.get_node(edge.u)
    assert node is not None
    neighbors = await repo.get_neighbors(edge.u, Mode.WALK)
    assert any(n.edge_id == edge.edge_id for n in neighbors)


async def test_streets_sudirman(repo):
    results = await repo.search_streets("sudirman", 5)
    assert any(r.name == "Jalan Jenderal Sudirman" for r in results)


async def test_like_wildcards_are_literal(repo):
    assert await repo.search_streets("%%%", 5) == []


async def test_roads_geojson(repo):
    version, text = await repo.roads_geojson(MONAS_BBOX, Mode.WALK, ["primary", "secondary"])
    assert '"FeatureCollection"' in text and version in text


async def test_subgraph_monas_to_bundaran_hi(repo):
    origin, destination = MONAS, Point(106.8230, -6.1950)
    bbox = compute_request_bbox(origin, destination)
    graph = await repo.read_road_graph(bbox, Mode.WALK, include_geometry=False)
    sub = assemble_request_subgraph(graph, origin, destination)
    assert sub.origin_node != sub.destination_node
    assert len(sub.nodes) > 100
