# Fastest-route baseline (TI-AI-07)

`solve_baseline` in `astar_osm.py` is shared by the CLI, API and experiments.
Coordinates are WGS84 decimal degrees, supplied as `{"lon": ..., "lat": ...}`.
Modes are `walk` and `bike`. The solver returns a JSON-serializable dictionary.

```python
from src.backend.route_model.astar_osm import solve_baseline, ExposureSnapshot

result = solve_baseline(
    {"lon": 106.8502, "lat": -6.2099},
    {"lon": 106.8272, "lat": -6.1754},
    "bike",
)
```

## Cost and metrics

For each directed edge, `t_e = length_m / speed_mps`; `T(R) = sum(t_e)` in
seconds. Default assumed speeds are **5 km/h walking** and **15 km/h cycling**.
These are configurable model assumptions in `SPEED_MPS`, not measured journey
speeds. Traffic lights, congestion, slope, dismounting and surface delays are
not modelled. Optional CSV columns `walk_time_s` and `bike_time_s` override
the corresponding edge time; supplied values must be positive and finite.
Experiments can also construct a `RoadRouter` with explicit `Edge.time_s`.
At a constant speed within a mode the fastest path equals the shortest path;
nonuniform edge times can select a different path, as the unit fixture shows.

The objective is only `T(R)`. `length_m` is summed separately. Exposure uses
one supplied pollutant snapshot: `E(R) = sum(concentration_e * t_e)` in
**microgram seconds per cubic metre**, not inhaled dose or a medical score.
`ExposureSnapshot` requires the graph version, pollutant name, time window,
and an edge-ID-to-concentration mapping in micrograms per cubic metre.
The snapshot must match the router's graph. A negative/nonfinite supplied
concentration is rejected. Missing values remain missing: if any traversed
edge lacks a value, `exposure_ug_s_m3` is null with `partial` or `unavailable`
status and an edge coverage fraction. No partial sum is presented as a total.

`RoadRouter.search` uses only travel time. Once the path is selected,
`route_metrics.evaluate_route` evaluates its time, length and exposure. The same
function can evaluate an already selected policy route; exposure never affects
the baseline's search or tie-breaking.

```python
snapshot = ExposureSnapshot(
    graph_version="jakarta-20261005",
    time_window="2026-10-10T00:00:00Z",
    pollutant="pm25",
    concentration_ug_m3={123: 18.0, 456: 22.0},
)
# Pass exposure=snapshot to solve_baseline(...).
```

The caller must supply a snapshot selected for the requested window and enforce
freshness. The current HTTP adapter has no pollution-cache integration and
therefore reports unavailable exposure. TI-SE-05 can supply that snapshot to
the same backend function. TI-AI-10 must reuse the same OD, snapped node IDs,
graph, edge times and snapshot for both solvers; do not independently resnap the
policy route. Missing exposure must be excluded/reported explicitly in comparisons.

The A* heuristic is `min_e(t_e / geodesic(u_e,v_e)) * geodesic(node,goal)`.
The triangle inequality bounds the heuristic change by every edge's time, so
it is consistent and admissible even with custom times. Node IDs and sorted
adjacency break ties deterministically. GeoJSON and route metrics are stable
for identical inputs. Measured `diagnostics.computation_ms` is outside GeoJSON
and naturally varies; it includes loading on a cold call.

## Snapping and statuses

Default snap radius: 300 m, measured geodesically. Origin candidates need an
outgoing arc; destination candidates need an incoming arc. Strongly connected
components and their directed condensation graph determine feasible pairs.
Choose the nearest **feasible origin**, then its nearest reachable destination,
breaking equal distances by node ID. Search all candidates within the radius;
do not restrict routing to the largest component or discard valid sink nodes.
This policy prioritizes origin proximity, rather than minimizing the sum of
two snap offsets. Response `snap` exposes both selected IDs and offsets.

Travel metrics cover only the network between snapped nodes. Snap offsets are
not asserted to be traversable access paths and are not included in time,
length or exposure. A* finds the fastest route for the selected pair.

| Status | Meaning | HTTP |
|---|---|---|
| `ok` | Nonempty route found | 200 |
| `same_location` | Identical input coordinates in graph coverage; no journey needed | 200 |
| `same_node` | Distinct coordinates snap to the same node; no network segment | 422 |
| `snap_failed` | No eligible node within radius at an endpoint | 404 |
| `no_route` | Candidates exist but no directed connection | 404 |
| `invalid_input` | Invalid coordinates, mode, radius or exposure snapshot | 422 |
| `data_unavailable` | Export files unavailable/unreadable | 503 |
| `invalid_graph` | Export fails validation | 503 |

All statuses except `ok` have `route: null` and `metrics: null`, so an empty
geometry cannot masquerade as a successfully computed journey. The existing
HTTP adapter maps these domain statuses to the status codes above.

`route_source` is `baseline` on direct calls. `/v1/routes` currently falls back
because the MORL policy is unavailable, returning `route_source: fallback`,
`fallback_reason: policy_unavailable`, `preference_applied: false`. An exception
from the policy adapter is labelled `policy_error`. The baseline does not use
alpha/beta. Actual model timeout/cancellation is not yet implemented; the future
model adapter must enforce its deadline before invoking fallback.

## API and CLI

Call `solve_baseline` or `inference.solve_baseline_route` directly to select
the baseline for experiments. The existing `POST /v1/routes` endpoint uses
the policy adapter and explicitly marked fallback. It accepts:

```json
{
  "origin": {"lon": 106.8502, "lat": -6.2099},
  "destination": {"lon": 106.8272, "lat": -6.1754},
  "mode": "bike",
  "alpha": 0.5,
  "beta": 0.5
}
```

The successful response includes `route` (GeoJSON FeatureCollection), `metrics`,
`graph_version`, `mode`, `route_source`, `snap`, `visited_nodes`, and
`graph_components` (strongly connected component count, not a failure rate).

```shell
python src/backend/route_model/astar_osm.py --mode bike --start-lon 106.8502 --start-lat -6.2099 --end-lon 106.8272 --end-lat -6.1754 --output route.geojson
```

CLI prints the structured result, exits 0 for `ok`/`same_location` and 2 for
domain failure. `--output` writes GeoJSON on success and the structured result
on failure, replacing any stale output. Argument syntax errors remain argparse
errors. Filesystem errors writing the requested output are not route failures.

## Cache and data input

The process caches at most two routers (normally walk and bike), including
geometry, adjacency and reachability components. A load lock prevents duplicate
construction under concurrent requests. Each call checks export file size and
mtime; replacing a dataset invalidates its cached entry. Deploy all export files
atomically or restart after replacing them. Cache memory is private to each API
worker, and separate CLI processes each pay the cold load cost.

`OSM_GRAPH_DIR` overrides the dataset directory; local default is
`src/data_worker/dataset_processed/OSM`. Required files are `graph_version.csv`,
`road_node.csv.gz` and `road_edge.csv.gz`. The calling environment must make
these exports available; otherwise the solver returns `data_unavailable`.
The OSM exports come from merged PR #44.
Retain OpenStreetMap attribution: © OpenStreetMap contributors,
ODbL, https://www.openstreetmap.org/copyright.

## Verification

```shell
python -m pip install -r src/backend/requirements-test.txt
python -m unittest discover -s src/backend/tests -v
python -m src.backend.route_model.verify_jakarta --output docs/experiments/TI-AI-07/jakarta-baseline.json
```

Small graph tests include a 100 m/100 s direct edge competing with a
400 m/20 s path, whose hand-calculated exposure is `10*10 + 20*10 = 300`.
They also compare A* with independent Dijkstra on 400 pairs, test access and
one-way direction, unreachable snap candidates, disconnected components,
zero-length journeys, invalid/missing data, cache invalidation and API statuses.
The Jakarta script records explicit coordinates and cold/warm timings; this is
one regression scenario, not a general latency benchmark or a measured 2.3%
failure rate. See the experiment report for measured results and limitations.
