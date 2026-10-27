"""Penyimpanan PostgreSQL data sumber sekunder dan manifest FeatureMatrix (skema ``features``).

Satu tabel per bentuk data, dengan stempel waktu, identitas sumber, status
kualitas, dan nilai kosong sebagai penanda data hilang (NULL, bukan nol):

- ``fetch_log``: satu baris per pengambilan sumber per siklus (status, jumlah
  permintaan, coba ulang, gagal, produk baru, catatan) untuk penelusuran.
- ``weather_hourly``: Open-Meteo per jam, berupa revisi yang tidak diubah. Revisi
  baru ditulis hanya bila nilainya berubah; pembaca memakai revisi terakhir
  dengan ``fetched_utc`` ≤ waktu acuan, sehingga FeatureMatrix dapat dibangun ulang.
- ``geoscf_ana_hourly``, ``geoscf_fcst_hourly``: GEOS-CF per (sel 0,25°, jam).
- ``satellite_product`` + ``satellite_layer``: satu baris per scene/granule
  (Landsat, VIIRS, Sentinel-5P) dengan waktu observasi/produksi dan ringkasan
  QA; layer per sel disimpan ringkas sebagai larik terkompresi zlib dengan
  urutan sel tetap (Landsat: ``active_cells()``; VIIRS: ``viirs.cell_ids()``).
- ``sentinel5p_observation``: observasi per (scene, sel 5 km).
- ``feature_window``: manifest FeatureMatrix per time window (``feature_version``).

Kunci primer menjadikan penulisan idempoten: produk dan jam yang sudah ada
tidak ditulis ulang (data duplikat diabaikan).
"""

from __future__ import annotations

import datetime as dt
import json
import os
import re
import zlib

import numpy as np
import pandas as pd

from feature_matrix.sources import WEATHER

UTC = dt.timezone.utc
DEFAULT_SCHEMA = "features"
_SCHEMA_NAME_RE = re.compile(r"^[a-z_][a-z0-9_]{0,62}$")

DDL = """
CREATE SCHEMA IF NOT EXISTS {s};
CREATE TABLE IF NOT EXISTS {s}.fetch_log (
    fetch_id           TEXT PRIMARY KEY,
    source             TEXT NOT NULL,
    time_window_start  TIMESTAMPTZ,
    started_utc        TIMESTAMPTZ NOT NULL,
    finished_utc       TIMESTAMPTZ,
    status             TEXT NOT NULL,          -- running | ok | degraded | unavailable | failed | disabled | not_due
    products_new       INTEGER NOT NULL DEFAULT 0,
    rows_written       INTEGER NOT NULL DEFAULT 0,
    requests           INTEGER NOT NULL DEFAULT 0,
    retried_requests   INTEGER NOT NULL DEFAULT 0,
    failed_requests    INTEGER NOT NULL DEFAULT 0,
    note               TEXT,
    detail             JSONB NOT NULL DEFAULT '{{}}'::jsonb
);
CREATE INDEX IF NOT EXISTS fetch_log_source_ix ON {s}.fetch_log (source, started_utc DESC);
CREATE TABLE IF NOT EXISTS {s}.weather_hourly (
    time_utc               TIMESTAMPTZ NOT NULL,
    fetched_utc            TIMESTAMPTZ NOT NULL,
    fetch_id               TEXT NOT NULL,
    temperature_2m         DOUBLE PRECISION,
    relative_humidity_2m   DOUBLE PRECISION,
    wind_speed_10m         DOUBLE PRECISION,
    wind_direction_10m     DOUBLE PRECISION,
    boundary_layer_height  DOUBLE PRECISION,
    precipitation          DOUBLE PRECISION,
    surface_pressure       DOUBLE PRECISION,
    qc                     TEXT[] NOT NULL DEFAULT '{{}}',   -- variabel yang dibuang karena di luar rentang
    PRIMARY KEY (time_utc, fetched_utc)
);
CREATE TABLE IF NOT EXISTS {s}.geoscf_ana_hourly (
    time_window_start  TIMESTAMPTZ NOT NULL,
    cell_id            SMALLINT NOT NULL,
    pm25_ugm3          REAL,
    no2_ppb            REAL,
    no2_ugm3           REAL,
    available_at_utc   TIMESTAMPTZ NOT NULL,
    latency_hours      REAL,
    fetched_utc        TIMESTAMPTZ NOT NULL,
    fetch_id           TEXT NOT NULL,
    PRIMARY KEY (time_window_start, cell_id)
);
CREATE TABLE IF NOT EXISTS {s}.geoscf_fcst_hourly (
    init_utc             TIMESTAMPTZ NOT NULL,
    time_window_start    TIMESTAMPTZ NOT NULL,
    cell_id              SMALLINT NOT NULL,
    lead_hours           REAL NOT NULL,
    pm25_ugm3            REAL,
    no2_ppb              REAL,
    no2_ugm3             REAL,
    first_published_utc  TIMESTAMPTZ,
    available_at_utc     TIMESTAMPTZ NOT NULL,
    fetched_utc          TIMESTAMPTZ NOT NULL,
    fetch_id             TEXT NOT NULL,
    PRIMARY KEY (init_utc, time_window_start, cell_id)
);
CREATE TABLE IF NOT EXISTS {s}.satellite_product (
    source              TEXT NOT NULL,         -- landsat | viirs | sentinel5p
    product_id          TEXT NOT NULL,         -- scene_id / nama granule
    observed_start_at   TIMESTAMPTZ,
    observed_at         TIMESTAMPTZ NOT NULL,
    produced_at         TIMESTAMPTZ NOT NULL,  -- proksi waktu tersedia
    catalog_created_at  TIMESTAMPTZ,
    fetched_utc         TIMESTAMPTZ NOT NULL,
    fetch_id            TEXT NOT NULL,
    processing_version  TEXT NOT NULL,
    source_url          TEXT,
    n_cells             INTEGER NOT NULL,
    quality             JSONB NOT NULL DEFAULT '{{}}'::jsonb,
    PRIMARY KEY (source, product_id)
);
CREATE INDEX IF NOT EXISTS satellite_product_produced_ix ON {s}.satellite_product (source, produced_at);
CREATE TABLE IF NOT EXISTS {s}.satellite_layer (
    source      TEXT NOT NULL,
    product_id  TEXT NOT NULL,
    layer       TEXT NOT NULL,
    dtype       TEXT NOT NULL,
    n           INTEGER NOT NULL,
    data        BYTEA NOT NULL,               -- zlib(larik numpy little-endian)
    PRIMARY KEY (source, product_id, layer),
    FOREIGN KEY (source, product_id) REFERENCES {s}.satellite_product ON DELETE CASCADE
);
CREATE TABLE IF NOT EXISTS {s}.sentinel5p_observation (
    product_id            TEXT NOT NULL,
    grid_id               TEXT NOT NULL,
    source                TEXT NOT NULL DEFAULT 'sentinel5p' CHECK (source = 'sentinel5p'),
    observed_start_at     TIMESTAMPTZ NOT NULL,
    observed_at           TIMESTAMPTZ NOT NULL,
    produced_at           TIMESTAMPTZ NOT NULL,
    no2_mol_m2            DOUBLE PRECISION,
    no2_precision_mol_m2  DOUBLE PRECISION,
    n_valid_pixels        INTEGER NOT NULL,
    available             BOOLEAN NOT NULL,
    PRIMARY KEY (product_id, grid_id),
    FOREIGN KEY (source, product_id) REFERENCES {s}.satellite_product ON DELETE CASCADE
);
CREATE TABLE IF NOT EXISTS {s}.feature_window (
    time_window_start  TIMESTAMPTZ NOT NULL,
    feature_version    TEXT NOT NULL,         -- SHA-256 isi FeatureMatrix
    time_utc           TIMESTAMPTZ NOT NULL,  -- waktu inferensi τ = akhir time window
    built_utc          TIMESTAMPTZ NOT NULL,  -- acuan revisi sumber (dibangun ulang dengan as_of = built_utc)
    run_id             TEXT,
    cells              INTEGER NOT NULL,
    columns            JSONB NOT NULL,
    availability       JSONB NOT NULL,
    sources            JSONB NOT NULL,
    parquet_path       TEXT,
    PRIMARY KEY (time_window_start, feature_version)
);
"""

#: Retensi bawaan per tabel/sumber (hari). As-of FeatureMatrix memakai nilai valid
#: terakhir; pada data latih umur maksimum Landsat ±200 hari, Sentinel-5P ±10, VIIRS ±17.
RETENTION_DAYS = {"weather": 45, "geoscf_ana": 14, "geoscf_fcst": 7, "sentinel5p": 30, "viirs": 60,
                  "landsat": 400, "fetch_log": 90}


def now_utc() -> dt.datetime:
    return dt.datetime.now(UTC)


def schema_name(environ=None) -> str:
    env = os.environ if environ is None else environ
    name = env.get("FEATURES_DB_SCHEMA") or DEFAULT_SCHEMA
    if not _SCHEMA_NAME_RE.match(name):
        raise ValueError(f"nama skema FEATURES_DB_SCHEMA tidak sah: {name!r}")
    return name


def encode_layer(values: np.ndarray) -> tuple[str, int, bytes]:
    array = np.ascontiguousarray(values)
    dtype = array.dtype.newbyteorder("<")
    return dtype.str, int(array.size), zlib.compress(array.astype(dtype).tobytes(), 6)


def decode_layer(dtype: str, n: int, data: bytes) -> np.ndarray:
    array = np.frombuffer(zlib.decompress(data), dtype=np.dtype(dtype))
    if array.size != n:
        raise ValueError(f"layer rusak: {array.size} elemen, seharusnya {n}")
    return array


def _none(value):
    """NaN/NaT → NULL; skalar numpy → Python."""
    if value is None or (isinstance(value, float) and np.isnan(value)) or value is pd.NaT:
        return None
    if isinstance(value, np.generic):
        value = value.item()
        return None if isinstance(value, float) and np.isnan(value) else value
    if isinstance(value, pd.Timestamp):
        return value.to_pydatetime()
    return value


def _rows(frame: pd.DataFrame, columns: list[str]) -> list[tuple]:
    return [tuple(_none(v) for v in row) for row in frame[columns].itertuples(index=False, name=None)]


def _utc(series: pd.Series) -> pd.Series:
    return pd.to_datetime(series, utc=True).astype("datetime64[ns, UTC]")


class FeatureStore:
    def __init__(self, conn, schema: str = DEFAULT_SCHEMA):
        if not _SCHEMA_NAME_RE.match(schema):
            raise ValueError(f"nama skema tidak sah: {schema!r}")
        self.conn = conn
        self.schema = schema
        self.s = f'"{schema}"'

    @classmethod
    def connect(cls, dsn: str, schema: str | None = None) -> "FeatureStore":
        import psycopg
        from psycopg.rows import dict_row

        conn = psycopg.connect(dsn, autocommit=True, row_factory=dict_row)
        conn.execute("SET TIME ZONE 'UTC'")
        return cls(conn, schema or schema_name())

    def close(self) -> None:
        self.conn.close()

    def __enter__(self) -> "FeatureStore":
        return self

    def __exit__(self, *exc) -> None:
        self.close()

    def ensure_schema(self) -> None:
        with self.conn.transaction():
            self.conn.execute("SELECT pg_advisory_xact_lock(hashtext('features-migrate'))")
            self.conn.execute(DDL.format(s=self.s))

    def _query(self, sql: str, params=()) -> pd.DataFrame:
        """Hasil kueri sebagai DataFrame; kolomnya tetap ada walau tanpa baris."""
        cur = self.conn.execute(sql.format(s=self.s), params)
        return pd.DataFrame(cur.fetchall(), columns=[d.name for d in cur.description])

    def _insert(self, table: str, columns: list[str], rows: list[tuple]) -> int:
        """Sisipkan baris dalam satu transaksi; baris dengan kunci yang sudah ada diabaikan.

        Mengembalikan jumlah baris yang benar-benar baru.
        """
        if not rows:
            return 0
        names = ", ".join(columns)
        with self.conn.transaction(), self.conn.cursor() as cur:
            cur.execute(f"CREATE TEMP TABLE _incoming (LIKE {self.s}.{table} INCLUDING DEFAULTS) ON COMMIT DROP")
            cur.executemany(f"INSERT INTO _incoming ({names}) VALUES ({', '.join(['%s'] * len(columns))})", rows)
            cur.execute(f"INSERT INTO {self.s}.{table} ({names}) SELECT {names} FROM _incoming ON CONFLICT DO NOTHING")
            return cur.rowcount

    # ------------------------------------------------------------- fetch_log

    def start_fetch(self, source: str, window_start: dt.datetime | None) -> str:
        started = now_utc()
        fetch_id = f"{source}-{started:%Y%m%dT%H%M%S%fZ}"
        self.conn.execute(
            f"INSERT INTO {self.s}.fetch_log (fetch_id, source, time_window_start, started_utc, status) "
            "VALUES (%s, %s, %s, %s, 'running')", (fetch_id, source, window_start, started))
        return fetch_id

    def finish_fetch(self, fetch_id: str, status: str, *, products_new: int = 0, rows_written: int = 0,
                     counts: dict | None = None, note: str | None = None, detail: dict | None = None) -> None:
        counts = counts or {}
        self.conn.execute(
            f"""UPDATE {self.s}.fetch_log SET finished_utc = %s, status = %s, products_new = %s, rows_written = %s,
                  requests = %s, retried_requests = %s, failed_requests = %s, note = %s, detail = %s
                WHERE fetch_id = %s""",
            (now_utc(), status, products_new, rows_written, counts.get("requests", 0),
             counts.get("retried_requests", 0), counts.get("failed_requests", 0), note,
             json.dumps(detail or {}, default=str), fetch_id))

    def last_success(self, source: str) -> dt.datetime | None:
        row = self.conn.execute(
            f"SELECT max(started_utc) AS t FROM {self.s}.fetch_log WHERE source = %s AND status IN ('ok', 'degraded')",
            (source,)).fetchone()
        return row["t"]

    def fetch_log(self, since: dt.datetime) -> pd.DataFrame:
        return self._query("SELECT * FROM {s}.fetch_log WHERE started_utc >= %s ORDER BY started_utc", (since,))

    # --------------------------------------------------------------- cuaca

    def write_weather(self, frame: pd.DataFrame, fetch_id: str, fetched_utc: dt.datetime) -> int:
        """Revisi baru untuk jam yang belum ada atau nilainya berubah; mengembalikan jumlah baris ditulis."""
        if frame.empty:
            return 0
        latest = self.read_weather(since=frame.time_utc.min(), until=frame.time_utc.max() + pd.Timedelta(hours=1),
                                   raw=True)
        incoming = frame[["time_utc", *WEATHER]].copy()
        incoming["qc"] = [[v for v in WEATHER if row[f"{v}_qc"]] for _, row in frame.iterrows()] \
            if any(f"{v}_qc" in frame for v in WEATHER) else [[] for _ in range(len(frame))]
        if not latest.empty:
            merged = incoming.merge(latest[["time_utc", *WEATHER, "qc"]], on="time_utc", how="left",
                                    suffixes=("", "_old"), indicator=True)
            same = merged["_merge"].eq("both")
            for v in WEATHER:
                a, b = merged[v].to_numpy("float64"), merged[f"{v}_old"].to_numpy("float64")
                same &= (a == b) | (np.isnan(a) & np.isnan(b))
            same &= np.array([sorted(x) == sorted(y) if isinstance(y, list) else False
                              for x, y in zip(merged.qc, merged.qc_old)], dtype=bool)
            incoming = incoming[~same.to_numpy()]
        if incoming.empty:
            return 0
        incoming = incoming.assign(fetched_utc=fetched_utc, fetch_id=fetch_id)
        cols = ["time_utc", "fetched_utc", "fetch_id", *WEATHER, "qc"]
        return self._insert("weather_hourly", cols, _rows(incoming, cols))

    def read_weather(self, as_of: dt.datetime | None = None, since: dt.datetime | None = None,
                     until: dt.datetime | None = None, raw: bool = False) -> pd.DataFrame:
        """Revisi terakhir per jam (``fetched_utc`` ≤ ``as_of``), berskema ``open_meteo/hourly.parquet``."""
        frame = self._query(
            """SELECT DISTINCT ON (time_utc) * FROM {s}.weather_hourly
               WHERE (%(as_of)s::timestamptz IS NULL OR fetched_utc <= %(as_of)s)
                 AND (%(since)s::timestamptz IS NULL OR time_utc >= %(since)s)
                 AND (%(until)s::timestamptz IS NULL OR time_utc < %(until)s)
               ORDER BY time_utc, fetched_utc DESC""", {"as_of": as_of, "since": since, "until": until})
        if frame.empty:
            return pd.DataFrame(columns=["time_utc", *WEATHER, "qc", "fetched_utc"]) if raw else \
                pd.DataFrame({"time_utc": pd.Series(dtype="datetime64[ns, UTC]"),
                              **{c: pd.Series(dtype="float64") for v in WEATHER for c in (v,)},
                              **{f"{v}_available": pd.Series(dtype="bool") for v in WEATHER}})
        frame["time_utc"] = _utc(frame.time_utc)
        for v in WEATHER:
            frame[v] = pd.to_numeric(frame[v], errors="coerce").astype("float64")
            frame[f"{v}_available"] = frame[v].notna()
        if raw:
            return frame
        frame["feature_available_at"] = pd.Series(pd.NaT, index=frame.index, dtype="datetime64[ns, UTC]")
        return frame[["time_utc", *[c for v in WEATHER for c in (v, f"{v}_available")], "feature_available_at"]]

    # -------------------------------------------------------------- GEOS-CF

    def stored_ana_hours(self, since: dt.datetime) -> set:
        rows = self.conn.execute(
            f"SELECT DISTINCT time_window_start AS t FROM {self.s}.geoscf_ana_hourly WHERE time_window_start >= %s",
            (since,)).fetchall()
        return {pd.Timestamp(r["t"]).tz_convert("UTC") for r in rows}

    def write_geoscf_ana(self, frame: pd.DataFrame, fetch_id: str, fetched_utc: dt.datetime) -> int:
        if frame.empty:
            return 0
        frame = frame.assign(fetched_utc=fetched_utc, fetch_id=fetch_id)
        cols = ["time_window_start", "cell_id", "pm25_ugm3", "no2_ppb", "no2_ugm3", "available_at_utc",
                "latency_hours", "fetched_utc", "fetch_id"]
        return self._insert("geoscf_ana_hourly", cols, _rows(frame, cols))

    def read_geoscf_ana(self, as_of: dt.datetime | None = None, since: dt.datetime | None = None) -> pd.DataFrame:
        """Skema ``geoscf_cell_hourly.parquet``."""
        frame = self._query(
            """SELECT * FROM {s}.geoscf_ana_hourly
               WHERE (%(as_of)s::timestamptz IS NULL OR fetched_utc <= %(as_of)s)
                 AND (%(since)s::timestamptz IS NULL OR time_window_start >= %(since)s)
               ORDER BY time_window_start, cell_id""", {"as_of": as_of, "since": since})
        columns = ["time_window_start", "cell_id", "pm25_ugm3", "no2_ugm3", "no2_ppb", "available_pm25",
                   "available_no2", "available_at_utc", "latency_hours", "source"]
        if frame.empty:
            return pd.DataFrame({c: pd.Series(dtype={"cell_id": "int64", "available_pm25": "bool",
                                                     "available_no2": "bool", "source": "object"}.get(
                c, "datetime64[ns, UTC]" if c in ("time_window_start", "available_at_utc") else "float32"))
                for c in columns})
        for col in ("time_window_start", "available_at_utc"):
            frame[col] = _utc(frame[col])
        for col in ("pm25_ugm3", "no2_ugm3", "no2_ppb", "latency_hours"):
            frame[col] = pd.to_numeric(frame[col], errors="coerce").astype("float32")
        frame["cell_id"] = frame.cell_id.astype("int64")
        frame["available_pm25"] = frame.pm25_ugm3.notna()
        frame["available_no2"] = frame.no2_ppb.notna()
        frame["source"] = "geos_cf_v2_ana"
        return frame[columns]

    def stored_fcst_inits(self, since: dt.datetime) -> set:
        rows = self.conn.execute(
            f"SELECT DISTINCT init_utc AS t FROM {self.s}.geoscf_fcst_hourly WHERE init_utc >= %s", (since,)).fetchall()
        return {pd.Timestamp(r["t"]).tz_convert("UTC") for r in rows}

    def write_geoscf_fcst(self, frame: pd.DataFrame, fetch_id: str, fetched_utc: dt.datetime) -> int:
        if frame.empty:
            return 0
        frame = frame.assign(fetched_utc=fetched_utc, fetch_id=fetch_id)
        cols = ["init_utc", "time_window_start", "cell_id", "lead_hours", "pm25_ugm3", "no2_ppb", "no2_ugm3",
                "first_published_utc", "available_at_utc", "fetched_utc", "fetch_id"]
        return self._insert("geoscf_fcst_hourly", cols, _rows(frame, cols))

    def read_geoscf_fcst(self, as_of: dt.datetime | None = None, since: dt.datetime | None = None) -> pd.DataFrame:
        """Skema ``geoscf_fcst_cell_hourly.parquet``."""
        frame = self._query(
            """SELECT * FROM {s}.geoscf_fcst_hourly
               WHERE (%(as_of)s::timestamptz IS NULL OR fetched_utc <= %(as_of)s)
                 AND (%(since)s::timestamptz IS NULL OR time_window_start >= %(since)s)
               ORDER BY init_utc, time_window_start, cell_id""", {"as_of": as_of, "since": since})
        columns = ["init_utc", "time_window_start", "lead_hours", "cell_id", "pm25_ugm3", "no2_ppb",
                   "first_published_utc", "available_at_utc", "no2_ugm3", "available_pm25", "available_no2"]
        times = ("init_utc", "time_window_start", "first_published_utc", "available_at_utc")
        if frame.empty:
            return pd.DataFrame({c: pd.Series(dtype="datetime64[ns, UTC]" if c in times else
                                              {"cell_id": "int64", "available_pm25": "bool",
                                               "available_no2": "bool"}.get(c, "float32")) for c in columns})
        for col in times:
            frame[col] = _utc(frame[col])
        for col in ("lead_hours", "pm25_ugm3", "no2_ppb", "no2_ugm3"):
            frame[col] = pd.to_numeric(frame[col], errors="coerce").astype("float32")
        frame["cell_id"] = frame.cell_id.astype("int64")
        frame["available_pm25"] = frame.pm25_ugm3.notna()
        frame["available_no2"] = frame.no2_ppb.notna()
        return frame[columns]

    # ------------------------------------------------------------- satelit

    def product_ids(self, source: str, since: dt.datetime | None = None) -> set:
        rows = self.conn.execute(
            f"""SELECT product_id FROM {self.s}.satellite_product
                WHERE source = %s AND (%s::timestamptz IS NULL OR observed_at >= %s)""",
            (source, since, since)).fetchall()
        return {r["product_id"] for r in rows}

    def write_product(self, source: str, record: dict, *, fetch_id: str, fetched_utc: dt.datetime,
                      processing_version: str, n_cells: int, layers: dict | None = None,
                      observations: pd.DataFrame | None = None, quality: dict | None = None) -> bool:
        """Satu produk beserta layer/observasinya dalam satu transaksi; ``False`` bila sudah ada."""
        with self.conn.transaction():
            row = self.conn.execute(
                f"""INSERT INTO {self.s}.satellite_product (source, product_id, observed_start_at, observed_at,
                      produced_at, catalog_created_at, fetched_utc, fetch_id, processing_version, source_url,
                      n_cells, quality)
                    VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s) ON CONFLICT DO NOTHING RETURNING product_id""",
                (source, record["scene_id"], _none(record.get("observed_start_at")), _none(record["observed_at"]),
                 _none(record["produced_at"]), _none(pd.Timestamp(record["catalog_created_at"]))
                 if record.get("catalog_created_at") else None, fetched_utc, fetch_id, processing_version,
                 record.get("source_url"), n_cells, json.dumps(quality or {}, default=str))).fetchone()
            if row is None:
                return False
            with self.conn.cursor() as cur:
                if layers:
                    cur.executemany(
                        f"INSERT INTO {self.s}.satellite_layer (source, product_id, layer, dtype, n, data) "
                        "VALUES (%s,%s,%s,%s,%s,%s)",
                        [(source, record["scene_id"], name, *encode_layer(values)) for name, values in layers.items()])
                if observations is not None and len(observations):
                    cols = ["product_id", "grid_id", "observed_start_at", "observed_at", "produced_at", "no2_mol_m2",
                            "no2_precision_mol_m2", "n_valid_pixels", "available"]
                    obs = observations.rename(columns={"scene_id": "product_id"})
                    cur.executemany(
                        f"INSERT INTO {self.s}.sentinel5p_observation ({', '.join(cols)}) "
                        f"VALUES ({', '.join(['%s'] * len(cols))})", _rows(obs, cols))
        return True

    def products(self, source: str, as_of: dt.datetime | None = None, since: dt.datetime | None = None,
                 produced_before: dt.datetime | None = None) -> pd.DataFrame:
        frame = self._query(
            """SELECT * FROM {s}.satellite_product
               WHERE source = %(source)s AND (%(as_of)s::timestamptz IS NULL OR fetched_utc <= %(as_of)s)
                 AND (%(since)s::timestamptz IS NULL OR observed_at >= %(since)s)
                 AND (%(before)s::timestamptz IS NULL OR produced_at < %(before)s)
               ORDER BY produced_at, observed_at, product_id""",
            {"source": source, "as_of": as_of, "since": since, "before": produced_before})
        for col in ("observed_start_at", "observed_at", "produced_at", "catalog_created_at", "fetched_utc"):
            if col in frame:
                frame[col] = _utc(frame[col])
        return frame

    def layers(self, source: str, product_id: str, names: list[str] | None = None) -> dict:
        rows = self.conn.execute(
            f"""SELECT layer, dtype, n, data FROM {self.s}.satellite_layer
                WHERE source = %s AND product_id = %s AND (%s::text[] IS NULL OR layer = ANY(%s))""",
            (source, product_id, names, names)).fetchall()
        return {r["layer"]: decode_layer(r["dtype"], r["n"], bytes(r["data"])) for r in rows}

    def read_sentinel5p(self, as_of: dt.datetime | None = None, since: dt.datetime | None = None) -> pd.DataFrame:
        """Skema ``sentinel5p/observations.parquet``."""
        frame = self._query(
            """SELECT o.product_id AS scene_id, o.grid_id, o.observed_start_at, o.observed_at, o.produced_at,
                      o.no2_mol_m2, o.no2_precision_mol_m2, o.n_valid_pixels, o.available
               FROM {s}.sentinel5p_observation o JOIN {s}.satellite_product p
                 ON p.source = 'sentinel5p' AND p.product_id = o.product_id
               WHERE (%(as_of)s::timestamptz IS NULL OR p.fetched_utc <= %(as_of)s)
                 AND (%(since)s::timestamptz IS NULL OR o.observed_at >= %(since)s)
               ORDER BY o.observed_start_at, o.product_id, o.grid_id""", {"as_of": as_of, "since": since})
        columns = ["scene_id", "grid_id", "observed_start_at", "observed_at", "produced_at", "no2_mol_m2",
                   "no2_precision_mol_m2", "n_valid_pixels", "available"]
        times = ("observed_start_at", "observed_at", "produced_at")
        if frame.empty:
            return pd.DataFrame({c: pd.Series(dtype="datetime64[ns, UTC]" if c in times else
                                              {"n_valid_pixels": "int64", "available": "bool",
                                               "no2_mol_m2": "float64", "no2_precision_mol_m2": "float64"}.get(
                                                  c, "object"))
                                 for c in columns})
        for col in times:
            frame[col] = _utc(frame[col])
        for col in ("no2_mol_m2", "no2_precision_mol_m2"):
            frame[col] = pd.to_numeric(frame[col], errors="coerce").astype("float64")
        frame["n_valid_pixels"] = frame.n_valid_pixels.astype("int64")
        frame["available"] = frame.available.astype(bool)
        return frame[columns]

    # ------------------------------------------------------ FeatureMatrix

    def write_feature_window(self, manifest: dict) -> bool:
        row = self.conn.execute(
            f"""INSERT INTO {self.s}.feature_window (time_window_start, feature_version, time_utc, built_utc, run_id,
                  cells, columns, availability, sources, parquet_path)
                VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s) ON CONFLICT DO NOTHING RETURNING feature_version""",
            (manifest["time_window_start"], manifest["feature_version"], manifest["time_utc"], manifest["built_utc"],
             manifest.get("run_id"), manifest["cells"], json.dumps(manifest["columns"]),
             json.dumps(manifest["availability"], default=str), json.dumps(manifest["sources"], default=str),
             manifest.get("parquet_path"))).fetchone()
        return row is not None

    def feature_windows(self, time_window_start: dt.datetime) -> list[dict]:
        return self.conn.execute(
            f"SELECT * FROM {self.s}.feature_window WHERE time_window_start = %s ORDER BY built_utc DESC",
            (time_window_start,)).fetchall()

    # ------------------------------------------------------------- retensi

    def prune(self, now: dt.datetime, retention: dict | None = None) -> dict:
        """Hapus data yang lebih tua dari retensi; mengembalikan jumlah baris terhapus per tabel."""
        days = {**RETENTION_DAYS, **(retention or {})}
        cut = {k: now - dt.timedelta(days=v) for k, v in days.items()}
        statements = {
            "weather_hourly": ("DELETE FROM {s}.weather_hourly WHERE time_utc < %s", cut["weather"]),
            "geoscf_ana_hourly": ("DELETE FROM {s}.geoscf_ana_hourly WHERE time_window_start < %s", cut["geoscf_ana"]),
            "geoscf_fcst_hourly": ("DELETE FROM {s}.geoscf_fcst_hourly WHERE init_utc < %s", cut["geoscf_fcst"]),
            "fetch_log": ("DELETE FROM {s}.fetch_log WHERE started_utc < %s", cut["fetch_log"]),
        }
        removed = {}
        with self.conn.transaction():
            for name, (statement, moment) in statements.items():
                removed[name] = self.conn.execute(statement.format(s=self.s), (moment,)).rowcount
            for source in ("landsat", "viirs", "sentinel5p"):
                removed[f"satellite_product:{source}"] = self.conn.execute(
                    f"DELETE FROM {self.s}.satellite_product WHERE source = %s AND observed_at < %s",
                    (source, cut[source])).rowcount
        return {k: v for k, v in removed.items() if v}

    def table_sizes(self) -> dict:
        rows = self.conn.execute(
            """SELECT c.relname AS name, pg_total_relation_size(c.oid) AS bytes
               FROM pg_class c JOIN pg_namespace n ON n.oid = c.relnamespace
               WHERE n.nspname = %s AND c.relkind = 'r' ORDER BY 1""", (self.schema,)).fetchall()
        return {r["name"]: int(r["bytes"]) for r in rows}
