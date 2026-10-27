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
- Open-Meteo Historical Forecast: arsip ini menyambung jam-jam pertama setiap
  run model, sehingga nilai valid time t dapat berasal dari run yang terbit
  sesudah t. Waktu terbit tidak tercatat, jadi diasumsikan
  ``available_at = floor(t, weather_run_interval_hours) + weather_delay_hours``
  (run global ECMWF IFS/ICON/GFS tiap 6 jam; jeda terbit 8 jam, asumsi
  konservatif). Dipakai valid time terbaru dengan ``available_at <= τ``;
  umur = τ − valid time.
- GEOS-CF ``fcst`` (ds-v0.3.0): prakiraan untuk jam label [τ − 1 jam, τ) dari run
  terbaru yang lengkap terbit (``available_at_utc`` = Last-Modified lead terakhir) pada
  τ; ``lead_hours`` = awal jam − awal run.
- Kalender: jam dan hari dalam pekan (WIB) awal jam label sebagai sin/cos; selalu
  diketahui pada τ.
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
CALENDAR = ["hour_sin", "hour_cos", "weekday_sin", "weekday_cos"]
LOCAL_TZ = "Asia/Jakarta"

S5P_LEFT, S5P_TOP, S5P_RES = 675000.0, 9340000.0, 5000.0
VIIRS_CELL_DEG = 1 / 240          # 15 detik busur
VIIRS_RULES = ("tile", "nearest_listed")
GEOSCF_LAT_CENTERS = [-6.5, -6.25, -6.0]
GEOSCF_LON_CENTERS = [106.5, 106.75, 107.0]
WEATHER_RUN_INTERVAL_HOURS = 6
WEATHER_DELAY_HOURS = 8.0


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


def geoscf_lookup(root: Path | str = DATASET_PROCESSED) -> pd.Series:
    """``grid_id`` 100 m → ``geoscf_cell_id`` (lookup TI-AI-01; ``gy`` dihitung dari sisi selatan)."""
    lookup = pd.read_parquet(Path(root) / "geos-cf/grid100m_geoscf_lookup.parquet",
                             columns=["gx", "gy", "geoscf_cell_id"])
    lookup["grid_id"] = [f"r{444 - y:04d}_c{x:04d}" for x, y in zip(lookup.gx, lookup.gy)]
    return lookup.set_index("grid_id").geoscf_cell_id


def geoscf_cell_ids(lon, lat) -> np.ndarray:
    """Sel GEOS-CF 0,25° terdekat (``iy × 3 + ix``); sama dengan lookup TI-AI-01 pada seluruh sel aktif."""
    iy = np.abs(np.asarray(lat, dtype=float)[:, None] - np.asarray(GEOSCF_LAT_CENTERS)[None, :]).argmin(axis=1)
    ix = np.abs(np.asarray(lon, dtype=float)[:, None] - np.asarray(GEOSCF_LON_CENTERS)[None, :]).argmin(axis=1)
    return iy * len(GEOSCF_LON_CENTERS) + ix


class SourceReader:
    """Pembaca fitur grid-jam dari ``dataset_processed``.

    ``viirs_cell_rule``:
      - ``"tile"`` (bawaan): sel VIIRS yang memuat pusat sel 100 m menurut
        indeks tile. Kolom ``longitude``/``latitude`` di ``viirs/daily.parquet``
        adalah sudut barat laut sel VIIRS, bukan pusatnya.
      - ``"nearest_listed"``: sel dengan ``longitude``/``latitude`` terdekat,
        aturan bundle ST-GNN 2026-10-07. Bergeser setengah piksel (±230 m) ke
        tenggara; hanya untuk mereproduksi bundle tersebut.

    ``weather_delay_hours``: jeda terbit run Open-Meteo yang diasumsikan. ``None``
    memakai nilai valid time τ seolah tersedia pada τ (aturan bundle; berisiko
    memakai run yang terbit sesudah τ).
    """

    def __init__(self, root: Path | str = DATASET_PROCESSED, viirs_cell_rule: str = "tile",
                 weather_delay_hours: float | None = WEATHER_DELAY_HOURS,
                 weather_run_interval_hours: int = WEATHER_RUN_INTERVAL_HOURS):
        if viirs_cell_rule not in VIIRS_RULES:
            raise ValueError(f"viirs_cell_rule harus salah satu dari {VIIRS_RULES}")
        self.root = Path(root)
        self.viirs_cell_rule = viirs_cell_rule
        self.weather_delay_hours = weather_delay_hours
        self.weather_run_interval_hours = weather_run_interval_hours
        self._cells = active_cells().set_index("grid_id")
        self._cache_key: tuple[str, ...] | None = None
        self._cache: dict = {}
        self._source_cache: dict | None = None

    # Berkas masukan --------------------------------------------------------
    def input_files(self) -> list[Path]:
        """Berkas yang dibaca, untuk hash provenans."""
        r = self.root
        return [*sorted((r / "landsat/scenes").glob("*.parquet")), r / "landsat/manifest.json",
                r / "sentinel5p/observations.parquet", r / "sentinel5p/manifest.json",
                r / "viirs/daily.parquet", r / "viirs/manifest.json",
                r / "open_meteo/hourly.parquet", r / "open_meteo/manifest.json",
                r / "geos-cf/geoscf_cell_hourly.parquet", r / "geos-cf/grid100m_geoscf_lookup.parquet",
                r / "geos-cf/manifest.json", r / "geos-cf/geoscf_fcst_cell_hourly.parquet",
                r / "geos-cf/fcst_manifest.json"]

    def _load_sources(self) -> dict:
        """Snapshot sumber sekali per instance; tidak menyimpan keluaran semua tile.

        Worker membuat reader baru per run. Gunakan instance baru untuk membaca
        pembaruan berkas. ID kategorikal membatasi RAM Landsat/VIIRS; indeks
        terurut memungkinkan pemilihan sel tanpa memindai seluruh tabel tiap tile.
        Cache diterbitkan hanya setelah semua sumber berhasil dimuat.
        """
        if self._source_cache is not None:
            return self._source_cache
        viirs = self._load_viirs()
        listed = viirs.drop_duplicates("viirs_cell_id")[["viirs_cell_id", "longitude", "latitude"]]
        viirs = viirs.rename(columns={"viirs_cell_id": "source_cell_id", "observed_end_at": "observed_at"})
        if "scene_id" not in viirs:
            viirs["scene_id"] = viirs.observation_date_utc.map(self._viirs_scene_names())
        viirs = self._index_source(viirs.drop(columns=["longitude", "latitude", "observation_date_utc"]))

        s5p = self._load_sentinel5p()
        s5p = s5p.rename(columns={"grid_id": "source_cell_id", "available": "no2_mol_m2_available"})

        landsat = self._load_landsat(self._cells.index).rename(columns={"grid_id": "source_cell_id"})
        landsat = self._index_source(landsat)

        all_cells = self._cells.reset_index()
        lookup = pd.Series(self._geoscf_cell_ids(all_cells).to_numpy(), index=all_cells.grid_id)
        geos = self._load_geoscf_ana()
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

        fcst = self._load_geoscf_fcst()
        fcst["cell_id"] = fcst.cell_id.astype("int64")
        for col in ("init_utc", "time_window_start", "available_at_utc"):
            fcst[col] = fcst[col].astype("datetime64[ns, UTC]")
        fcst_events = {}
        for var in GEOSCF:
            e = fcst[fcst[f"available_{var}"]].sort_values(["available_at_utc", "init_utc"])
            fcst_events[var] = e[["cell_id", "time_window_start", "available_at_utc", "init_utc", "lead_hours",
                                  f"{var}_ugm3"]].reset_index(drop=True)

        weather = self._load_weather()
        for name in WEATHER:
            weather.loc[~weather[f"{name}_available"], name] = np.nan
        weather = weather[weather[WEATHER].notna().any(axis=1)][["time_utc", *WEATHER]]
        weather["time_utc"] = weather.time_utc.astype("datetime64[ns, UTC]")
        weather["available_at_utc"] = self.weather_available_at(weather.time_utc)
        weather = (weather.sort_values(["available_at_utc", "time_utc"])
                   .drop_duplicates("available_at_utc", keep="last").reset_index(drop=True))

        self._source_cache = dict(landsat=landsat, viirs=viirs, listed=listed, lookup=lookup,
                                 s5p=satellite_events(s5p, "no2_mol_m2"),
                                 weather=weather, geos=geos_events, fcst=fcst_events)
        return self._source_cache

    @staticmethod
    def _index_source(frame: pd.DataFrame) -> pd.DataFrame:
        if frame.source_cell_id.hasnans:
            frame = frame.dropna(subset=["source_cell_id"])
        frame["source_cell_id"] = frame.source_cell_id.astype("category").cat.remove_unused_categories()
        frame["scene_id"] = frame.scene_id.astype("category")
        frame.set_index("source_cell_id", inplace=True)
        return frame.sort_index(kind="stable")

    @staticmethod
    def _source_rows(frame: pd.DataFrame, ids) -> pd.DataFrame:
        selected = frame.index.categories.intersection(ids)
        codes = frame.index.categories.get_indexer(selected).astype(frame.index.codes.dtype, copy=False)
        # Locate contiguous cell ranges without pandas' non-unique .loc indexer,
        # which can scan the entire city-sized index for a multi-cell request.
        starts = np.searchsorted(frame.index.codes, codes, side="left")
        ends = np.searchsorted(frame.index.codes, codes, side="right")
        positions = (np.concatenate([np.arange(a, b) for a, b in zip(starts, ends)])
                     if len(codes) else np.empty(0, dtype=np.intp))
        rows = frame.iloc[positions].reset_index()
        # Preserve the public/event dtype contract, independent of cache encoding.
        rows["source_cell_id"] = rows.source_cell_id.astype(object)
        rows["scene_id"] = rows.scene_id.astype(object)
        return rows

    # Pemetaan sel dan event per sumber (sekali per himpunan sel) ------------
    def _prepare(self, grid_ids: tuple[str, ...]) -> dict:
        if grid_ids == self._cache_key:
            return self._cache
        unknown = set(grid_ids) - set(self._cells.index)
        if unknown:
            raise ValueError(f"{len(unknown)} grid_id di luar sel aktif, mis. {sorted(unknown)[:3]}")
        source = self._load_sources()
        cells = self._cells.loc[list(grid_ids)].reset_index()
        mapping = cells[["grid_id"]].copy()
        mapping["landsat_cell_id"] = cells.grid_id
        mapping["sentinel5p_cell_id"] = s5p_cell_ids(cells.easting, cells.northing)
        if self.viirs_cell_rule == "tile":
            mapping["viirs_cell_id"] = viirs_tile_cell_ids(cells.longitude, cells.latitude)
        else:
            listed = source["listed"]
            i = _nearest(cells[["longitude", "latitude"]].to_numpy(), listed[["longitude", "latitude"]].to_numpy())
            mapping["viirs_cell_id"] = listed.viirs_cell_id.to_numpy()[i]
        viirs = self._source_rows(source["viirs"], mapping.viirs_cell_id)
        if viirs.scene_id.isna().any():
            raise ValueError("Tanggal VIIRS tanpa nama berkas di manifest")
        landsat = self._source_rows(source["landsat"], grid_ids)
        mapping["geoscf_cell_id"] = mapping.grid_id.map(source["lookup"]).astype("int64")
        self._cache = dict(
            cells=cells, mapping=mapping, weather=source["weather"], geos=source["geos"], fcst=source["fcst"],
            events={**{f: ("landsat", satellite_events(landsat, f)) for f in LANDSAT_FEATURES},
                    "no2_mol_m2": ("sentinel5p", source["s5p"]),
                    "ntl": ("viirs", satellite_events(viirs, "ntl"))})
        self._cache_key = grid_ids
        return self._cache

    # Pemuat per sumber; ``feature_store.reader.StoreSourceReader`` menggantinya dengan PostgreSQL.
    def _load_viirs(self) -> pd.DataFrame:
        return pd.read_parquet(self.root / "viirs/daily.parquet", columns=[
            "viirs_cell_id", "observation_date_utc", "longitude", "latitude", "ntl", "ntl_available",
            "observed_end_at", "produced_at"])

    def _viirs_scene_names(self) -> dict:
        manifest = json.loads((self.root / "viirs/manifest.json").read_text(encoding="utf-8"))
        return {pd.Timestamp(x["observation_date"], tz="UTC"): x["scene_id"] for x in manifest["inputs"]}

    def _load_sentinel5p(self) -> pd.DataFrame:
        return pd.read_parquet(self.root / "sentinel5p/observations.parquet")

    def _load_landsat(self, grid_ids) -> pd.DataFrame:
        land_columns = ["grid_id", "scene_id", "observed_at", "produced_at", *LANDSAT_FEATURES,
                        *[f"{f}_available" for f in LANDSAT_FEATURES]]
        scenes = []
        for path in sorted((self.root / "landsat/scenes").glob("*.parquet")):
            scene = pd.read_parquet(path, columns=land_columns)
            scene["grid_id"] = pd.Categorical(scene.grid_id, categories=grid_ids)
            scene["scene_id"] = scene.scene_id.astype("category")
            scenes.append(scene)
        scene_ids = pd.Index([value for scene in scenes for value in scene.scene_id.cat.categories]).unique()
        for scene in scenes:
            scene["scene_id"] = scene.scene_id.cat.set_categories(scene_ids)
        return pd.concat(scenes, ignore_index=True)

    def _geoscf_cell_ids(self, cells: pd.DataFrame) -> pd.Series:
        return cells.grid_id.map(geoscf_lookup(self.root))

    def _load_geoscf_ana(self) -> pd.DataFrame:
        return pd.read_parquet(self.root / "geos-cf/geoscf_cell_hourly.parquet")

    def _load_geoscf_fcst(self) -> pd.DataFrame:
        return pd.read_parquet(self.root / "geos-cf/geoscf_fcst_cell_hourly.parquet")

    def _load_weather(self) -> pd.DataFrame:
        return pd.read_parquet(self.root / "open_meteo/hourly.parquet")

    def weather_available_at(self, valid_time: pd.Series) -> pd.Series:
        """Waktu tersedia yang diasumsikan untuk nilai Open-Meteo pada ``valid_time``."""
        if self.weather_delay_hours is None:
            return valid_time
        run = valid_time.dt.floor(f"{self.weather_run_interval_hours}h")
        return run + pd.Timedelta(hours=self.weather_delay_hours)

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

        taus = pd.DataFrame({"tau": times}).sort_values("tau")
        weather = pd.merge_asof(taus, state["weather"], left_on="tau", right_on="available_at_utc",
                                direction="backward").set_index("tau").reindex(batch.time_utc)
        for name in WEATHER:
            batch[name] = weather[name].to_numpy()
        # ``.array`` mempertahankan zona waktu, juga bila seluruhnya NaT (tanpa data cuaca).
        batch["weather_time_utc"] = weather.time_utc.astype("datetime64[ns, UTC]").array
        batch["weather_available_at_utc"] = weather.available_at_utc.astype("datetime64[ns, UTC]").array
        batch["weather_age_hours"] = (batch.time_utc - batch.weather_time_utc).dt.total_seconds() / 3600

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

        # Prakiraan untuk jam label itu sendiri, dari run terbaru yang sudah terbit pada τ.
        left = batch[["time_utc", "geoscf_cell_id"]].reset_index().assign(
            window_start=lambda d: d.time_utc - pd.Timedelta(hours=1)).sort_values("time_utc")
        for var in GEOSCF:
            joined = pd.merge_asof(left, state["fcst"][var], left_on="time_utc", right_on="available_at_utc",
                                   left_by=["geoscf_cell_id", "window_start"],
                                   right_by=["cell_id", "time_window_start"], direction="backward")
            joined = joined.set_index("index").reindex(batch.index)
            batch[f"geoscf_fcst_{var}_ugm3"] = joined[f"{var}_ugm3"]
            batch[f"geoscf_fcst_{var}_init_utc"] = joined.init_utc
            batch[f"geoscf_fcst_{var}_available_at_utc"] = joined.available_at_utc
            batch[f"geoscf_fcst_{var}_lead_hours"] = joined.lead_hours.astype("float64")

        local = (batch.time_utc - pd.Timedelta(hours=1)).dt.tz_convert(LOCAL_TZ)
        hour = 2 * np.pi * local.dt.hour.to_numpy() / 24
        weekday = 2 * np.pi * local.dt.weekday.to_numpy() / 7
        batch["hour_sin"], batch["hour_cos"] = np.sin(hour), np.cos(hour)
        batch["weekday_sin"], batch["weekday_cos"] = np.sin(weekday), np.cos(weekday)
        return batch
