"""Route solving orchestration (TI-SE-06): IRouteSolve, SolveResult, solver providers.

    contract.py      RouteSolver protocol (IRouteSolve), SolveRequest, SolverReply,
                     RouteResult, SolveResult, SolverError
    deadline.py      compute_deadline (UT-BE-08)
    mock.py          deterministic mock solver with fault injection
    baseline.py      in-process fastest-route baseline (route_model.astar_osm)
    http_client.py   client for the Multi-Objective Route Model service (POST /internal/v1/solve)
    factory.py       provider chosen by ROUTE_SOLVER_PROVIDER, not by endpoint
    orchestrator.py  call_route_solve (timeout, error classification) and SolveResult assembly
    router.py        POST /v1/routes/solve
"""
