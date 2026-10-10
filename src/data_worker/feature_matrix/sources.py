"""Pembaca sumber dan penyelarasan ke grid kanonik (TI-AI-02).

Semua sumber dibaca dari keluaran preprocessing TI-AI-01 di
``src/data_worker/dataset_processed`` (tidak ada preprocessing ulang):

* fitur dinamis (Xmacro, Xmet) memakai aturan *as-of*: untuk time window t,
  hanya data dengan waktu tersedia <= waktu inferensi (t + 1 jam) yang boleh
  dipakai, dan umurnya dicatat;
* fitur statis (Xroad, Xland, Xactivity) dihitung dengan batas waktu
  ``cutoff`` sehingga data sesudah batas (mis. akhir periode pelatihan) tidak
  bocor ke fitur.
"""

from __future__ import annotations

import glob
import hashlib
import json
import pathlib
import warnings

import numpy as np
import pandas as pd

from feature_matrix.spec import CANONICAL_GRID
from spatial_model.grid import to_grid_xy, to_lonlat

DEFAULT_ROOT = pathlib.Path(__file__).resolve().parent.parent / "dataset_processed"
MAX_AGE_H = 72.0
STATIC_LOOKBACK_DAYS = 90
XROAD_MAX_NODE_DISTANCE_M = 500.0
CELL_AREA_KM2 = (CANONICAL_GRID.cell_m / 1000.0) ** 2

ROAD_CLASSES = {
    "major": {"motorway", "motorway_link", "trunk", "trunk_link", "primary", "primary_link", "busway"},
    "secondary": {"secondary", "secondary_link", "tertiary", "tertiary_link"},
    "local": {"residential", "living_street", "unclassified", "service"},
    "active": {"footway", "path", "pedestrian", "cycleway", "steps", "corridor", "track"},
}
ARTERIAL = {"trunk", "trunk_link", "primary", "primary_link", "secondary", "secondary_link"}
MOTORWAY = {"motorway", "motorway_link"}


def sha256_file(path: pathlib.Path) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as fh:
        for block in iter(lambda: fh.read(1 << 20), b""):
            h.update(block)
    return h.hexdigest()


def ns(values) -> np.ndarray:
    """Waktu UTC → bilangan bulat nanodetik (tanpa kehilangan zona)."""
    return pd.DatetimeIndex(pd.to_datetime(values, utc=True)).as_unit("ns").asi8


def as_utc(values) -> pd.DatetimeIndex:
    idx = pd.DatetimeIndex(pd.to_datetime(values, utc=True))
    return idx.as_unit("ns")


class Grid:
    """Pusat sel grid kanonik (EPSG:32748 dan lon/lat), dihitung sekali."""

    def __init__(self, spec=CANONICAL_GRID):
        self.spec = spec
        self.x, self.y = spec.cell_centers()
        self.lon, self.lat = to_lonlat(self.x, self.y)
        self.grid_id = np.arange(spec.n_cells, dtype=np.int64)


class Sources:
    """Akses malas (lazy) ke keluaran dataset_processed beserta jejak asal berkasnya."""

    def __init__(self, root: pathlib.Path = DEFAULT_ROOT):
        self.root = pathlib.Path(root)
        self.grid = Grid()
        self._cache: dict = {}
        self.inputs: dict[str, str] = {}      # berkas → sha256, untuk manifest

    def _path(self, rel: str) -> pathlib.Path:
        path = self.root / rel
        if rel not in self.inputs:
            self.inputs[rel] = sha256_file(path)
        return path

    # ------------------------------------------------------------------ GEOS-CF (Xmacro)
    def geoscf(self) -> dict:
        if "geoscf" not in self._cache:
            cells = pd.read_parquet(self._path("geos-cf/geoscf_cell_hourly.parquet"))
            lookup = pd.read_parquet(self._path("geos-cf/grid100m_geoscf_lookup.parquet"),
                                     columns=["grid_id", "geoscf_cell_id"])
            if not (lookup["grid_id"].to_numpy() == self.grid.grid_id).all():
                raise ValueError("grid100m_geoscf_lookup tidak sejajar dengan grid kanonik")
            cells["time_window_start"] = as_utc(cells["time_window_start"])
            cells["available_at_utc"] = as_utc(cells["available_at_utc"])
            # Waktu tersedia per valid time (satu berkas per jam untuk seluruh sel).
            avail = cells.groupby("time_window_start")["available_at_utc"].max().sort_values()
            valid = avail.index.to_numpy()
            self._cache["geoscf"] = {
                "values": cells.set_index(["time_window_start", "cell_id"])[
                    ["pm25_ugm3", "no2_ugm3", "available_pm25", "available_no2"]],
                "avail_at": ns(avail.to_numpy()),
                "available_at_of_valid": avail,
                "latest_valid": np.maximum.accumulate(ns(valid)),
                "cell_of_grid": lookup["geoscf_cell_id"].to_numpy(),
            }
        return self._cache["geoscf"]

    # ------------------------------------------------------------------ Sentinel-5P (Xmacro)
    def s5p(self) -> dict:
        if "s5p" not in self._cache:
            manifest = json.loads(self._path("sentinel5p/manifest.json").read_text(encoding="utf-8"))
            g = manifest["grid"]
            obs = pd.read_parquet(self._path("sentinel5p/observations.parquet"))
            obs = obs[obs["available"]].copy()
            for col in ("observed_at", "produced_at"):
                obs[col] = as_utc(obs[col])
            rc = obs["grid_id"].str.extract(r"r(\d+)_c(\d+)").astype(int)
            obs["s5p_cell"] = rc[0] * g["width"] + rc[1]
            per_cell = {}
            for cell, grp in obs.groupby("s5p_cell"):
                grp = grp.sort_values(["produced_at", "observed_at"])
                obs_ns = ns(grp["observed_at"])
                # indeks pengamatan terbaru (observed_at maksimum) di antara yang sudah diproduksi
                best = np.zeros(len(grp), dtype=np.int64)
                for i in range(1, len(grp)):
                    best[i] = i if obs_ns[i] >= obs_ns[best[i - 1]] else best[i - 1]
                per_cell[cell] = {
                    "produced": ns(grp["produced_at"]),
                    "best": best,
                    "observed": obs_ns,
                    "no2": grp["no2_mol_m2"].to_numpy() * 1e6,
                    "precision": grp["no2_precision_mol_m2"].to_numpy() * 1e6,
                }
            row = np.floor((g["top"] - self.grid.y) / g["resolution_m"]).astype(int)
            col = np.floor((self.grid.x - g["left"]) / g["resolution_m"]).astype(int)
            inside = (row >= 0) & (row < g["height"]) & (col >= 0) & (col < g["width"])
            self._cache["s5p"] = {"per_cell": per_cell,
                                  "cell_of_grid": np.where(inside, row * g["width"] + col, -1)}
        return self._cache["s5p"]

    # ------------------------------------------------------------------ Open-Meteo (Xmet)
    def open_meteo(self) -> pd.DataFrame:
        if "met" not in self._cache:
            m = pd.read_parquet(self._path("open_meteo/hourly.parquet"))
            m["time_utc"] = as_utc(m["time_utc"])
            speed = m["wind_speed_10m"] / 3.6
            direction = np.deg2rad(m["wind_direction_10m"])          # arah datang angin (meteorologis)
            flags = [c for c in m.columns if c.endswith("_available")]
            met = pd.DataFrame({
                "met_temperature_2m_c": m["temperature_2m"],
                "met_relative_humidity_2m_pct": m["relative_humidity_2m"],
                "met_wind_speed_10m_ms": speed,
                "met_wind_u_10m_ms": -speed * np.sin(direction),
                "met_wind_v_10m_ms": -speed * np.cos(direction),
                "met_boundary_layer_height_m": m["boundary_layer_height"],
                "met_precipitation_mm": m["precipitation"],
                "met_surface_pressure_hpa": m["surface_pressure"],
                "met_available": m[flags].all(axis=1),
            })
            met = met.astype({c: "float32" for c in met.columns if c != "met_available"})
            met.index = m["time_utc"]
            if met.index.duplicated().any():
                raise ValueError("Open-Meteo memuat jam ganda")
            self._cache["met"] = met
        return self._cache["met"]

    # ------------------------------------------------------------------ OSM (Xroad, statis)
    def xroad(self) -> pd.DataFrame:
        if "xroad" not in self._cache:
            import shapely

            from spatial_model.aggregate import edge_cell_pieces

            edges = pd.read_csv(self._path("OSM/road_edge.csv.gz"),
                                usecols=["edge_id", "graph_version", "u", "v", "highway", "walk_allowed", "geom_wkt"],
                                true_values=["t"], false_values=["f"])
            versions = pd.read_csv(self._path("OSM/graph_version.csv"))
            active = versions.loc[versions["status"] == "active", "version"]
            if len(active) != 1:
                raise ValueError("graph_version.csv wajib memiliki tepat satu versi active")
            edges = edges[edges["graph_version"] == active.iloc[0]].reset_index(drop=True)
            geoms = shapely.from_wkt(edges["geom_wkt"].to_numpy())
            coords, index = shapely.get_coordinates(geoms, return_index=True)
            pieces, _ = edge_cell_pieces(edges["edge_id"].to_numpy(), coords, index, CANONICAL_GRID)
            pieces = pieces.merge(edges[["edge_id", "highway", "walk_allowed"]], on="edge_id", how="left")
            n = CANONICAL_GRID.n_cells
            cell = pieces["cell_id"].to_numpy()
            length = pieces["length_m"].to_numpy()
            out = pd.DataFrame(index=pd.RangeIndex(n, name="grid_id"))

            def density(mask):
                return (np.bincount(cell[mask], weights=length[mask], minlength=n) / 1000.0 / CELL_AREA_KM2)

            out["road_density_km_km2"] = density(np.ones(len(pieces), bool))
            for name, classes in ROAD_CLASSES.items():
                out[f"road_{name}_density_km_km2"] = density(pieces["highway"].isin(classes).to_numpy())
            out["road_walk_density_km_km2"] = density(pieces["walk_allowed"].fillna(False).to_numpy(bool))

            # Persimpangan: simpul dengan >= 3 tetangga unik (tanpa loop).
            pairs = edges.loc[edges["u"] != edges["v"], ["u", "v"]]
            nb = pd.concat([pairs.rename(columns={"u": "a", "v": "b"}), pairs.rename(columns={"v": "a", "u": "b"})])
            degree = nb.drop_duplicates().groupby("a").size()
            nodes = pd.read_csv(self._path("OSM/road_node.csv.gz"), usecols=["node_id", "graph_version", "lon", "lat"])
            nodes = nodes[nodes["graph_version"] == active.iloc[0]]
            nx_, ny_ = to_grid_xy(nodes["lon"].to_numpy(), nodes["lat"].to_numpy())
            ncell = CANONICAL_GRID.cell_index(nx_, ny_)
            is_x = nodes["node_id"].map(degree).fillna(0).to_numpy() >= 3
            ok = is_x & (ncell >= 0)
            out["intersection_density_per_km2"] = np.bincount(ncell[ok], minlength=n) / CELL_AREA_KM2

            # Jarak ke arteri dan tol terdekat, pada proyeksi meter.
            gx, gy = to_grid_xy(coords[:, 0], coords[:, 1])
            lines_utm = shapely.linestrings(np.column_stack([gx, gy]), indices=index)
            centers = shapely.points(self.grid.x, self.grid.y)
            for col, classes in (("dist_arterial_m", ARTERIAL), ("dist_motorway_m", MOTORWAY)):
                tree = shapely.STRtree(lines_utm[edges["highway"].isin(classes).to_numpy()])
                (_, _), dist = tree.query_nearest(centers, return_distance=True, all_matches=False)
                out[col] = dist
            node_tree = shapely.STRtree(shapely.points(nx_, ny_))
            (hit, _), _ = node_tree.query_nearest(centers, max_distance=XROAD_MAX_NODE_DISTANCE_M,
                                                  return_distance=True, all_matches=False)
            available = np.zeros(n, bool)
            available[hit] = True
            out["xroad_available"] = available
            cols = [c for c in out.columns if c != "xroad_available"]
            out.loc[~available, cols] = np.nan
            self._cache["xroad"] = out.astype({c: "float32" for c in cols})
        return self._cache["xroad"]

    # ------------------------------------------------------------------ Landsat (Xland, statis)
    def xland(self, cutoff: pd.Timestamp) -> pd.DataFrame:
        key = ("xland", cutoff)
        if key not in self._cache:
            times = pd.read_parquet(self._path("landsat/scene_times.parquet"))
            for col in ("observed_at", "produced_at"):
                times[col] = as_utc(times[col])
            start = cutoff - pd.Timedelta(days=STATIC_LOOKBACK_DAYS)
            chosen = times[(times["observed_at"] >= start) & (times["observed_at"] < cutoff)
                           & (times["produced_at"] <= cutoff)].sort_values("scene_id")
            n = CANONICAL_GRID.n_cells
            stacks = {v: [] for v in ("ndvi", "ndbi", "lst_c")}
            for scene in chosen["scene_id"]:
                arrays = self._landsat_scene(scene)
                for v in stacks:
                    stacks[v].append(arrays[v])
            out = pd.DataFrame(index=pd.RangeIndex(n, name="grid_id"))
            for v, name in (("ndvi", "ndvi"), ("ndbi", "ndbi"), ("lst_c", "lst")):
                if stacks[v]:
                    m = np.vstack(stacks[v])
                    count = np.isfinite(m).sum(axis=0)
                    with warnings.catch_warnings():
                        warnings.simplefilter("ignore", RuntimeWarning)      # sel tanpa scene valid → NaN
                        med = np.nanmedian(m, axis=0)
                else:
                    count, med = np.zeros(n, int), np.full(n, np.nan)
                out[f"land_{v}"] = med.astype(np.float32)
                out[f"land_{name}_n_obs"] = count.astype(np.int16)
            out["land_available"] = (out[["land_ndvi_n_obs", "land_ndbi_n_obs", "land_lst_n_obs"]] > 0).any(axis=1)
            self._cache[key] = (out, chosen["scene_id"].tolist())
        return self._cache[key][0]

    def _landsat_scene(self, scene: str) -> dict[str, np.ndarray]:
        key = ("landsat_scene", scene)
        if key not in self._cache:
            d = pd.read_parquet(self._path(f"landsat/scenes/{scene}.parquet"),
                                columns=["easting", "northing", "ndvi", "ndvi_available", "ndbi",
                                         "ndbi_available", "lst_c", "lst_c_available"])
            idx = CANONICAL_GRID.cell_index(d["easting"].to_numpy(), d["northing"].to_numpy())
            if (idx < 0).any():
                raise ValueError(f"{scene}: sel di luar grid kanonik")
            arrays = {}
            for v in ("ndvi", "ndbi", "lst_c"):
                arr = np.full(CANONICAL_GRID.n_cells, np.nan, dtype=np.float32)
                arr[idx] = np.where(d[f"{v}_available"].to_numpy(), d[v].to_numpy(dtype=np.float32), np.nan)
                arrays[v] = arr
            self._cache[key] = arrays
        return self._cache[key]

    def xland_scenes(self, cutoff: pd.Timestamp) -> list[str]:
        self.xland(cutoff)
        return self._cache[("xland", cutoff)][1]

    # ------------------------------------------------------------------ VIIRS (Xactivity, statis)
    def xactivity(self, cutoff: pd.Timestamp) -> pd.DataFrame:
        key = ("xact", cutoff)
        if key not in self._cache:
            d = self._viirs_daily()
            start = cutoff - pd.Timedelta(days=STATIC_LOOKBACK_DAYS)
            # tanggal produk harian dianggap selesai pada akhir hari tersebut
            ok = (d["ntl_available"] & (d["observation_date_utc"] >= start)
                  & (d["observation_date_utc"] + pd.Timedelta(days=1) <= cutoff) & (d["produced_at"] <= cutoff))
            stats = d[ok].groupby("viirs_cell_id")["ntl"].agg(["median", "size"])
            rc = stats.index.to_series().str.extract(r"h(\d+)v(\d+)_r(\d+)_c(\d+)").astype(int)
            # Indeks tile VNP46A2: kolom c meliputi lon [-180 + 10h + c/240, ...), baris r meliputi
            # lat (90 - 10v - (r+1)/240, 90 - 10v - r/240]. Kolom longitude/latitude sumber adalah
            # sudut barat laut sel, bukan pusat.
            h, v = int(rc[0].iloc[0]) if len(rc) else 28, int(rc[1].iloc[0]) if len(rc) else 9
            gc = np.floor((self.grid.lon + 180 - 10 * h) * 240).astype(np.int64)
            gr = np.floor((90 - 10 * v - self.grid.lat) * 240).astype(np.int64)
            key_of_grid = pd.Series([f"h{h:02d}v{v:02d}_r{r}_c{c}" for r, c in zip(gr, gc)])
            n_obs = key_of_grid.map(stats["size"]).fillna(0).to_numpy()
            out = pd.DataFrame({
                "act_ntl_nw_cm2_sr": key_of_grid.map(stats["median"]).to_numpy(dtype=np.float32),
                "act_ntl_n_obs": n_obs.astype(np.int16),
                "act_available": n_obs > 0,
            }, index=pd.RangeIndex(CANONICAL_GRID.n_cells, name="grid_id"))
            self._cache[key] = out
        return self._cache[key]

    def _viirs_daily(self) -> pd.DataFrame:
        if "viirs_daily" not in self._cache:
            d = pd.read_parquet(self._path("viirs/daily.parquet"),
                                columns=["viirs_cell_id", "observation_date_utc", "ntl", "ntl_available", "produced_at"])
            d["observation_date_utc"] = as_utc(d["observation_date_utc"])
            d["produced_at"] = as_utc(d["produced_at"])
            self._cache["viirs_daily"] = d
        return self._cache["viirs_daily"]

    # ------------------------------------------------------------------ stasiun SPKU
    def stations(self) -> pd.DataFrame:
        """Stasiun SPKU beserta grid_id kanonik (keluaran TI-AI-01, geos-cf/spku_stations_geoscf_mapping.csv)."""
        if "stations" not in self._cache:
            s = pd.read_csv(self._path("geos-cf/spku_stations_geoscf_mapping.csv"))
            x, y = to_grid_xy(s["lon"].to_numpy(), s["lat"].to_numpy())
            s["x_utm"], s["y_utm"] = x, y
            s["grid_id"] = CANONICAL_GRID.cell_index(x, y)
            self._cache["stations"] = s
        return self._cache["stations"]


def nearest_sensor_distance(cell_x, cell_y, sensor_x, sensor_y, chunk: int = 20_000) -> np.ndarray:
    """Jarak Euclides (m) pusat sel ke sensor terdekat dari himpunan yang diberikan."""
    cx, cy = np.asarray(cell_x, float), np.asarray(cell_y, float)
    sx, sy = np.asarray(sensor_x, float), np.asarray(sensor_y, float)
    out = np.full(len(cx), np.nan)
    if len(sx) == 0:
        return out
    for lo in range(0, len(cx), chunk):
        hi = lo + chunk
        d2 = (cx[lo:hi, None] - sx[None, :]) ** 2 + (cy[lo:hi, None] - sy[None, :]) ** 2
        out[lo:hi] = np.sqrt(d2.min(axis=1))
    return out
