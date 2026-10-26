"""Backend request planner: RouteRequest -> solver input (TI-SE-05, Tabel 3.6 R5 to R11).

    models.py      ValidatedRouteRequest, PlannedEdge, WeightStatus, SolverInput
    validation.py  validate_route_request, resolve_mode (UT-BE-02, UT-BE-03)
    weights.py     attach_edge_weights (UT-BE-07)
    assembler.py   build_solver_input: validate, read both caches in parallel,
                   assemble the subgraph, attach weights
    schemas.py     RouteRequest and solver input API models
    router.py      POST /v1/routes/solver-input

Calling the Multi-Objective Route Model (IRouteSolve) is TI-SE-06.
"""
