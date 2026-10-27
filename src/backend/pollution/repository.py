"""Queries against schema `pollution` (Tabel 3.11 read_pollution_weight, IPollutionWeightRead).

- Only time windows with status 'complete' are read; 'writing' windows are
  invisible, so a half-written window never reaches the Backend (IT-K-05, IT-K-08).
- "latest" means the latest complete window. Rows are filtered by bbox (GiST on
  geom), by graph_version (default: the active OSM graph, so rows estimated on an
  older graph do not attach to new edge ids) and optionally by travel mode.
- No complete window raises CacheUnavailable(reason=cache_empty); an old window
  is still returned with data_stale = true (Tabel 3.15).
"""
from __future__ import annotations

import logging
from collections import Counter
from collections.abc import Callable
from datetime import datetime

from osm.db import OsmDatabase
from osm.errors import CacheUnavailable, NotFound
from osm.models import BBox, Mode
from osm.repository import OsmRepository

from .models import UTC, EdgeWeight, PollutionRead, TimeWindow, WindowInfo

logger = logging.getLogger("pollution")

ACTIVE = "active"

_WINDOW_COLUMNS = "time_window_start, status, pollutant, coverage_ratio, model_version, completed_at"

_SQL_LATEST_COMPLETE = f"""
SELECT {_WINDOW_COLUMNS}
FROM pollution.pollution_window
WHERE status = 'complete'
ORDER BY time_window_start DESC
LIMIT 1
"""

_SQL_WINDOW = f"""
SELECT {_WINDOW_COLUMNS}
FROM pollution.pollution_window
WHERE time_window_start = $1
"""

_SQL_WINDOWS = f"""
SELECT {_WINDOW_COLUMNS}
FROM pollution.pollution_window
ORDER BY time_window_start DESC
LIMIT $1
"""

# Access flags come from the road edge of the same graph_version as the row.
# The bbox test is the same as the road graph's (osm.repository, bounding boxes
# overlap): a stricter ST_Intersects here would drop weights of subgraph edges
# whose bounding box touches the bbox but whose line does not, and the planner
# would then count them as edges without an estimate (TI-SE-10, D-01).
_SQL_WEIGHTS_TEMPLATE = """
SELECT p.edge_id, p.time_window_start,
       round(p.pm25_ugm3::numeric, 4)::float8 AS pm25_ugm3,
       round(p.no2_ugm3::numeric, 4)::float8 AS no2_ugm3,
       round(p.exposure_index::numeric, 4)::float8 AS exposure_index,
       round(p.confidence_score::numeric, 4)::float8 AS confidence_score,
       p.background_source, p.estimation_source,
       p.graph_version, p.model_version{lineage},
       e.walk_allowed, e.bike_allowed
FROM pollution.edge_pollution p
LEFT JOIN osm.road_edge e ON e.edge_id = p.edge_id AND e.graph_version = p.graph_version
WHERE p.time_window_start = $1
  AND p.geom && ST_MakeEnvelope($2, $3, $4, $5, 4326)
ORDER BY p.edge_id
"""

# Per-edge lineage. One inference run writes the same values on every row of its
# window, and model_metadata alone is ~0.3 kB per row: for a 9 km trip (64k edges)
# these columns double the rows sent to the route planner (40.8 vs 20.1 MB), which
# never reads them. lineage=False leaves them out.
_LINEAGE_COLUMNS = ("written_at", "model_metadata", "trace_run_id")
_SQL_WEIGHTS = _SQL_WEIGHTS_TEMPLATE.format(lineage="".join(f", p.{c}" for c in _LINEAGE_COLUMNS))
_SQL_WEIGHTS_WITHOUT_LINEAGE = _SQL_WEIGHTS_TEMPLATE.format(lineage="")

_WEIGHT_FIELDS = tuple(EdgeWeight.__dataclass_fields__)

# The run's per-pollutant provenance (source, model version, fallback reason) from
# one row of the window, for routes read without lineage: one run writes a whole
# window at once, so every row carries the same model_metadata. No index starts
# with time_window_start (the key is (edge_id, time_window_start)), so a bare
# LIMIT 1 seq-scans until it meets the window: up to 1.8 s on Railway, depending
# on where the window's rows sit in the heap. ORDER BY edge_id walks the primary
# key instead and stops at the first edge, which every window covers (0.09 ms).
_SQL_WINDOW_MODEL_METADATA = """
SELECT model_metadata
FROM pollution.edge_pollution
WHERE time_window_start = $1
ORDER BY edge_id
LIMIT 1
"""

# Exposure surface, zoom >= 14 (get_exposure_surface, TI-SE-08): one row per edge of
# graph_version with its stored geometry, [[lon, lat], ...] rounded to 6 decimals.
_SQL_SURFACE_EDGES = """
SELECT p.edge_id,
       round(p.exposure_index::numeric, 4)::float8 AS exposure_index,
       round(p.pm25_ugm3::numeric, 4)::float8 AS pm25_ugm3,
       round(p.no2_ugm3::numeric, 4)::float8 AS no2_ugm3,
       round(p.confidence_score::numeric, 4)::float8 AS confidence_score,
       p.estimation_source,
       ST_AsGeoJSON(p.geom, 6)::json -> 'coordinates' AS coordinates
FROM pollution.edge_pollution p
WHERE p.time_window_start = $1
  AND p.graph_version = $2
  AND p.geom && ST_MakeEnvelope($3, $4, $5, $6, 4326)
  AND ST_Intersects(p.geom, ST_MakeEnvelope($3, $4, $5, $6, 4326))
ORDER BY p.edge_id
"""

# Exposure surface, zoom < 14: edges aggregated to grid cells of $7 x $8 degrees.
# An edge belongs to the cell holding the centre of its bounding box, so the
# envelope ($3..$6, aligned to the grid) holds every edge of the cells inside it
# and the index range ($9..$12) drops the partial cells around it. Averages are
# weighted by edge length (planar degrees: within Jakarta the scale differs by
# under 1 % between axes) over edges that have the value.
_SQL_SURFACE_CELLS = """
WITH e AS (
    SELECT p.exposure_index, p.pm25_ugm3, p.no2_ugm3, p.confidence_score,
           greatest(ST_Length(p.geom), 1e-12) AS w,
           floor((ST_XMin(p.geom) + ST_XMax(p.geom)) / 2 / $7)::bigint AS ix,
           floor((ST_YMin(p.geom) + ST_YMax(p.geom)) / 2 / $8)::bigint AS iy
    FROM pollution.edge_pollution p
    WHERE p.time_window_start = $1
      AND p.graph_version = $2
      AND p.geom && ST_MakeEnvelope($3, $4, $5, $6, 4326)
)
SELECT ix, iy, count(*) AS edge_count,
       round((sum(exposure_index * w) / nullif(sum(w) FILTER (WHERE exposure_index IS NOT NULL), 0))::numeric, 4)::float8
           AS exposure_index,
       round(max(exposure_index)::numeric, 4)::float8 AS exposure_index_max,
       round((sum(pm25_ugm3 * w) / nullif(sum(w) FILTER (WHERE pm25_ugm3 IS NOT NULL), 0))::numeric, 4)::float8
           AS pm25_ugm3,
       round((sum(no2_ugm3 * w) / nullif(sum(w) FILTER (WHERE no2_ugm3 IS NOT NULL), 0))::numeric, 4)::float8
           AS no2_ugm3,
       round((sum(confidence_score * w) / nullif(sum(w) FILTER (WHERE confidence_score IS NOT NULL), 0))::numeric, 4)::float8
           AS confidence_score
FROM e
WHERE ix >= $9 AND ix < $10 AND iy >= $11 AND iy < $12
GROUP BY ix, iy
ORDER BY iy, ix
"""


def _utcnow() -> datetime:
    return datetime.now(UTC)


class PollutionRepository:
    def __init__(
        self,
        db: OsmDatabase,
        osm: OsmRepository,
        stale_after_s: float = 3 * 3600,
        clock: Callable[[], datetime] = _utcnow,
        weights_work_mem: str | None = None,
    ):
        self._db = db
        self._osm = osm
        self._stale_after_s = stale_after_s
        self._clock = clock
        self._weights_work_mem = weights_work_mem

    @staticmethod
    def _window(row) -> WindowInfo:
        return WindowInfo(**dict(row))

    def staleness(self, window: WindowInfo) -> tuple[bool, float]:
        """(data_stale, age in seconds), age measured from the end of the window."""
        age = (self._clock() - window.time_window.end).total_seconds()
        return age > self._stale_after_s, max(age, 0.0)

    async def list_windows(self, limit: int = 24) -> list[WindowInfo]:
        async with self._db.connection() as conn:
            rows = await conn.fetch(_SQL_WINDOWS, limit)
        return [self._window(r) for r in rows]

    async def latest_complete_window(self) -> WindowInfo | None:
        async with self._db.connection() as conn:
            row = await conn.fetchrow(_SQL_LATEST_COMPLETE)
        return self._window(row) if row else None

    async def get_window(self, window: TimeWindow) -> WindowInfo | None:
        async with self._db.connection() as conn:
            row = await conn.fetchrow(_SQL_WINDOW, window.start)
        return self._window(row) if row else None

    async def resolve_window(self, time_window: TimeWindow | str = "latest") -> WindowInfo:
        """The complete window to read: the latest one, or the requested one."""
        if time_window == "latest":
            window = await self.latest_complete_window()
            if window is None:
                raise CacheUnavailable(
                    "Spatial Pollution Cache DB belum memiliki time window berstatus complete.",
                    CacheUnavailable.CACHE_EMPTY,
                )
            return window
        if not isinstance(time_window, TimeWindow):
            raise ValueError("time_window harus 'latest' atau TimeWindow")
        window = await self.get_window(time_window)
        label = f"{time_window.start:%Y-%m-%dT%H:%M:%SZ}"
        if window is None:
            raise NotFound(f"Time window {label} tidak ada di Spatial Pollution Cache DB.")
        if window.status != "complete":
            raise CacheUnavailable(f"Time window {label} masih berstatus {window.status!r}.",
                                   CacheUnavailable.CACHE_EMPTY)
        return window

    async def read_edge_trace(self, edge_id: int, time_window: TimeWindow | str = "latest") -> dict:
        """Durable lineage for one completed cached edge; explicit legacy status if absent."""
        import json
        window = await self.resolve_window(time_window)
        async with self._db.connection() as conn:
            row = await conn.fetchrow("""SELECT e.trace_run_id, r.manifest, r.trace_sha256
                FROM pollution.edge_pollution e
                LEFT JOIN pollution.inference_run r ON r.run_id = e.trace_run_id
                WHERE e.edge_id = $1 AND e.time_window_start = $2""", edge_id, window.time_window_start)
            if row is None:
                raise NotFound(f"EdgeWeight {edge_id} tidak ada pada time window tersebut.")
            if row["trace_run_id"] is None:
                return dict(status="legacy_unavailable", edge_id=edge_id,
                            time_window_start=window.time_window_start, run_id=None, cells=[])
            cells = await conn.fetch("""SELECT c.edge_id, c.length_m, g.*
                FROM pollution.edge_cell c JOIN pollution.grid_prediction g USING (run_id, cell_id)
                WHERE c.run_id = $1 AND c.edge_id = $2 ORDER BY c.cell_id""", row["trace_run_id"], edge_id)
            manifest = row["manifest"]
            if isinstance(manifest, str):
                manifest = json.loads(manifest)
            return dict(status="available", edge_id=edge_id, time_window_start=window.time_window_start,
                        run_id=row["trace_run_id"], manifest=manifest, trace_sha256=row["trace_sha256"],
                        cells=[dict(c) for c in cells])

    async def read_surface_edges(self, window: WindowInfo, graph_version: str, bbox: BBox) -> list[dict]:
        """Edges of graph_version intersecting bbox with their geometry (zoom >= 14)."""
        async with self._db.connection() as conn:
            rows = await conn.fetch(_SQL_SURFACE_EDGES, window.time_window_start, graph_version, *bbox.as_list())
        return [dict(r) for r in rows]

    async def read_surface_cells(self, window: WindowInfo, graph_version: str, envelope: BBox,
                                 cell_deg: tuple[float, float], index_range: tuple[int, int, int, int]) -> list[dict]:
        """Grid cells (ix, iy) with length-weighted averages (zoom < 14).

        envelope must be aligned to the grid; index_range is (ix0, ix1, iy0, iy1),
        end exclusive, the cells inside envelope."""
        async with self._db.connection() as conn:
            rows = await conn.fetch(_SQL_SURFACE_CELLS, window.time_window_start, graph_version,
                                    *envelope.as_list(), *cell_deg, *index_range)
        return [dict(r) for r in rows]

    async def window_model_metadata(self, window: WindowInfo) -> dict:
        """model_metadata of the run that wrote window ({} for legacy rows): which
        pollutants came from the registered model and which fell back to IDW."""
        async with self._db.connection() as conn:
            value = await conn.fetchval(_SQL_WINDOW_MODEL_METADATA, window.time_window_start)
        if isinstance(value, str):
            import json
            value = json.loads(value)
        return value if isinstance(value, dict) else {}

    async def read_pollution_weight(
        self,
        bbox: BBox,
        time_window: TimeWindow | str = "latest",
        *,
        mode: Mode | None = None,
        graph_version: str | None = ACTIVE,
        lineage: bool = True,
    ) -> PollutionRead:
        """read_pollution_weight(bbox, time_window="latest") from Tabel 3.11 (R6 and R25).

        graph_version: "active" (default) keeps only rows estimated on the active
        road graph; a version name keeps that version; None keeps every row.
        mode: when given, keeps only edges that mode may use.
        lineage: False leaves written_at, model_metadata and trace_run_id at their
        defaults (None, {}, None); every other field is read as usual.
        """
        window = await self.resolve_window(time_window)
        version = (await self._osm.get_active_version()).version if graph_version == ACTIVE else graph_version
        sql = _SQL_WEIGHTS if lineage else _SQL_WEIGHTS_WITHOUT_LINEAGE
        async with self._db.connection() as conn:
            if self._weights_work_mem:
                # A long trip joins and sorts tens of thousands of rows; with the
                # server default (4 MB) both spill to disk. SET LOCAL ends with the
                # transaction, so pooled connections keep the server default.
                async with conn.transaction(readonly=True):
                    await conn.execute("SELECT set_config('work_mem', $1, true)", self._weights_work_mem)
                    rows = await conn.fetch(sql, window.time_window_start, *bbox.as_list())
            else:
                rows = await conn.fetch(sql, window.time_window_start, *bbox.as_list())

        excluded: Counter[str] = Counter()
        weights: list[EdgeWeight] = []
        for r in rows:
            if version is not None and r["graph_version"] != version:
                excluded["other_graph_version"] += 1
                continue
            if mode is not None:
                allowed = r["walk_allowed"] if mode is Mode.WALK else r["bike_allowed"]
                if allowed is None:
                    excluded["edge_not_in_graph"] += 1
                    continue
                if not allowed:
                    excluded["not_allowed_for_mode"] += 1
                    continue
            data = {k: r[k] for k in _WEIGHT_FIELDS if k in r}
            metadata = data.get("model_metadata", {})
            if isinstance(metadata, str):
                import json
                metadata = json.loads(metadata)
            data["model_metadata"] = metadata
            weights.append(EdgeWeight(**data))
        if excluded:
            logger.debug("pollution rows excluded in bbox: %s", dict(excluded))

        stale, age = self.staleness(window)
        return PollutionRead(window=window, graph_version=version, weights=weights,
                             data_stale=stale, age_s=age, excluded=dict(excluded))
