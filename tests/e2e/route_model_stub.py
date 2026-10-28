"""Stand-in Multi-Objective Route Model for the E2E tests (IRouteSolve, POST /internal/v1/solve).

The real policy (TI-AI-10/11) does not exist yet. This service answers the same
contract so the Backend's HTTP client, deadline, provenance checks and fallback
can be exercised end to end. Behaviour is switched at runtime:

    POST /__mode?name=valid       fastest route, labelled policy (default)
    POST /__mode?name=slow        sleeps 30 s (past every deadline)
    POST /__mode?name=error       HTTP 500
    POST /__mode?name=mislabel    route_source policy with preference_applied false
    POST /__mode?name=no_route    422 no_route_found

Run with: uvicorn route_model_stub:app --port 8100 (from tests/e2e).
"""
from __future__ import annotations

import asyncio
import gzip
import heapq
import json

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse

app = FastAPI(title="route-model-stub")
STATE = {"mode": "valid", "calls": 0}
MODES = {"valid", "slow", "error", "mislabel", "no_route"}


@app.post("/__mode")
async def set_mode(name: str):
    if name not in MODES:
        return JSONResponse({"error": f"unknown mode {name}"}, status_code=400)
    STATE["mode"] = name
    return STATE


@app.get("/__mode")
async def get_mode():
    return STATE


def fastest(body: dict) -> list[int]:
    arcs: dict[int, list[tuple[float, int, int]]] = {}
    for e in body["edges"]:
        arcs.setdefault(e["u"], []).append((e["travel_time_s"], e["v"], e["id"]))
        if e["bidirectional"]:
            arcs.setdefault(e["v"], []).append((e["travel_time_s"], e["u"], e["id"]))
    start, goal = body["origin_node"], body["destination_node"]
    best, prev, queue = {start: 0.0}, {}, [(0.0, start)]
    while queue:
        d, n = heapq.heappop(queue)
        if n == goal:
            break
        if d > best.get(n, float("inf")):
            continue
        for t, m, eid in sorted(arcs.get(n, [])):
            if d + t < best.get(m, float("inf")):
                best[m], prev[m] = d + t, (n, eid)
                heapq.heappush(queue, (d + t, m))
    path, n = [], goal
    while n != start:
        n, eid = prev[n]
        path.append(eid)
    return path[::-1]


@app.post("/internal/v1/solve")
async def solve(request: Request):
    STATE["calls"] += 1
    body = json.loads(gzip.decompress(await request.body()))
    mode = STATE["mode"]
    if mode == "slow":
        await asyncio.sleep(30)
    if mode == "error":
        return JSONResponse({"error": {"code": "internal_error"}}, status_code=500)
    if mode == "no_route":
        return JSONResponse({"error": {"code": "no_route_found"}}, status_code=422)
    path = fastest(body)
    return {"route_source": "policy", "fallback_reason": None, "model_version": "stub-e2e-1",
            "preference_applied": mode != "mislabel",
            "recommended": {"edge_ids": path}, "comparison": {"edge_ids": path}}
