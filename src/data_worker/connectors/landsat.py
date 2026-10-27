"""Konektor Landsat 8/9 Collection 2 Level-2 (ISatelliteArchive): NDVI, NDBI, LST pada grid 100 m.

Produk sama dengan data latih TI-AI-01: ``landsat-c2-l2`` Planetary Computer,
awan < 40%, LC08/LC09. Band COG dibaca per jendela yang memuat wilayah studi
(tanpa mengunduh scene utuh), lalu dirata-rata ke grid 100 m EPSG:32748. Masker
QA, ambang, dan agregasi adalah porting ``processing/landsat.py`` bundle data
latih (``CONFIG``), sehingga nilai per sel identik.

Dokumen Desain (Tabel 3.18) menyebut Google Earth Engine atau USGS EarthExplorer;
Planetary Computer dipakai karena data latih berasal dari sana dan tidak
memerlukan akun. Frekuensi bulanan pada desain; konektor memeriksa katalog
sekali sehari (``CHECK_INTERVAL``) karena satu scene baru terbit tiap ±8 hari.
"""

from __future__ import annotations

import datetime as dt
import types

import numpy as np
import pandas as pd

from feature_matrix.grid import BBOX, GRID_N, GRID_RES, GRID_X_MIN, GRID_Y_TOP

from .http import FormatError, HttpClient, Policy, SourceError, safe_url
from . import stac

SOURCE = "landsat"
COLLECTION = "landsat-c2-l2"
QUERY = {"eo:cloud_cover": {"lt": 40}}
POLICY = Policy(timeout_s=120, tries=3)
PROCESSING_VERSION = "landsat-xland-2"
FEATURES = ["ndvi", "ndbi", "lst_c"]
CONFIG = dict(min_valid_fraction=0.5, max_lst_uncertainty_k=2.0, min_cloud_distance_km=1.0,
              lst_min_c=-10.0, lst_max_c=80.0, exclude_high_aerosol=True)
BANDS = ["red", "nir08", "swir16", "lwir11", "qa_pixel", "qa_radsat", "qa_aerosol", "qa", "cdist"]
LOOKBACK_DAYS = 32
MAX_PRODUCTS = 1
GDAL_ENV = dict(GDAL_DISABLE_READDIR_ON_OPEN="EMPTY_DIR", CPL_VSIL_CURL_ALLOWED_EXTENSIONS=".tif",
                GDAL_HTTP_TIMEOUT=str(int(POLICY.timeout_s)), GDAL_HTTP_MAX_RETRY=str(POLICY.tries),
                GDAL_HTTP_RETRY_DELAY="2", GDAL_HTTP_MULTIRANGE="SERIAL", VSI_CACHE="TRUE")


def search(client: HttpClient, now: dt.datetime, days: int = LOOKBACK_DAYS) -> list[dict]:
    items = stac.search(client, COLLECTION, now - dt.timedelta(days=days), now, QUERY)
    return [x for x in items if x["id"].startswith(("LC08_L2SP_", "LC09_L2SP_"))]


def _bits(qa, *positions):
    return np.logical_or.reduce([((qa >> p) & 1) == 1 for p in positions])


def _aggregate(values, mask, src):
    """Rata-rata luas piksel 30 m valid dan fraksi valid pada grid 100 m."""
    from rasterio.transform import Affine
    from rasterio.warp import Resampling, reproject

    total = np.zeros((GRID_N, GRID_N))
    cover = np.zeros((GRID_N, GRID_N))
    if src.crs.to_epsg() == 32648:   # zona sama, false northing utara: geser tanpa warping
        dst_crs = src.crs
        dst_transform = Affine(GRID_RES, 0, GRID_X_MIN, 0, -GRID_RES, GRID_Y_TOP - 1e7)
    else:
        dst_crs, dst_transform = "EPSG:32748", Affine(GRID_RES, 0, GRID_X_MIN, 0, -GRID_RES, GRID_Y_TOP)
    for data, out in ((np.where(mask, values, 0.0), total), (mask.astype("float64"), cover)):
        reproject(data, out, src_transform=src.transform, src_crs=src.crs, dst_transform=dst_transform,
                  dst_crs=dst_crs, resampling=Resampling.average)
    with np.errstate(invalid="ignore", divide="ignore"):
        return total / cover, cover


def compute_scene(bands: dict, src, config: dict = CONFIG) -> dict:
    """Band mentah (DN terkemas) → larik 445 × 445: nilai fitur dan fraksi valid."""
    def sr(b):
        return np.where(bands[b] == 0, np.nan, bands[b] * 2.75e-5 - 0.2)
    red, nir, swir = sr("red"), sr("nir08"), sr("swir16")
    qp = bands["qa_pixel"]
    clear = ~_bits(qp, 0, 1, 2, 3, 4, 5)        # fill, dilated cloud, cirrus, cloud, shadow, snow
    clear_sr = clear & (((bands["qa_aerosol"] >> 6) & 3) != 3) if config["exclude_high_aerosol"] else clear
    unit = lambda *xs: np.logical_and.reduce([(x > 0) & (x <= 1) for x in xs])  # noqa: E731
    with np.errstate(invalid="ignore", divide="ignore"):
        ndvi = (nir - red) / (nir + red)
        ndbi = (swir - nir) / (swir + nir)
    st = bands["lwir11"]
    lst = np.where(st == 0, np.nan, st * 0.00341802 + 149.0) - 273.15
    unc = np.where(bands["qa"] == -9999, np.nan, bands["qa"] * 0.01)
    cdist = np.where(bands["cdist"] == -9999, np.nan, bands["cdist"] * 0.01)
    with np.errstate(invalid="ignore"):
        masks = {
            "ndvi": (ndvi, clear_sr & unit(red, nir)),
            "ndbi": (ndbi, clear_sr & unit(nir, swir)),
            "lst_c": (lst, clear & (bands["qa_radsat"] == 0) & np.isfinite(lst)
                      & (unc <= config["max_lst_uncertainty_k"]) & (cdist >= config["min_cloud_distance_km"])
                      & (lst >= config["lst_min_c"]) & (lst <= config["lst_max_c"]))}
    result = {}
    for name, (values, mask) in masks.items():
        mean, cover = _aggregate(values, mask & np.isfinite(values), src)
        result[name], result[name + "_fraction"] = mean, cover
    return result


def cell_layers(arrays: dict, rows: np.ndarray, cols: np.ndarray, config: dict = CONFIG) -> tuple[dict, dict]:
    """Larik grid → layer per sel aktif (urutan ``active_cells()``) dan jumlah sel valid."""
    layers, valid = {}, {}
    for f in FEATURES:
        value, fraction = arrays[f][rows, cols], arrays[f + "_fraction"][rows, cols]
        available = (fraction >= config["min_valid_fraction"]) & np.isfinite(value)
        layers[f] = np.where(available, value, np.nan).astype("float32")
        layers[f + "_valid_fraction"] = fraction.astype("float32")
        valid[f] = int(available.sum())
    return layers, valid


def read_bands(item: dict, token: str) -> tuple[dict, object]:
    """Jendela wilayah studi dari setiap band COG; ``src`` membawa CRS dan transform jendela."""
    import rasterio
    from rasterio.warp import transform_bounds
    from rasterio.windows import Window, from_bounds

    bands, src = {}, None
    window = None
    with rasterio.Env(**GDAL_ENV):
        for key in BANDS:
            href = item.get("assets", {}).get(key, {}).get("href")
            if not href:
                raise FormatError(f"landsat: item {item['id']} tanpa aset {key}")
            try:
                with rasterio.open(stac.signed(href, token)) as dataset:
                    if window is None:
                        bounds = transform_bounds("EPSG:4326", dataset.crs, *BBOX)
                        raw = from_bounds(*bounds, transform=dataset.transform)
                        c0, r0 = max(0, int(np.floor(raw.col_off))), max(0, int(np.floor(raw.row_off)))
                        c1 = min(dataset.width, int(np.ceil(raw.col_off + raw.width)))
                        r1 = min(dataset.height, int(np.ceil(raw.row_off + raw.height)))
                        if c1 <= c0 or r1 <= r0:
                            raise FormatError(f"landsat: {item['id']} tidak memotong wilayah studi")
                        window = Window(c0, r0, c1 - c0, r1 - r0)
                        src = types.SimpleNamespace(crs=dataset.crs, transform=dataset.window_transform(window))
                    bands[key] = dataset.read(1, window=window)
            except rasterio.errors.RasterioIOError as exc:
                # Pesan GDAL memuat URL bertoken; yang dicatat hanya URL tanpa query.
                raise SourceError(f"landsat: band {key} {safe_url(href)} tidak terbaca") from None
    return bands, src


def produced_at(client: HttpClient, item: dict, token: str) -> pd.Timestamp:
    href = item.get("assets", {}).get("mtl.json", {}).get("href")
    if not href:
        raise FormatError(f"landsat: item {item['id']} tanpa aset mtl.json")
    mtl = client.get_json(stac.signed(href, token))
    try:
        value = mtl["LANDSAT_METADATA_FILE"]["LEVEL2_PROCESSING_RECORD"]["DATE_PRODUCT_GENERATED"]
    except (KeyError, TypeError) as exc:
        raise FormatError(f"landsat: mtl.json {item['id']} tanpa DATE_PRODUCT_GENERATED") from exc
    stamp = pd.Timestamp(value)
    return stamp.tz_localize("UTC") if stamp.tzinfo is None else stamp.tz_convert("UTC")


def fetch_product(client: HttpClient, item: dict, token: str, rows: np.ndarray, cols: np.ndarray) -> tuple[dict, dict]:
    produced = produced_at(client, item, token)
    bands, src = read_bands(item, token)
    layers, valid = cell_layers(compute_scene(bands, src), rows, cols)
    props = item["properties"]
    record = dict(scene_id=item["id"], observed_at=pd.Timestamp(props["datetime"]).tz_convert("UTC"),
                  produced_at=produced, catalog_created_at=pd.Timestamp(props.get("created")),
                  cloud_cover=props.get("eo:cloud_cover"), valid_cells=valid,
                  source_url=safe_url(item["assets"]["red"]["href"]))
    return layers, record
