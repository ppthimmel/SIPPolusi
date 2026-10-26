"""Lapisan penyimpanan: PostgreSQL dengan penulisan idempoten dan jejak revisi.

Porting dari versi SQLite pada kolektor udara-collector. Tiga janji yang
dijaga modul ini tidak berubah:

* **Idempoten.** Menulis ulang muatan yang sama tidak mengubah apa pun,
  sehingga tumpang tindih antarlintasan aman. Dengan siklus per jam,
  jendela 48 jam portal kini tertumpang 48 kali lipat.
* **Menetap.** Nilai yang sudah tersimpan tidak pernah hilang diam-diam.
  Perubahan dicatat ke ``observation_revision``; nilai kosong dari portal
  tidak pernah menimpa nilai yang sudah baik.
* **Utuh per lintasan.** Seluruh penulisan satu lintasan berada dalam satu
  transaksi. Galat di tengah jalan mengembalikan basis data ke keadaan
  sebelum lintasan dimulai, bukan menyisakan separuh data.

Seluruh tabel berada pada satu skema PostgreSQL (bawaan ``ground_truth``),
mengikuti konvensi Dokumen Desain subbab 3.5: satu instance, satu skema per
basis data logis.
"""

from __future__ import annotations

import contextlib
import datetime as dt
import functools
import json
import pathlib
import re
import uuid as uuidlib
from typing import Any, Iterable, Sequence

import psycopg
from psycopg import sql
from psycopg.rows import dict_row
from psycopg.types.json import Jsonb

from .normalize import (
    ASSUMED_UNITS,
    UTC,
    DailyIspu,
    IspuPoint,
    MeteoPoint,
    Observation,
    ParsedStation,
)

SCHEMA_PATH = pathlib.Path(__file__).resolve().parent / "schema.sql"
DEFAULT_SCHEMA = "ground_truth"
_SCHEMA_NAME_RE = re.compile(r"^[a-z_][a-z0-9_]{0,62}$")


def iso(value: dt.datetime | dt.date | None) -> str | None:
    """Serialisasi ke ISO UTC. Datetime tanpa zona ditolak, bukan ditebak."""
    if value is None:
        return None
    if isinstance(value, dt.datetime):
        if value.tzinfo is None:
            raise ValueError(
                "datetime tanpa zona tidak boleh disimpan; normalkan lebih dulu"
            )
        return value.astimezone(UTC).isoformat(timespec="seconds")
    return value.isoformat()


def ts(value: dt.datetime | None) -> dt.datetime | None:
    """Siapkan cap waktu untuk kolom TIMESTAMPTZ.

    Penjaga yang sama dengan :func:`iso`: datetime tanpa zona ditolak.
    PostgreSQL akan diam-diam menafsirkannya menurut zona sesi, dan itulah
    jenis galat tujuh jam yang sejak awal dihindari kolektor ini.
    """
    if value is None:
        return None
    if value.tzinfo is None:
        raise ValueError("datetime tanpa zona tidak boleh disimpan; normalkan lebih dulu")
    return value.astimezone(UTC)


def now_utc() -> dt.datetime:
    return dt.datetime.now(UTC).replace(microsecond=0)


class Store:
    def __init__(self, dsn: str, schema: str = DEFAULT_SCHEMA):
        if not dsn:
            raise ValueError(
                "DATABASE_URL belum diatur; Store membutuhkan DSN PostgreSQL, "
                "misalnya postgresql://user:pass@host:5432/sippolusi"
            )
        if not _SCHEMA_NAME_RE.match(schema):
            raise ValueError(f"nama skema tidak sah: {schema!r}")
        self.dsn = dsn
        self.schema = schema
        self.conn = psycopg.connect(dsn, autocommit=True, row_factory=dict_row)
        # Seluruh cap waktu dibaca kembali sebagai UTC, apa pun zona peladen.
        self.conn.execute("SET TIME ZONE 'UTC'")
        self._migrate()
        self.conn.execute(
            sql.SQL("SET search_path TO {}, public").format(sql.Identifier(schema))
        )

    def _migrate(self) -> None:
        """Pasang ekstensi, skema, dan tabel. Aman dijalankan berulang.

        Dikunci dengan advisory lock agar dua proses yang menyala bersamaan
        tidak saling bertabrakan pada DDL.
        """
        with self.conn.transaction():
            self.conn.execute("SELECT pg_advisory_xact_lock(hashtext('spku-migrate'))")
            # PostGIS dipasang di skema public, bukan di skema ground truth,
            # supaya dipakai bersama skema pollution dan osm.
            self.conn.execute("CREATE EXTENSION IF NOT EXISTS postgis WITH SCHEMA public")
            self.conn.execute(
                sql.SQL("CREATE SCHEMA IF NOT EXISTS {}").format(sql.Identifier(self.schema))
            )
            self.conn.execute(
                sql.SQL("SET LOCAL search_path TO {}, public").format(
                    sql.Identifier(self.schema)
                )
            )
            self.conn.execute(SCHEMA_PATH.read_text(encoding="utf-8"))

    def close(self) -> None:
        self.conn.close()

    def __enter__(self) -> "Store":
        return self

    def __exit__(self, *exc) -> None:
        self.close()

    @contextlib.contextmanager
    def transaction(self):
        """Satu lintasan, satu transaksi. Galat apa pun mengembalikan keadaan.

        Padanan ``BEGIN IMMEDIATE`` pada versi SQLite: advisory lock diambil di
        muka sehingga dua lintasan yang tumpang tindih (misalnya lintasan lambat
        yang belum selesai ketika jadwal berikutnya tiba) antre, bukan saling
        menimpa dan berakhir pada pelanggaran kunci unik.
        """
        with self.conn.transaction():
            self.conn.execute(
                "SELECT pg_advisory_xact_lock(hashtext(%s))", (f"spku-write:{self.schema}",)
            )
            yield self

    def _many(self, query: str, rows: Sequence[tuple]) -> None:
        if rows:
            with self.conn.cursor() as cur:
                cur.executemany(query, rows)

    # ---------------------------------------------------------------- lintasan

    def start_run(self, kind: str) -> str:
        run_id = f"{now_utc():%Y%m%dT%H%M%SZ}-{uuidlib.uuid4().hex[:6]}"
        self.conn.execute(
            "INSERT INTO run (run_id, kind, started_utc, status) VALUES (%s,%s,%s,'running')",
            (run_id, kind, now_utc()),
        )
        return run_id

    def finish_run(self, run_id: str, status: str, **counts) -> None:
        self.conn.execute(
            """UPDATE run SET finished_utc=%s, status=%s, stations_seen=%s, stations_ok=%s,
                   rows_new=%s, rows_revised=%s, note=%s WHERE run_id=%s""",
            (
                now_utc(),
                status,
                counts.get("stations_seen", 0),
                counts.get("stations_ok", 0),
                counts.get("rows_new", 0),
                counts.get("rows_revised", 0),
                counts.get("note"),
                run_id,
            ),
        )

    def log_fetch(self, run_id: str, **fields) -> None:
        self.conn.execute(
            """INSERT INTO fetch_log
               (run_id, url, fetched_utc, http_status, bytes, sha256, elapsed_ms,
                parse_ok, n_rows, archive_path, error)
               VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)""",
            (
                run_id,
                fields.get("url"),
                ts(fields.get("fetched_utc") or now_utc()),
                fields.get("http_status"),
                fields.get("bytes"),
                fields.get("sha256"),
                fields.get("elapsed_ms"),
                bool(fields.get("parse_ok")),
                fields.get("n_rows", 0),
                fields.get("archive_path"),
                fields.get("error"),
            ),
        )

    # ----------------------------------------------------------------- stasiun

    def known_station_uuids(self, active_only: bool = True) -> set[str]:
        """Daftar stasiun yang dikenal.

        ``active_only`` penting untuk dua hal: ambang penjaga muatan kosong
        tidak boleh naik terus seiring stasiun yang dinonaktifkan, dan
        peristiwa daftar tidak boleh berulang setiap lintasan untuk stasiun
        yang sudah lama hilang.
        """
        query = "SELECT uuid FROM station" + (" WHERE active" if active_only else "")
        return {r["uuid"] for r in self.conn.execute(query)}

    def upsert_station(self, parsed: ParsedStation, initial: str | None = None) -> None:
        """Perbarui metadata stasiun tanpa pernah menimpanya dengan kosong.

        Halaman yang terdegradasi mengembalikan seluruh metadata bernilai
        kosong meski HTTP-nya 200. Tanpa COALESCE, satu lintasan semacam itu
        menghapus koordinat yang justru satu-satunya lapangan spasial yang
        dapat dipercaya.
        """
        s = parsed.station
        stamp = now_utc()
        self.conn.execute(
            """INSERT INTO station (uuid, kode, initial, name, type, lat, lng, kecamatan,
                                    kota, alamat, dioperasikan, first_seen_utc, last_seen_utc, active)
               VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,TRUE)
               ON CONFLICT (uuid) DO UPDATE SET
                   kode=COALESCE(excluded.kode, station.kode),
                   initial=COALESCE(excluded.initial, station.initial),
                   name=COALESCE(excluded.name, station.name),
                   type=COALESCE(excluded.type, station.type),
                   lat=COALESCE(excluded.lat, station.lat),
                   lng=COALESCE(excluded.lng, station.lng),
                   kecamatan=COALESCE(excluded.kecamatan, station.kecamatan),
                   kota=COALESCE(excluded.kota, station.kota),
                   alamat=COALESCE(excluded.alamat, station.alamat),
                   dioperasikan=COALESCE(excluded.dioperasikan, station.dioperasikan),
                   last_seen_utc=excluded.last_seen_utc,
                   active=TRUE""",
            (s.uuid, s.kode, initial, s.name, s.type, s.lat, s.lng, s.kecamatan,
             s.kota, s.alamat, s.dioperasikan, stamp, stamp),
        )
        self._many(
            """INSERT INTO station_metric (station_uuid, metric, unit_assumed,
                                           first_seen_utc, last_seen_utc)
               VALUES (%s,%s,%s,%s,%s)
               ON CONFLICT (station_uuid, metric) DO UPDATE SET last_seen_utc=excluded.last_seen_utc""",
            [(s.uuid, metric, ASSUMED_UNITS.get(metric), stamp, stamp)
             for metric in dict.fromkeys(parsed.metrics)],
        )

    def record_roster(self, current: dict[str, str]) -> list[tuple[str, str, str]]:
        """Bandingkan daftar stasiun saat ini dengan yang sedang aktif.

        Perbandingan dilakukan terhadap stasiun aktif saja, sehingga stasiun
        yang sudah lama hilang tidak menghasilkan peristiwa berulang dan
        stasiun yang menyala kembali tercatat sebagai kemunculan baru.
        """
        active = self.known_station_uuids(active_only=True)
        stamp = now_utc()
        events: list[tuple[str, str, str]] = []
        for station_uuid, label in current.items():
            if station_uuid not in active:
                events.append((station_uuid, label, "appeared"))
        for station_uuid in active - set(current):
            events.append((station_uuid, "", "disappeared"))
            self.conn.execute("UPDATE station SET active=FALSE WHERE uuid=%s", (station_uuid,))
        self._many(
            "INSERT INTO roster_event (station_uuid, label, event, at_utc) VALUES (%s,%s,%s,%s)",
            [(station_uuid, label, event, stamp) for station_uuid, label, event in events],
        )
        return events

    # -------------------------------------------------------------- observasi

    def _existing(self, table: str, columns: str, keyed_rows: Sequence) -> list[dict]:
        """Baris tersimpan yang mungkin bertabrakan dengan muatan masuk.

        Versi SQLite membaca seluruh riwayat setiap stasiun. Di PostgreSQL yang
        diakses lewat jaringan dan dengan siklus per jam, itu berarti membaca
        jutaan baris per lintasan setelah beberapa bulan. Pencarian dibatasi
        pada rentang cap waktu muatan; hasilnya identik karena hanya koordinat
        muatan yang dibandingkan.
        """
        stamps = [ts(r.ts_utc) for r in keyed_rows]
        return list(self.conn.execute(
            f"SELECT {columns} FROM {table} "
            "WHERE station_uuid = ANY(%s) AND ts_utc BETWEEN %s AND %s",
            (sorted({r.station_uuid for r in keyed_rows}), min(stamps), max(stamps)),
        ))

    def upsert_observations(self, rows: Sequence[Observation]) -> tuple[int, int]:
        """Sisipkan baris baru dan catat revisi. Mengembalikan (baru, revisi).

        Tiga jalur penanganan:

        * koordinat belum ada -> sisipkan;
        * nilai berubah, keduanya terisi -> catat revisi lalu perbarui;
        * portal mengirim kosong untuk koordinat yang sudah terisi ->
          **pertahankan nilai lama**. Kekosongan sesaat pada sumber bukan
          alasan menghapus pengukuran yang sudah benar.

        Bendera kendali mutu diperbarui walau nilainya tidak berubah, karena
        deret macet baru dapat dikenali setelah cukup panjang.
        """
        if not rows:
            return 0, 0
        # Satu muatan yang sama boleh membawa koordinat (station_uuid, metric,
        # ts_utc) yang berulang -- portal kadang menyajikan entri yang
        # tumpang tindih dalam satu respons. Tanpa langkah ini, dua baris
        # dengan koordinat sama-sama tidak ditemukan pada 'existing' dan
        # keduanya masuk ke 'inserts', lalu INSERT kedua menabrak kunci utama
        # sehingga seluruh transaksi batal (insiden 16-17 September 2026).
        # Kemunculan terakhir per koordinat yang menang.
        deduped: dict[tuple[str, str, dt.datetime], Observation] = {}
        for row in rows:
            deduped[(row.station_uuid, row.metric, ts(row.ts_utc))] = row
        rows = list(deduped.values())
        stamp = now_utc()
        existing = {
            (r["station_uuid"], r["metric"], r["ts_utc"]): r
            for r in self._existing("observation", "station_uuid, metric, ts_utc, value, qc", rows)
        }

        inserts: list[tuple] = []
        revisions: list[tuple] = []
        updates: list[tuple] = []
        qc_only: list[tuple] = []

        for row in rows:
            key = (row.station_uuid, row.metric, ts(row.ts_utc))
            prior = existing.get(key)
            if prior is None:
                inserts.append((*key, row.value, row.qc, stamp))
                continue
            value_changed = _differs(prior["value"], row.value)
            if value_changed and row.value is None:
                # Portal mengirim kosong untuk nilai yang sudah baik: abaikan.
                if prior["qc"] != row.qc:
                    qc_only.append((row.qc, *key))
                continue
            if value_changed:
                revisions.append(("observation", *key, prior["value"], row.value, stamp))
                updates.append((row.value, row.qc, *key))
            elif prior["qc"] != row.qc:
                qc_only.append((row.qc, *key))

        self._many(
            """INSERT INTO observation
               (station_uuid, metric, ts_utc, value, qc, first_seen_utc)
               VALUES (%s,%s,%s,%s,%s,%s)""",
            inserts,
        )
        self._many(
            """INSERT INTO observation_revision
               (series, station_uuid, metric, ts_utc, old_value, new_value, noticed_utc)
               VALUES (%s,%s,%s,%s,%s,%s,%s)""",
            revisions,
        )
        self._many(
            "UPDATE observation SET value=%s, qc=%s WHERE station_uuid=%s AND metric=%s AND ts_utc=%s",
            updates,
        )
        self._many(
            "UPDATE observation SET qc=%s WHERE station_uuid=%s AND metric=%s AND ts_utc=%s",
            qc_only,
        )
        return len(inserts), len(revisions)

    def upsert_ispu(self, rows: Iterable[IspuPoint]) -> int:
        """Nilai indeks terbitan portal, dengan jejak revisi seperti konsentrasi."""
        deduped: dict[tuple[str, str, dt.datetime], IspuPoint] = {}
        for row in rows:
            deduped[(row.station_uuid, row.metric, ts(row.ts_utc))] = row
        rows = list(deduped.values())
        if not rows:
            return 0
        stamp = now_utc()
        existing = {
            (r["station_uuid"], r["metric"], r["ts_utc"]): r["ispu"]
            for r in self._existing("observation_ispu", "station_uuid, metric, ts_utc, ispu", rows)
        }

        inserts, revisions, updates = [], [], []
        for row in rows:
            key = (row.station_uuid, row.metric, ts(row.ts_utc))
            if key not in existing:
                inserts.append((*key, row.ispu, stamp))
            elif _differs(existing[key], row.ispu) and row.ispu is not None:
                revisions.append(("observation_ispu", *key, existing[key], row.ispu, stamp))
                updates.append((row.ispu, *key))

        self._many(
            """INSERT INTO observation_ispu
               (station_uuid, metric, ts_utc, ispu, first_seen_utc) VALUES (%s,%s,%s,%s,%s)""",
            inserts,
        )
        self._many(
            """INSERT INTO observation_revision
               (series, station_uuid, metric, ts_utc, old_value, new_value, noticed_utc)
               VALUES (%s,%s,%s,%s,%s,%s,%s)""",
            revisions,
        )
        self._many(
            "UPDATE observation_ispu SET ispu=%s WHERE station_uuid=%s AND metric=%s AND ts_utc=%s",
            updates,
        )
        return len(inserts)

    def upsert_meteo(self, rows: Iterable[MeteoPoint]) -> int:
        """Gabungkan per lapangan; bendera dihitung ulang agar sesuai isi baris."""
        deduped: dict[tuple[str, dt.datetime], MeteoPoint] = {}
        for row in rows:
            deduped[(row.station_uuid, ts(row.ts_utc))] = row
        rows = list(deduped.values())
        if not rows:
            return 0
        stamp = now_utc()
        existing = {
            (r["station_uuid"], r["ts_utc"]): r
            for r in self._existing(
                "meteo", "station_uuid, ts_utc, temp_c, rh_pct, wind_ms, qc", rows
            )
        }

        inserts, updates = [], []
        for row in rows:
            key = (row.station_uuid, ts(row.ts_utc))
            prior = existing.get(key)
            if prior is None:
                inserts.append((*key, row.temp_c, row.rh_pct, row.wind_ms, row.qc, stamp))
                continue
            temp = row.temp_c if row.temp_c is not None else prior["temp_c"]
            rh = row.rh_pct if row.rh_pct is not None else prior["rh_pct"]
            wind = row.wind_ms if row.wind_ms is not None else prior["wind_ms"]
            # Bendera hanya berlaku bagi lapangan yang setelah penggabungan
            # memang masih kosong atau masih bermasalah.
            flags = ""
            if temp is None or row.temp_c is not None:
                flags += row.qc_temp
            if rh is None or row.rh_pct is not None:
                flags += row.qc_rh
            if wind is None or row.wind_ms is not None:
                flags += row.qc_wind
            qc = "".join(sorted(set(flags)))
            if (temp, rh, wind, qc) != (prior["temp_c"], prior["rh_pct"],
                                        prior["wind_ms"], prior["qc"]):
                updates.append((temp, rh, wind, qc, *key))

        self._many(
            """INSERT INTO meteo
               (station_uuid, ts_utc, temp_c, rh_pct, wind_ms, qc, first_seen_utc)
               VALUES (%s,%s,%s,%s,%s,%s,%s)""",
            inserts,
        )
        self._many(
            "UPDATE meteo SET temp_c=%s, rh_pct=%s, wind_ms=%s, qc=%s WHERE station_uuid=%s AND ts_utc=%s",
            updates,
        )
        return len(inserts) + len(updates)

    def upsert_daily(self, rows: Iterable[DailyIspu]) -> int:
        """Baris final tidak pernah ditimpa; baris hari berjalan selalu boleh."""
        stamp = now_utc()
        params = [
            (row.station_uuid, row.date_utc, row.max_ispu, row.concentration,
             row.dominant_metric, bool(row.provisional), stamp)
            for row in rows
        ]
        # Satu pernyataan tidak boleh menyentuh baris yang sama dua kali.
        params = list({(p[0], p[1]): p for p in params}.values())
        self._many(
            """INSERT INTO daily_ispu
               (station_uuid, date_utc, max_ispu, concentration, dominant_metric,
                provisional, updated_utc)
               VALUES (%s,%s,%s,%s,%s,%s,%s)
               ON CONFLICT (station_uuid, date_utc) DO UPDATE SET
                   max_ispu=excluded.max_ispu,
                   concentration=excluded.concentration,
                   dominant_metric=excluded.dominant_metric,
                   provisional=excluded.provisional,
                   updated_utc=excluded.updated_utc
               WHERE daily_ispu.provisional""",
            params,
        )
        return len(params)

    def record_status(self, parsed: ParsedStation, fetched: dt.datetime) -> None:
        self.conn.execute(
            """INSERT INTO station_status
               (station_uuid, fetched_utc, ispu_value, ispu_category, ispu_parameter,
                ispu_concentration, last_update_raw, newest_raw_utc, cadence_minutes, n_observations)
               VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)
               ON CONFLICT (station_uuid, fetched_utc) DO UPDATE SET
                   ispu_value=excluded.ispu_value, ispu_category=excluded.ispu_category,
                   ispu_parameter=excluded.ispu_parameter,
                   ispu_concentration=excluded.ispu_concentration,
                   last_update_raw=excluded.last_update_raw,
                   newest_raw_utc=excluded.newest_raw_utc,
                   cadence_minutes=excluded.cadence_minutes,
                   n_observations=excluded.n_observations""",
            (parsed.station.uuid, ts(fetched), parsed.ispu_value, parsed.ispu_category,
             parsed.ispu_parameter, parsed.ispu_concentration, parsed.last_update_raw,
             ts(parsed.newest_raw_ts), parsed.cadence_minutes, len(parsed.observations)),
        )

    def save_snapshot(
        self, payload: list[dict], update_time_wib: str | None, fetched: dt.datetime
    ) -> None:
        """Simpan cuplikan beranda, dikunci pada waktu permintaan."""
        self.conn.execute(
            """INSERT INTO snapshot (fetched_utc, update_time_wib, n_stations, payload_json)
               VALUES (%s,%s,%s,%s)
               ON CONFLICT (fetched_utc) DO UPDATE SET
                   update_time_wib=excluded.update_time_wib,
                   n_stations=excluded.n_stations,
                   payload_json=excluded.payload_json""",
            (ts(fetched), update_time_wib, len(payload or []),
             Jsonb(payload, dumps=_json_dumps)),
        )

    # ---------------------------------------------------------------- baca

    def read_measurements(
        self,
        start: dt.datetime,
        end: dt.datetime,
        metrics: Sequence[str],
        bbox: tuple[float, float, float, float] | None = None,
    ) -> list[dict]:
        """Pengukuran pada rentang [start, end) beserta koordinat stasiun.

        ``bbox`` berbentuk (min_lon, min_lat, max_lon, max_lat) dalam WGS 84,
        sama dengan struktur BBox pada Tabel 3.3 Dokumen Desain.
        """
        where = ["o.ts_utc >= %s", "o.ts_utc < %s", "o.metric = ANY(%s)"]
        params: list[Any] = [ts(start), ts(end), list(metrics)]
        if bbox is not None:
            where.append("ST_Intersects(s.geom, ST_MakeEnvelope(%s, %s, %s, %s, 4326))")
            params.extend(bbox)
        return list(self.conn.execute(
            f"""SELECT o.station_uuid, s.initial, s.name, s.type, s.lat, s.lng,
                       o.metric, o.ts_utc, o.value, o.qc
                FROM observation o JOIN station s ON s.uuid = o.station_uuid
                WHERE {' AND '.join(where)}
                ORDER BY o.ts_utc, s.initial, o.metric""",
            params,
        ))

    # ---------------------------------------------------------------- laporan

    def coverage(self) -> dict:
        return self.conn.execute(
            """SELECT COUNT(*) AS n, MIN(ts_utc) AS t0, MAX(ts_utc) AS t1,
                      COUNT(DISTINCT station_uuid) AS stations FROM observation"""
        ).fetchone()

    def stale_metrics(self, hours: float) -> list[dict]:
        """Parameter yang berhenti mengirim, walau stasiunnya masih hidup.

        Kebasian tingkat stasiun tidak cukup. Teramati pada 15 September 2026:
        DKI02 mengirim lima parameter dengan normal sementara kanal PM10-nya
        sudah 13,6 jam diam dan hanya menghasilkan tujuh titik dalam 28 jam.
        Dilihat dari stasiunnya, semuanya tampak sehat.
        """
        cutoff = now_utc() - dt.timedelta(hours=hours)
        return list(self.conn.execute(
            """SELECT s.initial, s.name, o.metric, MAX(o.ts_utc) AS newest,
                      COUNT(*) AS n
               FROM observation o JOIN station s ON s.uuid = o.station_uuid
               WHERE s.active
               GROUP BY s.uuid, o.metric
               HAVING MAX(o.ts_utc) < %s
               ORDER BY newest ASC""",
            (cutoff,),
        ))

    def metric_gaps(self, hours: float = 48.0, factor: float = 3.0) -> list[dict]:
        """Lubang di dalam jendela: jarak antartitik jauh melebihi iramanya.

        ``factor`` adalah kelipatan irama lazim parameter tersebut yang masih
        dianggap wajar. Tiga kali irama menyaring satu-dua titik yang meleset
        dan hanya menyisakan lubang yang sungguh besar.
        """
        since = now_utc() - dt.timedelta(hours=hours)
        rows = self.conn.execute(
            """SELECT s.initial, o.station_uuid, o.metric, o.ts_utc
               FROM observation o JOIN station s ON s.uuid = o.station_uuid
               WHERE s.active AND o.ts_utc >= %s
               ORDER BY o.station_uuid, o.metric, o.ts_utc""",
            (since,),
        ).fetchall()

        hasil: list[dict] = []
        kunci_lalu = None
        stempel: list[dt.datetime] = []

        def tutup(kunci, stempel):
            if not kunci or len(stempel) < 3:
                return
            jarak = [
                (b - a).total_seconds() / 60 for a, b in zip(stempel, stempel[1:])
            ]
            irama = sorted(jarak)[len(jarak) // 2]
            besar = [g for g in jarak if g > factor * irama]
            if besar:
                hasil.append({
                    "initial": kunci[0], "metric": kunci[2],
                    "irama_menit": int(irama), "lubang": len(besar),
                    "lubang_terbesar_menit": int(max(besar)),
                    "titik": len(stempel),
                })

        for row in rows:
            kunci = (row["initial"], row["station_uuid"], row["metric"])
            if kunci != kunci_lalu:
                tutup(kunci_lalu, stempel)
                kunci_lalu, stempel = kunci, []
            stempel.append(row["ts_utc"])
        tutup(kunci_lalu, stempel)
        return sorted(hasil, key=lambda r: -r["lubang_terbesar_menit"])

    def stale_stations(self, hours: float) -> list[dict]:
        cutoff = now_utc() - dt.timedelta(hours=hours)
        return list(self.conn.execute(
            """SELECT s.initial, s.name, s.type, MAX(o.ts_utc) AS newest
               FROM station s LEFT JOIN observation o ON o.station_uuid = s.uuid
               WHERE s.active
               GROUP BY s.uuid
               HAVING MAX(o.ts_utc) IS NULL OR MAX(o.ts_utc) < %s
               ORDER BY newest ASC NULLS FIRST""",
            (cutoff,),
        ))


_json_dumps = functools.partial(json.dumps, separators=(",", ":"), default=str)


def _differs(a: float | None, b: float | None) -> bool:
    if a is None and b is None:
        return False
    if a is None or b is None:
        return True
    return abs(a - b) > 1e-9
