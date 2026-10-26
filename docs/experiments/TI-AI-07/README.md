# TI-AI-07 baseline verification

Dataset: `jakarta-20261005`, OSM exports from merged PR #44. Scenario uses
Manggarai `(106.8502, -6.2099)` to Monas `(106.8272, -6.1754)`, radius 300 m.
Coordinates are specified here because the review did not provide exact OD
coordinates. Reproduce using `python -m src.backend.route_model.verify_jakarta`.

The old independent destination snap selects bike node **2624398858**, which
is unreachable from origin node **9398103262**. Reachability-aware snapping
selects **2624398882**, 202.67 m from the requested destination, and finds a
6,304.12 m route with estimated time 1,512.99 s (25.22 min at 15 km/h).
Walking yields 5,190.01 m and 3,736.81 s (62.28 min at 5 km/h).
The offsets are reported; access travel outside the road graph is not modelled.

Two requests per mode produce identical GeoJSON hashes. Measured locally on
Windows/Python 3.13: bike cold 13.31 s / warm 0.275 s; walk cold 15.22 s /
warm 0.286 s. Cold calls validate and index the graph; warm requests reuse it.
These are individual observations, not percentiles or concurrency benchmarks.
The raw response metadata and hashes are in `jakarta-baseline.json`.

The unit/API suite verifies 18 cases, including 400 pairwise comparisons with
Dijkstra, a hand-calculated time/exposure fixture, and unchanged baseline paths
under different pollution values. Route metrics are evaluated by a separate
function reusable for policy routes. A sandboxed API run hung;
the complete suite passed outside the Windows sandbox.

There is no pollution snapshot in this OSM export: real-route exposure is
explicitly unavailable. Synthetic exposure tests demonstrate the aggregation,
including partial-data handling. The claimed 2.3% failure rate has not been
independently reproduced and is not inferred from the number of components.
