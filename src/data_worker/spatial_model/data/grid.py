"""Grid analisis Spatial Downscaling Model (Dokumen Desain, subbab 4.3.2).

Seluruh fitur diselaraskan pada grid reguler 100 m dalam proyeksi UTM zona
48S (EPSG:32748). Jarak IDW juga dihitung pada proyeksi ini, dalam meter,
bukan pada derajat lintang-bujur.
"""

from __future__ import annotations

import dataclasses
import math

import numpy as np
from pyproj import Transformer

GRID_CRS = "EPSG:32748"
WGS84 = "EPSG:4326"

_TO_GRID = Transformer.from_crs(WGS84, GRID_CRS, always_xy=True)
_TO_WGS84 = Transformer.from_crs(GRID_CRS, WGS84, always_xy=True)


def to_grid_xy(lon, lat) -> tuple[np.ndarray, np.ndarray]:
    """lon/lat WGS 84 → x/y meter pada EPSG:32748."""
    x, y = _TO_GRID.transform(np.asarray(lon, dtype=float), np.asarray(lat, dtype=float))
    return np.asarray(x, dtype=float), np.asarray(y, dtype=float)


def to_lonlat(x, y) -> tuple[np.ndarray, np.ndarray]:
    lon, lat = _TO_WGS84.transform(np.asarray(x, dtype=float), np.asarray(y, dtype=float))
    return np.asarray(lon, dtype=float), np.asarray(lat, dtype=float)


@dataclasses.dataclass(frozen=True, slots=True)
class GridSpec:
    """Grid reguler pada EPSG:32748; (x0, y0) adalah sudut kiri bawah sel pertama."""

    x0: float
    y0: float
    nx: int
    ny: int
    cell_m: float = 100.0
    crs: str = GRID_CRS

    @classmethod
    def from_bbox(cls, bbox: tuple[float, float, float, float], cell_m: float = 100.0) -> "GridSpec":
        """Grid yang menutup bbox (min_lon, min_lat, max_lon, max_lat), sel diselaraskan ke kelipatan cell_m."""
        min_lon, min_lat, max_lon, max_lat = bbox
        xs, ys = to_grid_xy([min_lon, max_lon, min_lon, max_lon], [min_lat, min_lat, max_lat, max_lat])
        x0 = math.floor(xs.min() / cell_m) * cell_m
        y0 = math.floor(ys.min() / cell_m) * cell_m
        nx = int(math.ceil((xs.max() - x0) / cell_m))
        ny = int(math.ceil((ys.max() - y0) / cell_m))
        return cls(x0=x0, y0=y0, nx=nx, ny=ny, cell_m=cell_m)

    @property
    def n_cells(self) -> int:
        return self.nx * self.ny

    def cell_centers(self) -> tuple[np.ndarray, np.ndarray]:
        """Pusat sel, urutan baris demi baris dari selatan (indeks sel = iy * nx + ix)."""
        cx = self.x0 + (np.arange(self.nx) + 0.5) * self.cell_m
        cy = self.y0 + (np.arange(self.ny) + 0.5) * self.cell_m
        xx, yy = np.meshgrid(cx, cy)
        return xx.ravel(), yy.ravel()

    def cell_index(self, x, y) -> np.ndarray:
        """Indeks sel untuk titik x/y; -1 bila di luar grid."""
        ix = np.floor((np.asarray(x, dtype=float) - self.x0) / self.cell_m).astype(int)
        iy = np.floor((np.asarray(y, dtype=float) - self.y0) / self.cell_m).astype(int)
        inside = (ix >= 0) & (ix < self.nx) & (iy >= 0) & (iy < self.ny)
        return np.where(inside, iy * self.nx + ix, -1)

    def as_dict(self) -> dict:
        return dataclasses.asdict(self)
