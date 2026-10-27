"""get_exposure_surface(bbox, zoom) (Tabel 3.6, IExposureSurface, PF-11).

zoom >= 14   one Feature per edge (LineString), bbox at most 25 km².
zoom < 14    edges aggregated to grid cells (Polygon): 250 m at zoom 13, 500 m at
             zoom 12, 1 km at zoom 11 and below, so the response stays bounded.

Every Feature carries the combined exposure index and both concentrations, so the
Frontend switches layers (combined, PM2.5, NO2) without a new request.

The grid is fixed in degrees (cell size in metres converted at Jakarta's latitude,
anchored at lon 0, lat 0), so a cell keeps its id and value while the map is
panned. Cells are axis-aligned in WGS 84 and within 0.5 % of the nominal size
across the study area.

Caching (TI-SE-08 QA): the body depends only on the data and the query, never on
the request (no request_id; the ID is in the X-Request-ID header), so it can be
shared. It is sent with Cache-Control "public, no-cache" and a weak ETag over the
graph version, the time window (start, completion time, model) and data_stale
plus the query. A cache must revalidate before reuse: while nothing changed the
answer is 304 without running the surface query, and once a newer window is
complete the ETag changes and the new data is served at once.
"""
from __future__ import annotations

import hashlib
import math
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any

from osm.errors import InvalidRequest
from osm.models import BBox
from osm.repository import OsmRepository
from pollution.models import WindowInfo
from pollution.repository import PollutionRepository
from solver.geojson import COORDINATE_ORDER, CRS84, DISCLAIMER, EXPOSURE_INDEX_DEFINITION

from .config import ApiSettings

EDGE_MIN_ZOOM = 14
# Class limits of the heatmap colour scale (Subbab 3.1): 1.0 is exactly the WHO guideline.
EXPOSURE_INDEX_CLASSES = [0.5, 1.0, 2.0, 3.0]

# Metres per degree at the reference latitude (WGS 84 series expansion).
REFERENCE_LAT = -6.2
_PHI = math.radians(REFERENCE_LAT)
M_PER_DEG_LAT = 111_132.954 - 559.822 * math.cos(2 * _PHI) + 1.175 * math.cos(4 * _PHI)
M_PER_DEG_LON = 111_412.84 * math.cos(_PHI) - 93.5 * math.cos(3 * _PHI)

EDGE_UNITS = {
    "exposure_index": "index",
    "pm25_ugm3": "µg/m³",
    "no2_ugm3": "µg/m³",
    "confidence_score": "fraction 0–1",
}
CELL_UNITS = {**EDGE_UNITS, "exposure_index_max": "index", "edge_count": "count", "cell_size_m": "m"}
GRID_AGGREGATION = ("Rata-rata berbobot panjang ruas atas ruas yang memiliki nilai; ruas masuk ke sel yang memuat "
                    "titik tengah kotak pembatasnya. exposure_index_max = nilai ruas tertinggi di sel.")


def cell_size_m(zoom: int) -> int | None:
    """None for per-edge Features (zoom >= 14), else the grid cell size in metres."""
    if zoom >= EDGE_MIN_ZOOM:
        return None
    return {13: 250, 12: 500}.get(zoom, 1000)


@dataclass(frozen=True, slots=True)
class Grid:
    cell_m: int
    ix0: int
    ix1: int  # exclusive
    iy0: int
    iy1: int  # exclusive

    @property
    def dlon(self) -> float:
        return self.cell_m / M_PER_DEG_LON

    @property
    def dlat(self) -> float:
        return self.cell_m / M_PER_DEG_LAT

    @classmethod
    def covering(cls, bbox: BBox, cell_m: int) -> Grid:
        dlon, dlat = cell_m / M_PER_DEG_LON, cell_m / M_PER_DEG_LAT
        return cls(cell_m,
                   math.floor(bbox.min_lon / dlon), math.ceil(bbox.max_lon / dlon),
                   math.floor(bbox.min_lat / dlat), math.ceil(bbox.max_lat / dlat))

    @property
    def cell_count(self) -> int:
        return (self.ix1 - self.ix0) * (self.iy1 - self.iy0)

    def envelope(self) -> BBox:
        return BBox(self.ix0 * self.dlon, self.iy0 * self.dlat, self.ix1 * self.dlon, self.iy1 * self.dlat)

    def polygon(self, ix: int, iy: int) -> list[list[list[float]]]:
        """Closed ring, counter-clockwise (RFC 7946 section 3.1.6)."""
        w, e = round(ix * self.dlon, 6), round((ix + 1) * self.dlon, 6)
        s, n = round(iy * self.dlat, 6), round((iy + 1) * self.dlat, 6)
        return [[[w, s], [e, s], [e, n], [w, n], [w, s]]]


def parse_surface_bbox(text: str, zoom: int, settings: ApiSettings) -> tuple[BBox, Grid | None]:
    """The requested bbox and, below zoom 14, the grid covering it; 400 when too large for the zoom."""
    try:
        bbox = BBox.parse(text)
    except ValueError as exc:
        raise InvalidRequest(
            "bbox tidak valid; gunakan min_lon,min_lat,max_lon,max_lat (WGS 84) dengan min < max.") from exc
    cell_m = cell_size_m(zoom)
    if cell_m is None:
        if bbox.area_km2() > settings.surface_edge_max_km2:
            raise InvalidRequest(
                f"bbox terlalu luas untuk zoom {zoom} ({bbox.area_km2():.1f} km²); per ruas (zoom ≥ {EDGE_MIN_ZOOM}) "
                f"batasnya {settings.surface_edge_max_km2:g} km². Perkecil bbox atau gunakan zoom lebih kecil.")
        return bbox, None
    grid = Grid.covering(bbox, cell_m)
    if grid.cell_count > settings.surface_max_cells:
        raise InvalidRequest(
            f"bbox terlalu luas untuk zoom {zoom} ({grid.cell_count} sel {cell_m} m); batasnya "
            f"{settings.surface_max_cells} sel. Perkecil bbox atau gunakan zoom lebih kecil.")
    return bbox, grid


CACHE_CONTROL = "public, no-cache"
# Part of the ETag: bump when the response body changes shape, so cached bodies of
# the old shape are not revalidated as current.
SURFACE_FORMAT = "surface-1"


@dataclass(frozen=True, slots=True)
class SurfaceSource:
    """What the surface is computed from: two cheap reads, enough for the ETag."""

    window: WindowInfo
    graph_version: str
    data_stale: bool


async def surface_source(osm: OsmRepository, pollution: PollutionRepository) -> SurfaceSource:
    window = await pollution.resolve_window("latest")  # 503 cache_empty without a complete window
    graph_version = (await osm.get_active_version()).version
    stale, _ = pollution.staleness(window)
    return SurfaceSource(window, graph_version, stale)


def surface_etag(source: SurfaceSource, bbox: BBox, zoom: int, layer: str) -> str:
    """Weak ETag (the body may be gzip-encoded) of everything the body depends on."""
    w = source.window
    key = "|".join([
        SURFACE_FORMAT, source.graph_version, _iso(w.time_window_start),
        _iso(w.completed_at) if w.completed_at else "", w.model_version or "", str(source.data_stale),
        ",".join(repr(v) for v in bbox.as_list()), str(zoom), layer,
    ])
    return f'W/"{hashlib.sha256(key.encode()).hexdigest()[:24]}"'


def etag_matches(if_none_match: str | None, etag: str) -> bool:
    """RFC 9110 weak comparison against an If-None-Match header."""
    if not if_none_match:
        return False
    if if_none_match.strip() == "*":
        return True
    opaque = etag.removeprefix("W/")
    return any(tag.strip().removeprefix("W/") == opaque for tag in if_none_match.split(","))


async def get_exposure_surface(*, bbox: BBox, grid: Grid | None, zoom: int, layer: str, source: SurfaceSource,
                               pollution: PollutionRepository) -> dict[str, Any]:
    window, graph_version = source.window, source.graph_version
    if grid is None:
        rows = await pollution.read_surface_edges(window, graph_version, bbox)
        features = [{
            "type": "Feature",
            "geometry": {"type": "LineString", "coordinates": r["coordinates"]},
            "properties": {k: r[k] for k in ("edge_id", "exposure_index", "pm25_ugm3", "no2_ugm3",
                                             "confidence_score", "estimation_source")},
        } for r in rows if r["coordinates"]]
    else:
        rows = await pollution.read_surface_cells(window, graph_version, grid.envelope(), (grid.dlon, grid.dlat),
                                                  (grid.ix0, grid.ix1, grid.iy0, grid.iy1))
        features = [{
            "type": "Feature",
            "geometry": {"type": "Polygon", "coordinates": grid.polygon(r["ix"], r["iy"])},
            "properties": {
                "cell_id": f"{grid.cell_m}m:{r['ix']}:{r['iy']}",
                **{k: r[k] for k in ("edge_count", "exposure_index", "exposure_index_max", "pm25_ugm3",
                                     "no2_ugm3", "confidence_score")},
            },
        } for r in rows]

    return {
        "type": "FeatureCollection",
        "features": features,
        "metadata": {
            "kind": "edge" if grid is None else "grid",
            "zoom": zoom,
            "layer": layer,
            "bbox": bbox.as_list(),
            "cell_size_m": None if grid is None else grid.cell_m,
            "aggregation": None if grid is None else GRID_AGGREGATION,
            "feature_count": len(features),
            "time_window_start": _iso(window.time_window_start),
            "time_window_end": _iso(window.time_window.end),
            "data_stale": source.data_stale,
            "graph_version": graph_version,
            "model_version": {"downscaling": window.model_version},
            "crs": CRS84,
            "coordinate_order": COORDINATE_ORDER,
            "units": EDGE_UNITS if grid is None else CELL_UNITS,
            "exposure_index_definition": EXPOSURE_INDEX_DEFINITION,
            "exposure_index_classes": EXPOSURE_INDEX_CLASSES,
            "disclaimer": DISCLAIMER,
        },
    }


def _iso(value: datetime) -> str:
    return value.astimezone(timezone.utc).isoformat().replace("+00:00", "Z")
