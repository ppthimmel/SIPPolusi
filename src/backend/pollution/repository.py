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
_SQL_WEIGHTS = """
SELECT p.edge_id, p.time_window_start,
       round(p.pm25_ugm3::numeric, 4)::float8 AS pm25_ugm3,
       round(p.no2_ugm3::numeric, 4)::float8 AS no2_ugm3,
       round(p.exposure_index::numeric, 4)::float8 AS exposure_index,
       round(p.confidence_score::numeric, 4)::float8 AS confidence_score,
       p.background_source, p.estimation_source,
       p.graph_version, p.model_version, p.written_at,
       e.walk_allowed, e.bike_allowed
FROM pollution.edge_pollution p
LEFT JOIN osm.road_edge e ON e.edge_id = p.edge_id AND e.graph_version = p.graph_version
WHERE p.time_window_start = $1
  AND p.geom && ST_MakeEnvelope($2, $3, $4, $5, 4326)
  AND ST_Intersects(p.geom, ST_MakeEnvelope($2, $3, $4, $5, 4326))
ORDER BY p.edge_id
"""

_WEIGHT_FIELDS = tuple(EdgeWeight.__dataclass_fields__)

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
    ):
        self._db = db
        self._osm = osm
        self._stale_after_s = stale_after_s
        self._clock = clock

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

    async def read_pollution_weight(
        self,
        bbox: BBox,
        time_window: TimeWindow | str = "latest",
        *,
        mode: Mode | None = None,
        graph_version: str | None = ACTIVE,
    ) -> PollutionRead:
        """read_pollution_weight(bbox, time_window="latest") from Tabel 3.11 (R6 and R25).

        graph_version: "active" (default) keeps only rows estimated on the active
        road graph; a version name keeps that version; None keeps every row.
        mode: when given, keeps only edges that mode may use.
        """
        window = await self.resolve_window(time_window)
        version = (await self._osm.get_active_version()).version if graph_version == ACTIVE else graph_version
        async with self._db.connection() as conn:
            rows = await conn.fetch(_SQL_WEIGHTS, window.time_window_start, *bbox.as_list())

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
            weights.append(EdgeWeight(**{k: r[k] for k in _WEIGHT_FIELDS}))
        if excluded:
            logger.debug("pollution rows excluded in bbox: %s", dict(excluded))

        stale, age = self.staleness(window)
        return PollutionRead(window=window, graph_version=version, weights=weights,
                             data_stale=stale, age_s=age, excluded=dict(excluded))
