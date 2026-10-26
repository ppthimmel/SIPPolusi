"""Read-only access to the OSM Cache DB (schema `osm`) for the Backend.

Layout:
    config.py      settings from the environment / src/backend/.env
    models.py      domain types (Point, BBox, Mode, RoadGraph, Subgraph, ...)
    db.py          asyncpg pool, read-only session, error translation
    repository.py  SQL against osm.graph_version, osm.road_node, osm.road_edge
    graph.py       in-memory graph helpers (compute_request_bbox, snap_to_graph,
                   assemble_request_subgraph)
    errors.py      uniform error schema {"error": {code, message, request_id}}
    schemas.py     API request/response models
    router.py      REST endpoints under /v1/osm
    app.py         standalone FastAPI app and lifespan for mounting elsewhere

Run standalone from src/backend:  uvicorn osm.app:app --reload
"""
