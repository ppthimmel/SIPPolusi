"""A large synthetic SolverInput for timeout and cancellation tests (no database)."""
from __future__ import annotations

from datetime import datetime, timezone

from osm.models import BBox, Mode, Point
from planner.models import PlannedEdge, Preference, SnappedPoint, SolverInput, SourceMetadata, WeightStatus

STEP = 0.0005  # about 55 m


def grid_input(n: int, reachable: bool = False) -> SolverInput:
    """n x n bidirectional grid near Jakarta. The destination is an isolated node
    unless reachable, so a search has to expand every grid node before giving up."""
    def nid(r: int, c: int) -> int:
        return r * n + c + 1

    nodes = {nid(r, c): Point(106.8 + c * STEP, -6.2 + r * STEP) for r in range(n) for c in range(n)}
    edges: list[PlannedEdge] = []
    for r in range(n):
        for c in range(n):
            for r2, c2 in ((r, c + 1), (r + 1, c)):
                if r2 < n and c2 < n:
                    edges.append(PlannedEdge(
                        edge_id=len(edges) + 1, u=nid(r, c), v=nid(r2, c2), length_m=55.0, highway="residential",
                        oneway=False, bidirectional=True, travel_time_s=55.0 / 1.4, exposure_index=1.0,
                        confidence_score=0.5, weight_source="estimate", concentration_ugm3=20.0, pm25_ugm3=20.0,
                        no2_ugm3=30.0, estimation_source="idw"))
    if reachable:
        goal = nid(n - 1, n - 1)
    else:
        goal = n * n + 1
        nodes[goal] = Point(106.8 + n * STEP * 2, -6.2)
    now = datetime(2026, 10, 26, 10, tzinfo=timezone.utc)
    origin = nid(0, 0)
    return SolverInput(
        request_id="big", mode=Mode.WALK, speed_mps=1.4, preference=Preference(0.5, 0.5), pollutant="pm25",
        bbox=BBox(106.79, -6.21, 106.8 + n * STEP * 2 + 0.01, -6.2 + n * STEP + 0.01),
        origin=SnappedPoint(nodes[origin], origin, 0.0), destination=SnappedPoint(nodes[goal], goal, 0.0),
        weights=WeightStatus(now, now, False, 0.0, len(edges), len(edges), 0, None, 0),
        source=SourceMetadata("grid", None, None, None, {}, {}, {}),
        nodes=nodes, edges=edges)
