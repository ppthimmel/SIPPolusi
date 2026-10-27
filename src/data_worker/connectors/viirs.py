"""Konektor VIIRS Black Marble VNP46A2 (ISatelliteArchive): radiansi malam harian 500 m.

Produk sama dengan data latih TI-AI-01: VNP46A2 Collection 2, tile h28v09,
dicari lewat NASA CMR dan diunduh dari LAADS DAAC dengan token Earthdata
(``EARTHDATA_TOKEN``, header ``Authorization: Bearer``). Sel tetap pada grid
asli 15″; ekstraksinya baris 1441-1536 dan kolom 1585-1680 tile, yaitu 9.216
sel yang sama dengan ``dataset_processed/viirs/daily.parquet``. QA dan kolom
keluaran adalah porting ``processing/viirs.py`` bundle data latih.

Tabel 3.19: 120 detik per berkas, tiga kali, jeda eksponensial. Tanpa token,
sumber ini dicatat ``disabled``.
"""

from __future__ import annotations

import datetime as dt
import io
import re

import numpy as np
import pandas as pd

from .http import FormatError, HttpClient, Policy, safe_url

SOURCE = "viirs"
CMR_URL = "https://cmr.earthdata.nasa.gov/search/granules.json"
SHORT_NAME = "VNP46A2"
TILE = "h28v09"
LAADS_PREFIX = "https://data.laadsdaac.earthdatacloud.nasa.gov/"
POLICY = Policy(timeout_s=120, tries=3)
PROCESSING_VERSION = "viirs-ntl-3"
GROUP = "HDFEOS/GRIDS/VIIRS_Grid_DNB_2d/Data Fields/"
NTL_FILL = np.float32(-999.9)
ROWS = (1441, 1537)          # indeks tile, setengah terbuka
COLS = (1585, 1681)
CELL_DEG = 1 / 240
LOOKBACK_DAYS = 21
MAX_PRODUCTS = 3
MAX_BYTES = 150 << 20
NAME_RE = re.compile(r"(?P<product>V(?:NP|J1|J2)46A2)\.A(?P<year>\d{4})(?P<doy>\d{3})\.(?P<tile>h\d{2}v\d{2})\."
                     r"(?P<collection>\d{3})\.(?P<prod>\d{13})")
#: Layer per sel yang disimpan, dengan dtype penyimpanan.
LAYERS = {"ntl": "float32", "ntl_gap_filled": "float32", "latest_high_quality_age_days": "float32",
          "mandatory_quality_flag": "uint8", "cloud_mask": "uint16", "snow_flag": "uint8",
          "lunar_irradiance": "float32", "longitude": "float64", "latitude": "float64"}


def cell_ids() -> list[str]:
    rows, cols = np.meshgrid(np.arange(*ROWS), np.arange(*COLS), indexing="ij")
    return [f"{TILE}_r{r:04d}_c{c:04d}" for r, c in zip(rows.ravel(), cols.ravel())]


def cell_corners() -> tuple[np.ndarray, np.ndarray]:
    """``longitude``/``latitude`` seperti data latih: sudut barat laut sel (nilai larik lat/lon berkas)."""
    rows, cols = np.meshgrid(np.arange(*ROWS), np.arange(*COLS), indexing="ij")
    h, v = int(TILE[1:3]), int(TILE[4:6])
    return (-180 + 10 * h + cols.ravel() * CELL_DEG), (90 - 10 * v - rows.ravel() * CELL_DEG)


def parse_name(scene: str) -> tuple[pd.Timestamp, pd.Timestamp, str]:
    match = NAME_RE.match(scene)
    if not match:
        raise FormatError(f"viirs: nama granule tidak dikenal: {scene}")
    date = pd.Timestamp(f"{match['year']}-01-01", tz="UTC") + pd.Timedelta(days=int(match["doy"]) - 1)
    produced = pd.to_datetime(match["prod"], format="%Y%j%H%M%S").tz_localize("UTC")
    return date, produced, match["tile"]


def search(client: HttpClient, now: dt.datetime, days: int = LOOKBACK_DAYS) -> list[dict]:
    """Granule tile h28v09 pada ``days`` hari terakhir, terlama dulu, dengan tautan unduh LAADS."""
    start = now - dt.timedelta(days=days)
    params = {"short_name": SHORT_NAME, "bounding_box": "106.6,-6.4,107.0,-6.0", "page_size": 200,
              "temporal": f"{start:%Y-%m-%dT00:00:00Z},{now:%Y-%m-%dT%H:%M:%SZ}"}
    payload = client.get_json(CMR_URL, params=params)
    try:
        entries = payload["feed"]["entry"]
    except (KeyError, TypeError) as exc:
        raise FormatError("viirs: respons CMR tanpa feed.entry") from exc
    granules = []
    for entry in entries:
        name = entry.get("producer_granule_id", "")
        if f".{TILE}." not in name:
            continue
        href = next((link["href"] for link in entry.get("links", [])
                     if link.get("href", "").startswith(LAADS_PREFIX) and link["href"].endswith(".h5")), None)
        if href is None:
            raise FormatError(f"viirs: granule {name} tanpa tautan unduh LAADS")
        date, produced, _ = parse_name(name)
        granules.append({"id": name, "href": href, "observation_date": date, "produced_at": produced,
                         "updated": entry.get("updated")})
    return sorted(granules, key=lambda g: (g["observation_date"], g["produced_at"]))


def valid_mask(ntl, mqf, cloud, snow):
    """Flag wajib 0, malam, mutu cloud mask sedang/tinggi, pasti cerah, tanpa bayangan/sirus/salju,
    Snow_Flag 0, radiansi finite tidak negatif (sama dengan data latih)."""
    night = (cloud & 1) == 0
    cloud_quality = (cloud >> 4) & 3
    cloud_detect = (cloud >> 6) & 3
    shadow, cirrus, snow_surface = (cloud >> 8) & 1, (cloud >> 9) & 1, (cloud >> 10) & 1
    return ((mqf == 0) & night & (cloud_quality >= 2) & (cloud_detect == 0) & (shadow == 0)
            & (cirrus == 0) & (snow_surface == 0) & (snow == 0) & np.isfinite(ntl) & (ntl >= 0) & (ntl != NTL_FILL))


def read_granule(h5, scene: str) -> tuple[dict, dict]:
    """Granule terbuka → layer per sel (urutan ``cell_ids()``) dan rekaman produk."""
    date, produced, tile = parse_name(scene)
    if tile != TILE:
        raise FormatError(f"viirs: tile {tile}, bukan {TILE}")
    (r0, r1), (c0, c1) = ROWS, COLS
    try:
        g = h5[GROUP]
        lat, lon = g["lat"][r0:r1], g["lon"][c0:c1]
        read = lambda name: np.asarray(g[name][r0:r1, c0:c1])  # noqa: E731
        ntl, gap = read("DNB_BRDF-Corrected_NTL"), read("Gap_Filled_DNB_BRDF-Corrected_NTL")
        mqf, cloud, snow = read("Mandatory_Quality_Flag"), read("QF_Cloud_Mask"), read("Snow_Flag")
        lhq, lunar = read("Latest_High_Quality_Retrieval"), read("DNB_Lunar_Irradiance")
    except KeyError as exc:
        raise FormatError(f"viirs: {scene} tidak memuat {exc}") from exc
    expected_lon, expected_lat = cell_corners()
    if (len(lat) != r1 - r0 or len(lon) != c1 - c0
            or not np.allclose(lat, expected_lat.reshape(r1 - r0, c1 - c0)[:, 0], atol=1e-6)
            or not np.allclose(lon, expected_lon.reshape(r1 - r0, c1 - c0)[0], atol=1e-6)):
        raise FormatError(f"viirs: koordinat {scene} berbeda dengan grid data latih")
    valid = valid_mask(ntl, mqf, cloud, snow)
    lat2, lon2 = np.meshgrid(np.asarray(lat, "float64"), np.asarray(lon, "float64"), indexing="ij")
    gap_ok = np.isfinite(gap) & (gap != NTL_FILL) & (gap >= 0)
    layers = {
        "ntl": np.where(valid, ntl, np.nan).astype("float32").ravel(),
        "ntl_gap_filled": np.where(gap_ok, gap, np.nan).astype("float32").ravel(),
        "latest_high_quality_age_days": np.where(lhq == 255, np.nan, lhq).astype("float32").ravel(),
        "mandatory_quality_flag": mqf.astype("uint8").ravel(), "cloud_mask": cloud.astype("uint16").ravel(),
        "snow_flag": snow.astype("uint8").ravel(),
        "lunar_irradiance": np.where(lunar == 65535, np.nan, lunar * 0.1).astype("float32").ravel(),
        "longitude": lon2.ravel(), "latitude": lat2.ravel()}
    record = dict(scene_id=scene, observation_date=date, observed_start_at=date,
                  observed_at=date + pd.Timedelta(days=1) - pd.Timedelta(seconds=1), produced_at=produced,
                  cells=int(valid.size), valid_cells=int(valid.sum()), gap_filled_cells=int(gap_ok.sum()))
    return layers, record


def daily_frame(layers: dict, record: dict) -> pd.DataFrame:
    """Layer satu produk → baris berskema ``viirs/daily.parquet`` (ditambah ``scene_id``)."""
    n = len(layers["ntl"])
    frame = pd.DataFrame({"viirs_cell_id": cell_ids(), "observation_date_utc": np.full(n, record["observation_date"]),
                          "longitude": layers["longitude"], "latitude": layers["latitude"]})
    frame["ntl"] = layers["ntl"]
    frame["ntl_available"] = np.isfinite(layers["ntl"])
    frame["ntl_gap_filled"] = layers["ntl_gap_filled"]
    frame["ntl_gap_filled_available"] = np.isfinite(layers["ntl_gap_filled"])
    for name in ("latest_high_quality_age_days", "mandatory_quality_flag", "cloud_mask", "snow_flag",
                 "lunar_irradiance"):
        frame[name] = layers[name]
    frame["observed_start_at"] = record["observed_start_at"]
    frame["observed_end_at"] = record["observed_at"]
    frame["produced_at"] = record["produced_at"]
    frame["scene_id"] = record["scene_id"]
    return frame


def fetch_product(client: HttpClient, granule: dict) -> tuple[dict, dict]:
    """Unduh satu granule (±20 MB) ke memori lalu baca subset Jakarta."""
    import h5py

    data = client.download(granule["href"], MAX_BYTES)
    try:
        with h5py.File(io.BytesIO(data), "r") as h5:
            layers, record = read_granule(h5, granule["id"])
    except OSError as exc:
        raise FormatError(f"viirs: {safe_url(granule['href'])} bukan HDF5 yang terbaca") from exc
    record.update(source_url=safe_url(granule["href"]), bytes_read=len(data),
                  catalog_updated_at=granule.get("updated"))
    return layers, record
