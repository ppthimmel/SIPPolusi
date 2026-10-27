"""``StoreSourceReader``: ``SourceReader`` FeatureMatrix yang membaca skema ``features`` PostgreSQL.

Aturan ketersediaan per sumber (as-of, tanpa kebocoran waktu) sama persis dengan
pembaca ``dataset_processed`` data latih, karena seluruh penggabungan tetap
dikerjakan ``SourceReader``; kelas ini hanya mengganti pemuat per sumber.

- ``as_of``: hanya data dengan ``fetched_utc`` ≤ ``as_of`` yang dibaca, sehingga
  FeatureMatrix satu time window dapat dibangun ulang persis (``feature_version``).
- Riwayat Landsat direduksi sebelum digabung: untuk setiap sel dan fitur, hanya
  pengamatan valid terakhir yang terbit ≤ awal interval, ditambah pengamatan yang
  terbit di dalam interval. Hasil gabungannya sama dengan memuat seluruh scene,
  tetapi memorinya tetap kecil (±196 ribu sel × satu scene per fitur).
"""

from __future__ import annotations

import datetime as dt

import numpy as np
import pandas as pd

from connectors import viirs as viirs_connector
from feature_matrix.grid import active_cells
from feature_matrix.sources import LANDSAT_FEATURES, SourceReader, geoscf_cell_ids

from .store import FeatureStore

#: Jangkauan riwayat yang dimuat sebelum awal interval, per sumber.
LOOKBACK = {"weather": pd.Timedelta(days=3), "geoscf_ana": pd.Timedelta(days=7),
            "geoscf_fcst": pd.Timedelta(hours=1)}


class StoreSourceReader(SourceReader):
    def __init__(self, store: FeatureStore, as_of: dt.datetime | None = None, **kwargs):
        super().__init__(**kwargs)
        self.store = store
        self.as_of = as_of
        self._window: tuple[pd.Timestamp, pd.Timestamp] | None = None
        self._active = active_cells()
        self._positions = pd.Series(np.arange(len(self._active)), index=self._active.grid_id)

    def __call__(self, grid_ids, start, end) -> pd.DataFrame:
        window = (pd.Timestamp(start), pd.Timestamp(end))
        if window != self._window:
            self._window, self._cache_key = window, None
            # Source loaders bound their history to this window; reuse across
            # tiles only while those bounds remain unchanged.
            self._source_cache = None
        return super().__call__(grid_ids, start, end)

    def input_files(self):
        return []

    def _geoscf_cell_ids(self, cells: pd.DataFrame) -> pd.Series:
        return pd.Series(geoscf_cell_ids(cells.longitude, cells.latitude), index=cells.index)

    # Pemuat ------------------------------------------------------------------
    def _load_weather(self) -> pd.DataFrame:
        return self.store.read_weather(as_of=self.as_of, since=self._window[0] - LOOKBACK["weather"])

    def _load_geoscf_ana(self) -> pd.DataFrame:
        return self.store.read_geoscf_ana(as_of=self.as_of, since=self._window[0] - LOOKBACK["geoscf_ana"])

    def _load_geoscf_fcst(self) -> pd.DataFrame:
        return self.store.read_geoscf_fcst(as_of=self.as_of, since=self._window[0] - LOOKBACK["geoscf_fcst"])

    def _load_sentinel5p(self) -> pd.DataFrame:
        frame = self.store.read_sentinel5p(as_of=self.as_of)
        return frame[frame.produced_at < self._window[1]].reset_index(drop=True)

    def _load_viirs(self) -> pd.DataFrame:
        products = self.store.products("viirs", as_of=self.as_of, produced_before=self._window[1])
        frames = []
        for record in products.to_dict("records"):
            layers = self.store.layers("viirs", record["product_id"],
                                       ["ntl", "ntl_gap_filled", "longitude", "latitude"])
            record = dict(record, scene_id=record["product_id"], observation_date=record["observed_start_at"])
            frames.append(_viirs_rows(layers, record))
        columns = ["viirs_cell_id", "observation_date_utc", "longitude", "latitude", "ntl", "ntl_available",
                   "observed_end_at", "produced_at", "scene_id"]
        if not frames:
            return pd.DataFrame({c: pd.Series(dtype="datetime64[ns, UTC]" if c in (
                "observation_date_utc", "observed_end_at", "produced_at") else
                {"ntl_available": "bool", "longitude": "float64", "latitude": "float64", "ntl": "float32"}.get(
                    c, "object")) for c in columns})
        return pd.concat(frames, ignore_index=True)[columns]

    def _load_landsat(self, grid_ids) -> pd.DataFrame:
        start, end = self._window
        positions = self._positions.reindex(list(grid_ids)).to_numpy()
        products = self.store.products("landsat", as_of=self.as_of, produced_before=end + pd.Timedelta(1))
        products = products[products.produced_at <= end]
        n = len(positions)
        best = {f: dict(observed=np.full(n, np.iinfo("int64").min), product=np.full(n, -1)) for f in LANDSAT_FEATURES}
        values = {f: np.full(n, np.nan, "float32") for f in LANDSAT_FEATURES}
        frames = []
        base = products[products.produced_at <= start]
        for i, record in enumerate(base.to_dict("records")):
            layers = self.store.layers("landsat", record["product_id"], LANDSAT_FEATURES)
            observed = record["observed_at"].value
            for f in LANDSAT_FEATURES:
                v = layers[f][positions]
                take = np.isfinite(v) & (observed >= best[f]["observed"])
                best[f]["observed"][take] = observed
                best[f]["product"][take] = i
                values[f][take] = v[take]
        records = base.to_dict("records")
        for i, record in enumerate(records):
            chosen = {f: best[f]["product"] == i for f in LANDSAT_FEATURES}
            mask = np.logical_or.reduce([chosen[f] for f in LANDSAT_FEATURES])
            if mask.any():
                frames.append(_landsat_rows(record, np.asarray(list(grid_ids))[mask],
                                            {f: np.where(chosen[f], values[f], np.nan)[mask] for f in LANDSAT_FEATURES}))
        for record in products[products.produced_at > start].to_dict("records"):
            layers = self.store.layers("landsat", record["product_id"], LANDSAT_FEATURES)
            frames.append(_landsat_rows(record, np.asarray(list(grid_ids)),
                                        {f: layers[f][positions] for f in LANDSAT_FEATURES}))
        if not frames:
            return _landsat_rows({"product_id": "", "observed_at": pd.NaT, "produced_at": pd.NaT,
                                  "catalog_created_at": pd.NaT}, np.array([], dtype=object),
                                 {f: np.array([], "float32") for f in LANDSAT_FEATURES})
        return pd.concat(frames, ignore_index=True)


def _viirs_rows(layers: dict, record: dict) -> pd.DataFrame:
    ntl = layers["ntl"]
    return pd.DataFrame({
        "viirs_cell_id": viirs_connector.cell_ids(), "observation_date_utc": record["observation_date"],
        "longitude": layers["longitude"], "latitude": layers["latitude"], "ntl": ntl,
        "ntl_available": np.isfinite(ntl), "observed_end_at": record["observed_at"],
        "produced_at": record["produced_at"], "scene_id": record["scene_id"]})


def _landsat_rows(record: dict, grid_ids: np.ndarray, values: dict) -> pd.DataFrame:
    frame = pd.DataFrame({"grid_id": grid_ids})
    frame["scene_id"] = record["product_id"]
    for col in ("observed_at", "produced_at", "catalog_created_at"):
        value = record.get(col)
        value = pd.NaT if value is None or pd.isna(value) else pd.Timestamp(value).tz_convert("UTC")
        frame[col] = pd.Series(value, index=frame.index, dtype="datetime64[ns, UTC]")
    for f in LANDSAT_FEATURES:
        frame[f] = np.asarray(values[f], dtype="float32")
        frame[f + "_available"] = np.isfinite(frame[f].to_numpy())
    return frame


def landsat_grid_positions() -> tuple[np.ndarray, np.ndarray]:
    """Baris/kolom grid setiap sel aktif, urutan ``active_cells()`` (urutan layer Landsat)."""
    rc = active_cells().grid_id.str.extract(r"^r(\d+)_c(\d+)$").astype(int)
    return rc[0].to_numpy(), rc[1].to_numpy()
