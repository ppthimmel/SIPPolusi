"""Pembaca langsung keluaran preprocessing TI-AI-01 per sumber.

``SourceReader`` adalah ``reader`` yang dipakai ``export_stgnn_inputs``: untuk
sekumpulan sel dan interval jam [start, end), ia mengembalikan satu baris per
(sel, jam) dengan nilai sumber dan jam provenansnya. Tidak ada preprocessing
ulang, statistik, atau imputasi.

Aturan per sumber (``time_utc`` = waktu inferensi τ, akhir jam label):

- Landsat (``ndvi``, ``ndbi``, ``lst_c``), Sentinel-5P (``no2_mol_m2``) dan
  VIIRS (``ntl``): pengamatan valid terakhir dengan ``produced_at <= τ``
  (``satellite_events`` / ``join_satellite_events``); umur = τ − ``observed_at``.
- GEOS-CF ``ana``: valid time terbaru dengan ``available_at_utc <= τ``; umur =
  τ − akhir jam valid time.
- Open-Meteo: nilai pada jam τ; diasumsikan tersedia pada τ.
"""

from __future__ import annotations

import json
from pathlib import Path

import numpy as np
import pandas as pd

from .grid import active_cells
from .prepare_stgnn import join_satellite_events, satellite_events

DATASET_PROCESSED = Path(__file__).resolve().parents[1] / "dataset_processed"

LANDSAT_FEATURES = ["ndvi", "ndbi", "lst_c"]
WEATHER = ["temperature_2m", "relative_humidity_2m", "wind_speed_10m", "wind_direction_10m",
           "boundary_layer_height", "precipitation", "surface_pressure"]
GEOSCF = ["pm25", "no2"]

S5P_LEFT, S5P_TOP, S5P_RES = 675000.0, 9340000.0, 5000.0
VIIRS_CELL_DEG = 1 / 240          # 15 detik busur
VIIRS_RULES = ("tile", "nearest_listed")


def s5p_cell_ids(easting, northing) -> list[str]:
    """Sel 5 km Sentinel-5P yang memuat pusat sel 100 m."""
    cols = np.floor((np.asarray(easting) - S5P_LEFT) / S5P_RES).astype(int)
    rows = np.floor((S5P_TOP - np.asarray(northing)) / S5P_RES).astype(int)
    return [f"r{r:04d}_c{c:04d}" for r, c in zip(rows, cols)]


def viirs_tile_cell_ids(lon, lat) -> list[str]:
    """Sel VIIRS (indeks tile sinusoidal 10°, 2400 × 2400) yang memuat titik lon/lat."""
    lon = np.asarray(lon, dtype=float)
    lat = np.asarray(lat, dtype=float)
    h = np.floor((lon + 180) / 10).astype(int)
    v = np.floor((90 - lat) / 10).astype(int)
    rows = np.floor((90 - 10 * v - lat) / VIIRS_CELL_DEG).astype(int)
    cols = np.floor((lon - (-180 + 10 * h)) / VIIRS_CELL_DEG).astype(int)
    return [f"h{a:02d}v{b:02d}_r{r:04d}_c{c:04d}" for a, b, r, c in zip(h, v, rows, cols)]


def _nearest(points, targets, chunk: int = 4_000) -> np.ndarray:
    out = np.empty(len(points), dtype=int)
    for i in range(0, len(points), chunk):
        d = ((points[i:i + chunk, None, :] - targets[None, :, :]) ** 2).sum(axis=2)
        out[i:i + chunk] = d.argmin(axis=1)
    return out


class SourceReader:
    """Pembaca fitur grid-jam dari ``dataset_processed``.

    ``viirs_cell_rule``:
      - ``"tile"`` (bawaan): sel VIIRS yang memuat pusat sel 100 m menurut
        indeks tile. Kolom ``longitude``/``latitude`` di ``viirs/daily.parquet``
        adalah sudut barat laut sel VIIRS, bukan pusatnya.
      - ``"nearest_listed"``: sel dengan ``longitude``/``latitude`` terdekat,
        aturan bundle ST-GNN 2026-10-07. Bergeser setengah piksel (±230 m) ke
        tenggara; hanya untuk mereproduksi bundle tersebut.
    """

    def __init__(self, root: Path | str = DATASET_PROCESSED, viirs_cell_rule: str = "tile"):
        if viirs_cell_rule not in VIIRS_RULES:
            raise ValueError(f"viirs_cell_rule harus salah satu dari {VIIRS_RULES}")
        self.root = Path(root)
        self.viirs_cell_rule = viirs_cell_rule
        self._cells = active_cells().set_index("grid_id")
        self._cache_key: tuple[str, ...] | None = None
        self._cache: dict = {}

    # Berkas masukan --------------------------------------------------------
    def input_files(self) -> list[Path]:
        """Berkas yang dibaca, untuk hash provenans."""
        r = self.root
        return [*sorted((r / "landsat/scenes").glob("*.parquet")), r / "landsat/manifest.json",
                r / "sentinel5p/observations.parquet", r / "sentinel5p/manifest.json",
                r / "viirs/daily.parquet", r / "viirs/manifest.json",
                r / "open_meteo/hourly.parquet", r / "open_meteo/manifest.json",
                r / "geos-cf/geoscf_cell_hourly.parquet", r / "geos-cf/grid100m_geoscf_lookup.parquet",
                r / "geos-cf/manifest.json"]

    # Pemetaan sel dan event per sumber (sekali per himpunan sel) ------------
    def _prepare(self, grid_ids: tuple[str, ...]) -> dict:
        if grid_ids == self._cache_key:
            return self._cache
        unknown = set(grid_ids) - set(self._cells.index)
        if unknown:
            raise ValueError(f"{len(unknown)} grid_id di luar sel aktif, mis. {sorted(unknown)[:3]}")
        cells = self._cells.loc[list(grid_ids)].reset_index()
        mapping = cells[["grid_id"]].copy()
        mapping["landsat_cell_id"] = cells.grid_id
        mapping["sentinel5p_cell_id"] = s5p_cell_ids(cells.easting, cells.northing)

        viirs = pd.read_parquet(self.root / "viirs/daily.parquet", columns=[
            "viirs_cell_id", "observation_date_utc", "longitude", "latitude", "ntl", "ntl_available",
            "observed_end_at", "produced_at"])
        if self.viirs_cell_rule == "tile":
            mapping["viirs_cell_id"] = viirs_tile_cell_ids(cells.longitude, cells.latitude)
        else:
            listed = viirs.drop_duplicates("viirs_cell_id")
            i = _nearest(cells[["longitude", "latitude"]].to_numpy(), listed[["longitude", "latitude"]].to_numpy())
            mapping["viirs_cell_id"] = listed.viirs_cell_id.to_numpy()[i]
        manifest = json.loads((self.root / "viirs/manifest.json").read_text(encoding="utf-8"))
        names = {pd.Timestamp(x["observation_date"], tz="UTC"): x["scene_id"] for x in manifest["inputs"]}
        viirs = viirs[viirs.viirs_cell_id.isin(set(mapping.viirs_cell_id))]
        viirs = viirs.rename(columns={"viirs_cell_id": "source_cell_id", "observed_end_at": "observed_at"})
        viirs["scene_id"] = viirs.observation_date_utc.map(names)
        if viirs.scene_id.isna().any():
            raise ValueError("Tanggal VIIRS tanpa nama berkas di manifest")

        s5p = pd.read_parquet(self.root / "sentinel5p/observations.parquet")
        s5p = s5p.rename(columns={"grid_id": "source_cell_id", "available": "no2_mol_m2_available"})

        landsat = pd.concat([pd.read_parquet(p, filters=[("grid_id", "in", list(grid_ids))])
                             for p in sorted((self.root / "landsat/scenes").glob("*.parquet"))], ignore_index=True)
        landsat = landsat.rename(columns={"grid_id": "source_cell_id"})

        lookup = pd.read_parquet(self.root / "geos-cf/grid100m_geoscf_lookup.parquet",
                                 columns=["gx", "gy", "geoscf_cell_id"])
        lookup["grid_id"] = [f"r{444 - y:04d}_c{x:04d}" for x, y in zip(lookup.gx, lookup.gy)]
        mapping["geoscf_cell_id"] = mapping.grid_id.map(lookup.set_index("grid_id").geoscf_cell_id).astype("int64")
        geos = pd.read_parquet(self.root / "geos-cf/geoscf_cell_hourly.parquet")
        geos["cell_id"] = geos.cell_id.astype("int64")
        for col in ("time_window_start", "available_at_utc"):
            geos[col] = geos[col].astype("datetime64[ns, UTC]")
        geos["time_window_end"] = geos.time_window_start + pd.Timedelta(hours=1)
        geos["latency_hours"] = geos.latency_hours.astype("float64")
        geos_events = {}
        for var in GEOSCF:
            e = geos[geos[f"available_{var}"]].sort_values(["available_at_utc", "time_window_start"])
            # Valid time tidak pernah mundur saat berkas lama terbit belakangan.
            e = e[e.time_window_start.eq(e.groupby("cell_id").time_window_start.cummax())]
            e = e.drop_duplicates(["cell_id", "available_at_utc"], keep="last")
            geos_events[var] = e[["cell_id", "available_at_utc", "time_window_start", "time_window_end",
                                  f"{var}_ugm3", "latency_hours"]].reset_index(drop=True)

        weather = pd.read_parquet(self.root / "open_meteo/hourly.parquet").set_index("time_utc")
        for name in WEATHER:
            weather.loc[~weather[f"{name}_available"], name] = np.nan

        self._cache_key = grid_ids
        self._cache = dict(
            cells=cells, mapping=mapping, weather=weather, geos=geos_events,
            events={**{f: ("landsat", satellite_events(landsat, f)) for f in LANDSAT_FEATURES},
                    "no2_mol_m2": ("sentinel5p", satellite_events(s5p, "no2_mol_m2")),
                    "ntl": ("viirs", satellite_events(viirs, "ntl"))})
        return self._cache

    def cell_mapping(self, grid_ids) -> pd.DataFrame:
        """Sel sumber per sel 100 m (Landsat, Sentinel-5P, VIIRS, GEOS-CF)."""
        return self._prepare(tuple(grid_ids))["mapping"].copy()

    # Reader ---------------------------------------------------------------
    def __call__(self, grid_ids, start, end) -> pd.DataFrame:
        state = self._prepare(tuple(grid_ids))
        times = pd.date_range(start, end, freq="h", inclusive="left").astype("datetime64[ns, UTC]")
        cells = state["cells"]
        batch = pd.DataFrame({
            "time_utc": np.repeat(times, len(cells)),
            "grid_id": np.tile(cells.grid_id.to_numpy(), len(times))})
        batch = batch.merge(cells, on="grid_id", how="left", validate="many_to_one")
        mapping = state["mapping"]
        for feature in [*LANDSAT_FEATURES, "no2_mol_m2", "ntl"]:
            source, events = state["events"][feature]
            batch = join_satellite_events(batch, mapping, events, feature, source)

        weather = state["weather"].reindex(batch.time_utc)
        for name in WEATHER:
            batch[name] = weather[name].to_numpy()
        batch["weather_time_utc"] = batch.time_utc.where(weather[WEATHER].notna().any(axis=1).to_numpy())

        batch["geoscf_cell_id"] = batch.grid_id.map(mapping.set_index("grid_id").geoscf_cell_id)
        left = batch[["time_utc", "geoscf_cell_id"]].reset_index().sort_values("time_utc")
        for var in GEOSCF:
            joined = pd.merge_asof(left, state["geos"][var], left_on="time_utc", right_on="available_at_utc",
                                   left_by="geoscf_cell_id", right_by="cell_id", direction="backward")
            joined = joined.set_index("index").reindex(batch.index)
            batch[f"geoscf_{var}_ugm3"] = joined[f"{var}_ugm3"]
            for col in ("time_window_start", "time_window_end"):
                batch[f"geoscf_{var}_{col}"] = joined[col]
            batch[f"geoscf_{var}_available_at_utc"] = joined.available_at_utc
            batch[f"geoscf_{var}_latency_hours"] = joined.latency_hours
            batch[f"geoscf_{var}_age_hours"] = (
                (batch.time_utc - joined.time_window_end).dt.total_seconds() / 3600)
        return batch
