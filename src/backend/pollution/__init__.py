"""Read-only access to the Spatial Pollution Cache DB (schema `pollution`) for the Backend.

Counterpart of src/backend/osm: it reuses the same asyncpg pool (one PostgreSQL
instance hosts both cache schemas, Tabel 3.17) and the same error schema. Rows
are written by the Spatial Downscaling Model (src/data_worker/spatial_model/cache.py).

    config.py      POLLUTION_* settings
    models.py      TimeWindow, WindowInfo, EdgeWeight, PollutionRead
    repository.py  read_pollution_weight, time window selection, data_stale
    schemas.py     API response models
    router.py      REST endpoints under /v1/pollution
"""
