# Route time, exposure and weighted cost (TI-AI-06)

For a route R consisting of directed edges e, the planner computes edge time
`t_e = length_e / speed_mode` in seconds, rounding to three decimal places.
The default speeds are 1.3 m/s walking and 4.2 m/s cycling; the backend can
override them through `OSM_WALK_SPEED_MPS` and `OSM_BIKE_SPEED_MPS`. These are
constant-speed assumptions, not measured speeds, and exclude traffic lights,
congestion, slope and surface delays. The standalone baseline uses the same
defaults, but accepts explicit per-edge walk or bike times in its CSV input.

The backend reports travel time `T(R) = Σ t_e / 60` in minutes. For a
dimensionless edge exposure index `I_e`, it reports cumulative index exposure
`E(R) = Σ I_e × t_e / 60` in index·minutes. The index is the mean of PM2.5
and NO2 concentrations divided by their respective WHO 2021 24-hour guideline
values (15 and 25 µg/m³); if only one pollutant is available, its normalized
concentration is used. This is an exposure proxy, not an inhaled dose.

The backend also reports single-pollutant concentration exposure
`Σ C_e × t_e / 60` in µg·min/m³, where `C_e` is the requested pollutant's
concentration in µg/m³. It is null if any route edge lacks that concentration.
For the standalone baseline, the corresponding total uses seconds instead of
minutes and is reported in µg·s/m³; it is null if any edge is missing, with
coverage and status reported separately.

For the backend's index-based calculation, an edge without an exposure-index
estimate is filled with the median of usable estimates in the request bounding
box, marked `imputed_median`, and assigned zero confidence. The response counts
imputed edges. If there are no usable estimates for a median, the planner returns
`cache_empty`; it does not silently substitute zero. Missing pollutant
concentrations are not imputed. Stale data is flagged rather than silently
presented as fresh. See [planner details](../src/backend/planner/README.md) and
[baseline details](../src/backend/route_model/README.md).

The deterministic weighted A* provider and development mock compute the dimensionless objective
`α × T(R)/T_ref + β × E(R)/E_ref`, where the reference values are computed
on the fastest route, and α and β sum to one. It calculates equivalent edge
costs using seconds for both numerator and reference. If a reference total is
zero, both use 1.0 as its denominator (in the units of that reference). This
preserves defined, nonnegative costs but is not relative normalization when the
reference is zero. The `weighted` provider finds the minimum-cost route with
A*, using the fastest route as reference; select it with
`ROUTE_SOLVER_PROVIDER=weighted`. The mock uses Dijkstra. The A* baseline
minimizes travel time alone and does not apply α/β; the HTTP solver is a
separate provider intended for MORL. The current API's `route_source: policy`
means preference-applied, not necessarily MORL: `solver: weighted`, model
version `weighted-astar-1` and the response note identify deterministic A*.
The configured default remains `mock`. See
[solver providers](../src/backend/solver/README.md).

The shared numeric functions in [`costs.py`](../src/backend/route_model/costs.py)
return raw seconds, index-seconds and dimensionless costs. The planner uses the
time function, backend and standalone route metrics use the exposure function,
and both deterministic providers use the cost functions. A future MORL model
can consume them without API formatting. The weighted search accepts the same
`SolverInput` as the planner. Its lower-bound proof and reproducible Dijkstra
comparison are in [the verification notebook](../notebooks/weighted_astar_verification.ipynb).

The baseline's [small-graph test](../src/backend/tests/test_baseline.py) gives
an example: two edges each take 10 s, with concentrations 10 and 20 µg/m³.
Thus `T(R) = 20 s` and `E(R) = 10 × 10 + 20 × 10 = 300 µg·s/m³`.

For a weighted-cost example, suppose the fastest route has `T_ref = 20 s`
and `E_ref = 80 index·s`, while a cleaner route has `T = 30 s` and
`E = 30 index·s`. Their normalized values are `(1, 1)` and `(1.5, 0.375)`.
With `(α, β) = (0.8, 0.2)` their costs are 1 and 1.275, so the fastest
route wins. With `(0.2, 0.8)` the costs are 1 and 0.6, so the cleaner
route wins. The [mock solver test](../src/backend/solver/tests/test_mock.py)
checks both calculations and choices against this two-route graph.
