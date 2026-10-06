"""Agregator grid ke ruas jalan (Dokumen Desain Tabel 3.8, B25, UT-SDM-07).

Metode: setiap segmen garis ruas dipotong tepat pada batas sel grid 100 m
(EPSG:32748). Nilai ruas adalah rata-rata nilai sel yang dilalui, dibobot
panjang potongan ruas di dalam setiap sel:

    c_e = Σ_k (ℓ_ek · c_k) / Σ_k ℓ_ek

dengan ℓ_ek panjang potongan ruas e di sel k. Polutan diagregasi sendiri-sendiri
dan sel yang polutannya kosong tidak ikut dalam pembagi polutan tersebut. Indeks
paparan gabungan dihitung SETELAH agregasi dari polutan yang tersedia (PF-06).
Potongan di luar grid tidak dipakai; ruas yang seluruhnya di luar grid tidak
menghasilkan EdgeWeight dan dicatat sebagai ruas tanpa estimasi (UT-SDM-07e).

Pemotongan dihitung sekali per (versi graf, GridSpec) oleh ``edge_cell_pieces``.
Tabel potongan tersebut (edge_id, cell_id, length_m) sekaligus menjadi jejak
sel grid sumber setiap EdgeWeight.
"""

from __future__ import annotations

import hashlib
import json

import numpy as np
import pandas as pd

from spatial_model.grid import GridSpec, to_grid_xy
from spatial_model.idw import exposure_index

#: Kolom nilai sel yang diagregasi ke ruas, bila tersedia pada grid_prediction.
VALUE_COLUMNS = ("pm25_ugm3", "no2_ugm3", "confidence_score", "nearest_station_m")


def grid_spec_id(spec: GridSpec) -> str:
    """Penanda pendek GridSpec untuk kunci tabel potongan dan manifest."""
    payload = json.dumps(spec.as_dict(), sort_keys=True).encode()
    return hashlib.sha256(payload).hexdigest()[:12]


def _group_arange(counts: np.ndarray) -> np.ndarray:
    """0..c-1 untuk setiap kelompok berukuran c, digabung."""
    total = int(counts.sum())
    if total == 0:
        return np.zeros(0, dtype=np.int64)
    starts = np.repeat(np.cumsum(counts) - counts, counts)
    return np.arange(total) - starts


def split_segments_by_grid(x0, y0, x1, y1, spec: GridSpec):
    """Potong segmen (x0, y0)–(x1, y1) pada garis grid.

    Mengembalikan (indeks segmen, cell_id, panjang meter) per potongan;
    cell_id bernilai -1 untuk potongan di luar grid.
    """
    x0, y0, x1, y1 = (np.asarray(a, dtype=float) for a in (x0, y0, x1, y1))
    n = len(x0)
    dx, dy = x1 - x0, y1 - y0
    seg_len = np.hypot(dx, dy)
    ts, segs = [np.zeros(n), np.ones(n)], [np.arange(n), np.arange(n)]
    for a0, a1, d, origin in ((x0, x1, dx, spec.x0), (y0, y1, dy, spec.y0)):
        i0 = np.floor((a0 - origin) / spec.cell_m)
        i1 = np.floor((a1 - origin) / spec.cell_m)
        lo = np.minimum(i0, i1)
        count = np.abs(i1 - i0).astype(np.int64)
        seg = np.repeat(np.arange(n), count)
        k = _group_arange(count) + 1
        boundary = origin + (lo[seg] + k) * spec.cell_m
        ts.append((boundary - a0[seg]) / d[seg])
        segs.append(seg)
    t = np.concatenate(ts)
    s = np.concatenate(segs)
    order = np.lexsort((t, s))
    t, s = t[order], s[order]
    same = s[1:] == s[:-1]
    t_a, t_b, seg = t[:-1][same], t[1:][same], s[:-1][same]
    piece = (t_b - t_a) * seg_len[seg]
    keep = piece > 1e-9
    t_mid = (t_a + t_b)[keep] / 2
    seg = seg[keep]
    cell = spec.cell_index(x0[seg] + t_mid * dx[seg], y0[seg] + t_mid * dy[seg])
    return seg, cell, piece[keep]


def edge_cell_pieces(edge_ids, coords_lonlat: np.ndarray, coord_edge_index: np.ndarray,
                     spec: GridSpec) -> tuple[pd.DataFrame, pd.DataFrame]:
    """Potongan ruas per sel grid.

    ``coords_lonlat`` (m, 2) adalah titik seluruh ruas (WGS 84) berurutan, dan
    ``coord_edge_index`` (m,) posisi ruas (0..len(edge_ids)-1) untuk setiap titik,
    seperti keluaran ``shapely.get_coordinates(geoms, return_index=True)``.

    Mengembalikan ``pieces`` (edge_id, cell_id, length_m; satu baris per
    pasangan ruas-sel di dalam grid) dan ``edges`` (edge_id, length_m,
    inside_length_m, inside_fraction).
    """
    edge_ids = np.asarray(edge_ids)
    coords_lonlat = np.asarray(coords_lonlat, dtype=float)
    coord_edge_index = np.asarray(coord_edge_index)
    x, y = to_grid_xy(coords_lonlat[:, 0], coords_lonlat[:, 1])
    same = coord_edge_index[1:] == coord_edge_index[:-1]
    seg_edge = coord_edge_index[:-1][same]
    seg, cell, length = split_segments_by_grid(x[:-1][same], y[:-1][same], x[1:][same], y[1:][same], spec)
    pos = seg_edge[seg]
    n = len(edge_ids)
    total = np.bincount(pos, weights=length, minlength=n)
    inside = cell >= 0
    inside_len = np.bincount(pos[inside], weights=length[inside], minlength=n)
    pieces = (
        pd.DataFrame({"edge_id": edge_ids[pos[inside]], "cell_id": cell[inside], "length_m": length[inside]})
        .groupby(["edge_id", "cell_id"], as_index=False, sort=True)["length_m"].sum()
    )
    edges = pd.DataFrame({
        "edge_id": edge_ids,
        "length_m": total,
        "inside_length_m": inside_len,
        "inside_fraction": np.divide(inside_len, total, out=np.zeros(n), where=total > 0),
    })
    return pieces, edges


def aggregate_grid_to_edges(grid_prediction: pd.DataFrame, pieces: pd.DataFrame,
                            edges: pd.DataFrame | None = None) -> tuple[pd.DataFrame, pd.DataFrame]:
    """Rata-rata nilai sel berbobot panjang potongan ruas (Tabel 3.8, B25).

    ``grid_prediction`` diindeks ``cell_id`` (atau memiliki kolom ``cell_id``)
    dengan kolom ``pm25_ugm3``, ``no2_ugm3``, dan opsional ``confidence_score``
    serta ``nearest_station_m``. ``pieces`` berasal dari ``edge_cell_pieces``.

    Mengembalikan (``weights``, ``missing``): ``weights`` satu baris per ruas
    yang memiliki sekurang-kurangnya satu polutan, dengan kolom nilai di atas,
    ``exposure_index``, dan ``n_cells``; ``missing`` berisi edge_id ruas tanpa
    estimasi (di luar grid atau seluruh selnya kosong).
    """
    grid = grid_prediction.set_index("cell_id") if "cell_id" in grid_prediction.columns else grid_prediction
    columns = [c for c in VALUE_COLUMNS if c in grid.columns]
    joined = pieces.join(grid[columns], on="cell_id", how="left")
    w = joined["length_m"].to_numpy()
    codes, edge_ids = pd.factorize(joined["edge_id"], sort=True)
    n = len(edge_ids)
    out = pd.DataFrame({"edge_id": edge_ids, "n_cells": np.bincount(codes, minlength=n)})
    for col in columns:
        v = joined[col].to_numpy(dtype=float)
        ok = np.isfinite(v)
        num = np.bincount(codes[ok], weights=w[ok] * v[ok], minlength=n)
        den = np.bincount(codes[ok], weights=w[ok], minlength=n)
        out[col] = np.divide(num, den, out=np.full(n, np.nan), where=den > 0)
    available = {p: out[f"{p}_ugm3"].to_numpy() for p in ("pm25", "no2") if f"{p}_ugm3" in out}
    out["exposure_index"] = exposure_index(available)
    has_value = out["exposure_index"].notna()
    weights = out[has_value].reset_index(drop=True)
    all_edges = edges["edge_id"] if edges is not None else pd.Series(edge_ids)
    missing = pd.DataFrame({"edge_id": np.setdiff1d(all_edges.to_numpy(), weights["edge_id"].to_numpy())})
    return weights, missing
