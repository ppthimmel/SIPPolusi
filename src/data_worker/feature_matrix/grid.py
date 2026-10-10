"""Grid 100 m bersama keluaran preprocessing TI-AI-01 (Landsat, OSM, GEOS-CF).

EPSG:32748, 445 × 445 sel, sudut kiri atas (676900, 9336600). ID sel berupa
string ``rRRRR_cCCCC`` dengan baris 0 di sisi utara, sama dengan ID Landsat dan
OSM. Lookup GEOS-CF memakai indeks bilangan bulat ``gy × 445 + gx`` dari sisi
selatan; ``gy = 444 − baris``. Sel aktif (195.782) adalah sel yang pusatnya di
dalam bbox studi 106,6–107,0 BT / 6,4–6,0 LS yang diproyeksikan.
"""

from __future__ import annotations

import numpy as np
import pandas as pd
import shapely

from spatial_model.grid import GRID_CRS, to_grid_xy, to_lonlat  # noqa: F401  (GRID_CRS diekspor ulang)

BBOX = (106.6, -6.4, 107.0, -6.0)            # lon_min, lat_min, lon_max, lat_max
GRID_RES = 100.0
GRID_X_MIN = 676900.0
GRID_Y_TOP = 9336600.0
GRID_N = 445


def grid_id(rows, cols) -> list[str]:
    return [f"r{int(r):04d}_c{int(c):04d}" for r, c in zip(np.atleast_1d(rows), np.atleast_1d(cols))]


def row_col(x, y) -> tuple[np.ndarray, np.ndarray]:
    """Baris/kolom sel yang memuat titik x/y (meter); bisa di luar 0..444."""
    rows = np.floor((GRID_Y_TOP - np.asarray(y, dtype=float)) / GRID_RES).astype(int)
    cols = np.floor((np.asarray(x, dtype=float) - GRID_X_MIN) / GRID_RES).astype(int)
    return rows, cols


def _bbox_polygon_utm():
    lon0, lat0, lon1, lat1 = BBOX
    xs, ys = to_grid_xy([lon0, lon1, lon1, lon0], [lat0, lat0, lat1, lat1])
    return shapely.Polygon(zip(xs, ys))


def active_cells() -> pd.DataFrame:
    """Sel aktif dengan koordinat pusat, urut ``grid_id``."""
    cols, rows = np.meshgrid(np.arange(GRID_N), np.arange(GRID_N))
    x = GRID_X_MIN + GRID_RES * cols + GRID_RES / 2
    y = GRID_Y_TOP - GRID_RES * rows - GRID_RES / 2
    mask = shapely.contains_xy(_bbox_polygon_utm(), x, y)
    rows, cols, x, y = rows[mask], cols[mask], x[mask], y[mask]
    lon, lat = to_lonlat(x, y)
    cells = pd.DataFrame({"grid_id": grid_id(rows, cols), "easting": x, "northing": y,
                          "longitude": lon, "latitude": lat})
    return cells.sort_values("grid_id").reset_index(drop=True)


def nearest_sensor_distance(easting, northing, sensor_lon, sensor_lat, chunk: int = 20_000) -> np.ndarray:
    """Jarak Euclidean (m, EPSG:32748) dari pusat sel ke stasiun terdekat; NaN bila tanpa stasiun."""
    easting = np.asarray(easting, dtype=float)
    northing = np.asarray(northing, dtype=float)
    out = np.full(len(easting), np.nan)
    if len(sensor_lon) == 0:
        return out
    sx, sy = to_grid_xy(sensor_lon, sensor_lat)
    for i in range(0, len(easting), chunk):
        dx = easting[i:i + chunk, None] - sx[None, :]
        dy = northing[i:i + chunk, None] - sy[None, :]
        out[i:i + chunk] = np.sqrt(dx * dx + dy * dy).min(axis=1)
    return out
