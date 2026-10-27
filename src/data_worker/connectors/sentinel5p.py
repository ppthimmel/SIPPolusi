"""Konektor Sentinel-5P TROPOMI NO2 (ITroposphericColumn, ``fetch_tropospheric_column``).

Produk sama dengan data latih TI-AI-01: L2 NO2 NRTI dari katalog STAC Microsoft
Planetary Computer (``sentinel-5p-l2-netcdf``). Granule NetCDF4 dibaca lewat
HTTP Range (``RangeFile``) sehingga hanya variabel yang dipakai yang diunduh.
Seleksi piksel, QA, sel 5 km, dan agregasi median per (scene, sel) adalah
porting ``processing/sentinel5p.py`` bundle data latih, sehingga nilainya identik.

Tanpa akun: token SAS anonim Planetary Computer diminta per siklus dan tidak
pernah dicatat. Dokumen Desain (Tabel 3.18/3.19) menyebut Copernicus Data Space
Ecosystem dengan token OAuth2; Planetary Computer dipakai karena data latih
berasal dari sana. Batas: 120 detik per permintaan, tiga kali.
"""

from __future__ import annotations

import datetime as dt

import numpy as np
import pandas as pd

from feature_matrix.grid import BBOX, grid_id, to_grid_xy

from .http import FormatError, HttpClient, Policy, RangeFile, safe_url
from . import stac

SOURCE = "sentinel5p"
COLLECTION = "sentinel-5p-l2-netcdf"
QUERY = {"s5p:product_name": {"eq": "no2"}, "s5p:processing_mode": {"eq": "NRTI"}}
POLICY = Policy(timeout_s=120, tries=3)
PROCESSING_VERSION = "sentinel5p-no2-3"
QA_MIN = 75                 # qa_value disimpan uint8 × 0,01; 75 = 0,75
RES = 5000.0
LEFT, TOP, N = 675000.0, 9340000.0, 10
LOOKBACK_HOURS = 48
MAX_PRODUCTS = 6


def search(client: HttpClient, now: dt.datetime, hours: int = LOOKBACK_HOURS) -> list[dict]:
    return stac.search(client, COLLECTION, now - dt.timedelta(hours=hours), now, QUERY)


def _attr(f, key):
    value = f.attrs.get(key)
    return value.decode() if isinstance(value, bytes) else (None if value is None else str(value))


def read_granule(h5, scene: str) -> tuple[pd.DataFrame, dict]:
    """Granule terbuka (``h5py.File``) → piksel di dalam bbox dan rekaman scene.

    Kolom piksel mengikuti ``PIXEL_SCHEMA`` bundle; ``observed_at`` adalah
    ``time_utc`` scanline piksel.
    """
    try:
        p = h5["PRODUCT"]
        lat = np.asarray(p["latitude"][0], dtype="float64")
        lon = np.asarray(p["longitude"][0], dtype="float64")
        produced = pd.Timestamp(_attr(h5, "date_created"))
        coverage = pd.Timestamp(_attr(h5, "time_coverage_start")), pd.Timestamp(_attr(h5, "time_coverage_end"))
    except KeyError as exc:
        raise FormatError(f"sentinel5p: {scene} tidak memuat variabel {exc}") from exc
    if pd.isna(produced):
        raise FormatError(f"sentinel5p: {scene} tanpa atribut date_created")
    lon0, lat0, lon1, lat1 = BBOX
    inside = (lon >= lon0) & (lon <= lon1) & (lat >= lat0) & (lat <= lat1)
    record = dict(scene_id=scene, source_pixels=0, valid_pixels=0, observed_start=coverage[0],
                  observed_end=coverage[1], produced_at=produced)
    rows = np.nonzero(inside.any(axis=1))[0]
    if not len(rows):
        return pd.DataFrame(), record
    r0, r1 = int(rows.min()), int(rows.max()) + 1
    sub = inside[r0:r1]
    qa = np.asarray(p["qa_value"][0, r0:r1]).astype(int)[sub]
    no2 = np.asarray(p["nitrogendioxide_tropospheric_column"][0, r0:r1]).astype("float64")[sub]
    prec = np.asarray(p["nitrogendioxide_tropospheric_column_precision"][0, r0:r1]).astype("float64")[sub]
    times = np.asarray(p["time_utc"][0, r0:r1])
    times = [t.decode() if isinstance(t, bytes) else t for t in times]
    scanline, ground = np.nonzero(sub)
    scanline = scanline + r0
    lat, lon = lat[r0:r1][sub], lon[r0:r1][sub]
    no2[np.abs(no2) > 1e30] = np.nan
    prec[np.abs(prec) > 1e30] = np.nan
    east, north = to_grid_xy(lon, lat)
    col = np.floor((east - LEFT) / RES).astype(int)
    row = np.floor((TOP - north) / RES).astype(int)
    keep = (col >= 0) & (col < N) & (row >= 0) & (row < N)
    passes = (qa >= QA_MIN) & (qa != 255) & np.isfinite(no2)
    pixels = pd.DataFrame({
        "scene_id": scene, "observed_at": pd.to_datetime([times[s - r0] for s in scanline], utc=True),
        "source_scanline": scanline, "source_ground_pixel": ground, "latitude": lat, "longitude": lon,
        "easting": east, "northing": north, "grid_id": grid_id(row, col), "no2_raw_mol_m2": no2,
        "no2_mol_m2": np.where(passes, no2, np.nan), "no2_precision_mol_m2": prec, "qa_value": qa * 0.01,
        "passes_qa": passes, "produced_at": produced})[keep].reset_index(drop=True)
    record.update(source_pixels=len(pixels), valid_pixels=int(pixels.passes_qa.sum()))
    if len(pixels):
        record.update(observed_start=pixels.observed_at.min(), observed_end=pixels.observed_at.max())
    return pixels, record


def observations(pixels: pd.DataFrame) -> pd.DataFrame:
    """Per scene × sel 5 km yang memuat piksel sumber: median piksel lolos QA (skema ``observations.parquet``)."""
    columns = ["scene_id", "grid_id", "observed_start_at", "observed_at", "produced_at", "no2_mol_m2",
               "no2_precision_mol_m2", "n_valid_pixels", "available"]
    if pixels.empty:
        return pd.DataFrame(columns=columns)
    pixels = pixels.copy()
    pixels.loc[~pixels.passes_qa, "no2_precision_mol_m2"] = np.nan
    span = pixels.groupby(["scene_id", "grid_id"]).agg(
        observed_start_at=("observed_at", "min"), observed_at=("observed_at", "max"),
        produced_at=("produced_at", "first"))
    good = pixels[pixels.passes_qa].groupby(["scene_id", "grid_id"]).agg(
        no2_mol_m2=("no2_mol_m2", "median"), no2_precision_mol_m2=("no2_precision_mol_m2", "median"),
        n_valid_pixels=("no2_mol_m2", "size"))
    obs = span.join(good).reset_index()
    obs["n_valid_pixels"] = obs.n_valid_pixels.fillna(0).astype("int64")
    obs["available"] = obs.n_valid_pixels > 0
    return obs.sort_values(["observed_start_at", "scene_id", "grid_id"]).reset_index(drop=True)[columns]


def fetch_product(client: HttpClient, item: dict, token: str) -> tuple[pd.DataFrame, dict]:
    """Satu item STAC → observasi per sel dan rekaman scene."""
    import h5py

    href = item.get("assets", {}).get("no2", {}).get("href")
    if not href:
        raise FormatError(f"sentinel5p: item {item.get('id')} tanpa aset no2")
    remote = RangeFile(client, stac.signed(href, token))
    try:
        with h5py.File(remote, "r") as h5:
            pixels, record = read_granule(h5, item["id"])
    except OSError as exc:
        if remote.error is not None:       # galat transport di bawah h5py, bukan format berkas
            raise remote.error from None
        raise FormatError(f"sentinel5p: {safe_url(href)} bukan HDF5/NetCDF4 yang terbaca") from exc
    record.update(source_url=safe_url(href), bytes_read=remote.fetched, catalog_created_at=item.get(
        "properties", {}).get("created"))
    return observations(pixels), record
