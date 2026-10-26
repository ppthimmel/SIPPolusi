#!/usr/bin/env python3
"""Pindahkan basis data SQLite kolektor udara-collector ke PostgreSQL.

    python scripts/migrate_sqlite_to_postgres.py PATH/udara.sqlite \\
        --database-url postgresql://USER:PASS@HOST:PORT/DB   # atau env DATABASE_URL

Sifat:

* **Satu transaksi.** Seluruh tabel ditulis dalam satu transaksi; galat di
  tengah jalan tidak meninggalkan hasil separuh jadi di PostgreSQL.
* **Idempoten.** Dijalankan ulang (misalnya sesaat sebelum peralihan, ketika
  SQLite sudah bertambah) hanya menyisipkan baris baru dan memperbarui baris
  yang berubah. Sumber kebenarannya berkas SQLite.
* **Selalu UTC.** Sampai 28 September 2026 kolektor SQLite menafsirkan cap
  waktu halaman rinci sebagai UTC, padahal WIB (lihat spku/normalize.py,
  jebakan 1). Basis data yang sudah dikonversi ke UTC di tempat memuat tabel
  ``tz_correction`` dan disalin apa adanya. Basis data tanpa tabel itu
  (misalnya salinan cadangan sebelum konversi) dikoreksi saat disalin:
  observation.ts_utc, observation_ispu.ts_utc, meteo.ts_utc,
  observation_revision.ts_utc, dan station_status.newest_raw_utc digeser
  -7 jam. Cap waktu yang dibuat kolektor sendiri (first_seen_utc,
  fetched_utc, run, dan lainnya) selalu sudah UTC dan tidak pernah digeser.
* **Terverifikasi.** Setelah menulis, jumlah baris dan checksum nilai per
  tabel dibandingkan antara kedua basis data; kode keluar 1 bila berbeda.

Jalankan migrasi SEBELUM Data Worker berbasis PostgreSQL mulai menulis. Bila
PostgreSQL sudah memuat lintasan yang tidak ada di SQLite, skrip menolak
berjalan kecuali diberi --force, karena id pada tabel log dapat bertabrakan.
"""

from __future__ import annotations

import argparse
import datetime as dt
import os
import pathlib
import sqlite3
import sys

ROOT = pathlib.Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

import psycopg  # noqa: E402
from psycopg import sql  # noqa: E402

from spku.store import DEFAULT_SCHEMA, Store  # noqa: E402

TZ_SHIFT = dt.timedelta(hours=-7)

# Tipe kolom untuk konversi dari SQLite.
T, D, B, J = "timestamptz", "date", "bool", "jsonb"

#: (tabel, kolom -> tipe khusus, kunci konflik, kolom yang digeser -7 jam)
TABLES: list[tuple[str, dict[str, str | None], list[str], set[str]]] = [
    ("station", {
        "uuid": None, "kode": None, "initial": None, "name": None, "type": None,
        "lat": None, "lng": None, "kecamatan": None, "kota": None, "alamat": None,
        "dioperasikan": None, "first_seen_utc": T, "last_seen_utc": T, "active": B,
    }, ["uuid"], set()),
    ("station_metric", {
        "station_uuid": None, "metric": None, "unit_assumed": None,
        "first_seen_utc": T, "last_seen_utc": T,
    }, ["station_uuid", "metric"], set()),
    ("observation", {
        "station_uuid": None, "metric": None, "ts_utc": T, "value": None, "qc": None,
        "first_seen_utc": T,
    }, ["station_uuid", "metric", "ts_utc"], {"ts_utc"}),
    ("observation_ispu", {
        "station_uuid": None, "metric": None, "ts_utc": T, "ispu": None, "first_seen_utc": T,
    }, ["station_uuid", "metric", "ts_utc"], {"ts_utc"}),
    ("meteo", {
        "station_uuid": None, "ts_utc": T, "temp_c": None, "rh_pct": None, "wind_ms": None,
        "qc": None, "first_seen_utc": T,
    }, ["station_uuid", "ts_utc"], {"ts_utc"}),
    ("daily_ispu", {
        "station_uuid": None, "date_utc": D, "max_ispu": None, "concentration": None,
        "dominant_metric": None, "provisional": B, "updated_utc": T,
    }, ["station_uuid", "date_utc"], set()),
    ("station_status", {
        "station_uuid": None, "fetched_utc": T, "ispu_value": None, "ispu_category": None,
        "ispu_parameter": None, "ispu_concentration": None, "last_update_raw": None,
        "newest_raw_utc": T, "cadence_minutes": None, "n_observations": None,
    }, ["station_uuid", "fetched_utc"], {"newest_raw_utc"}),
    ("run", {
        "run_id": None, "kind": None, "started_utc": T, "finished_utc": T, "status": None,
        "stations_seen": None, "stations_ok": None, "rows_new": None, "rows_revised": None,
        "note": None,
    }, ["run_id"], set()),
    ("snapshot", {
        "fetched_utc": T, "update_time_wib": None, "n_stations": None, "payload_json": J,
    }, ["fetched_utc"], set()),
    # Tabel log berkunci identity: id dipertahankan, baris yang sudah ada dilewati.
    ("observation_revision", {
        "id": None, "series": None, "station_uuid": None, "metric": None, "ts_utc": T,
        "old_value": None, "new_value": None, "noticed_utc": T,
    }, ["id"], {"ts_utc"}),
    ("fetch_log", {
        "id": None, "run_id": None, "url": None, "fetched_utc": T, "http_status": None,
        "bytes": None, "sha256": None, "elapsed_ms": None, "parse_ok": B, "n_rows": None,
        "archive_path": None, "error": None,
    }, ["id"], set()),
    ("roster_event", {
        "id": None, "station_uuid": None, "label": None, "event": None, "at_utc": T,
    }, ["id"], set()),
]
IDENTITY_TABLES = {"observation_revision", "fetch_log", "roster_event"}


def _convert(value, kind: str | None, shift: bool):
    if value is None:
        return None
    if kind == T:
        moment = dt.datetime.fromisoformat(value)
        if moment.tzinfo is None:
            raise ValueError(f"cap waktu tanpa zona pada SQLite: {value!r}")
        return moment + TZ_SHIFT if shift else moment
    if kind == D:
        return dt.date.fromisoformat(value[:10])
    if kind == B:
        return bool(value)
    return value  # teks JSON diteruskan apa adanya ke kolom JSONB lewat COPY


def migrate_table(pg: psycopg.Connection, lite: sqlite3.Connection, table: str,
                  columns: dict, key: list[str], shifted: set[str]) -> int:
    cols = list(columns)
    temp = f"_mig_{table}"
    pg.execute(sql.SQL(
        "CREATE TEMP TABLE {} (LIKE {} INCLUDING DEFAULTS) ON COMMIT DROP"
    ).format(sql.Identifier(temp), sql.Identifier(table)))
    copy_stmt = sql.SQL("COPY {} ({}) FROM STDIN").format(
        sql.Identifier(temp), sql.SQL(", ").join(map(sql.Identifier, cols)))
    with pg.cursor().copy(copy_stmt) as copy:
        for row in lite.execute(f"SELECT {', '.join(cols)} FROM {table}"):
            copy.write_row([
                _convert(value, columns[col], col in shifted) for col, value in zip(cols, row)
            ])

    target = sql.Identifier(table)
    col_list = sql.SQL(", ").join(map(sql.Identifier, cols))
    conflict = sql.SQL(", ").join(map(sql.Identifier, key))
    if table in IDENTITY_TABLES:
        action = sql.SQL("DO NOTHING")
    else:
        update = [c for c in cols if c not in key and c != "first_seen_utc"]
        action = sql.SQL("DO UPDATE SET {} WHERE ({}) IS DISTINCT FROM ({})").format(
            sql.SQL(", ").join(
                sql.SQL("{} = excluded.{}").format(sql.Identifier(c), sql.Identifier(c))
                for c in update),
            sql.SQL(", ").join(sql.SQL("{}.{}").format(target, sql.Identifier(c)) for c in update),
            sql.SQL(", ").join(sql.SQL("excluded.{}").format(sql.Identifier(c)) for c in update),
        )
    cur = pg.execute(sql.SQL(
        "INSERT INTO {} ({}) SELECT {} FROM {} ON CONFLICT ({}) {}"
    ).format(target, col_list, col_list, sql.Identifier(temp), conflict, action))
    written = cur.rowcount

    if table in IDENTITY_TABLES:
        pg.execute(sql.SQL(
            "SELECT setval(pg_get_serial_sequence(%s, 'id'), "
            "GREATEST((SELECT COALESCE(MAX(id), 0) FROM {}), 1))"
        ).format(target), (table,))
    return written


def verify(pg: psycopg.Connection, lite: sqlite3.Connection, tz_fix: bool) -> bool:
    """Bandingkan jumlah baris dan checksum nilai; kembalikan True bila cocok."""
    checks = {
        "station": ("COUNT(*)", "SUM(lat + lng)"),
        "observation": ("COUNT(*)", "SUM(value)"),
        "observation_ispu": ("COUNT(*)", "SUM(ispu)"),
        "meteo": ("COUNT(*)", "SUM(COALESCE(temp_c,0) + COALESCE(rh_pct,0) + COALESCE(wind_ms,0))"),
        "daily_ispu": ("COUNT(*)", "SUM(max_ispu)"),
    }
    ok = True
    print(f"\n{'tabel':22s} {'SQLite':>10s} {'PostgreSQL':>12s}  checksum")
    for table, *_ in TABLES:
        count_expr, sum_expr = checks.get(table, ("COUNT(*)", None))
        n_lite = lite.execute(f"SELECT {count_expr} FROM {table}").fetchone()[0]
        n_pg = pg.execute(sql.SQL("SELECT COUNT(*) AS n FROM {}").format(sql.Identifier(table))).fetchone()["n"]
        line = f"{table:22s} {n_lite:>10d} {n_pg:>12d}"
        good = n_pg == n_lite
        if sum_expr:
            s_lite = lite.execute(f"SELECT {sum_expr} FROM {table}").fetchone()[0]
            s_pg = pg.execute(sql.SQL(f"SELECT ({sum_expr})::double precision AS s FROM {{}}").format(
                sql.Identifier(table))).fetchone()["s"]
            same = (s_lite is None and s_pg is None) or (
                s_lite is not None and s_pg is not None
                and abs(float(s_lite) - float(s_pg)) <= 1e-9 * max(1.0, abs(float(s_lite))))
            good = good and same
            line += f"  {'sama' if same else f'BEDA ({s_lite} vs {s_pg})'}"
        print(line + ("" if good else "   <-- TIDAK COCOK"))
        ok = ok and good

    # Rentang waktu observasi harus bergeser tepat sebesar koreksi.
    t_lite = lite.execute("SELECT MIN(ts_utc), MAX(ts_utc) FROM observation").fetchone()
    row = pg.execute("SELECT MIN(ts_utc) AS t0, MAX(ts_utc) AS t1 FROM observation").fetchone()
    t_pg = (row["t0"], row["t1"])
    if t_lite[0] is not None:
        expected = [dt.datetime.fromisoformat(v) + (TZ_SHIFT if tz_fix else dt.timedelta(0))
                    for v in t_lite]
        same = list(t_pg) == expected
        print(f"rentang observasi PG   {t_pg[0]:%Y-%m-%d %H:%M}Z .. {t_pg[1]:%Y-%m-%d %H:%M}Z"
              f"  {'sesuai' if same else 'TIDAK SESUAI'} dengan SQLite"
              f"{' - 7 jam' if tz_fix else ''}")
        ok = ok and same
    return ok


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("sqlite_path")
    parser.add_argument("--database-url", default=os.environ.get("DATABASE_URL"))
    parser.add_argument("--schema", default=os.environ.get("SPKU_DB_SCHEMA", DEFAULT_SCHEMA))
    parser.add_argument("--force", action="store_true",
                        help="tetap berjalan walau PostgreSQL sudah memuat lintasan baru")
    parser.add_argument("--verify-only", action="store_true")
    args = parser.parse_args(argv)
    if not args.database_url:
        parser.error("DSN PostgreSQL wajib: --database-url atau env DATABASE_URL")

    path = pathlib.Path(args.sqlite_path).resolve()
    # immutable=1: dibaca sebagai berkas beku. Pakai salinan cadangan (sqlite3
    # .backup) bila kolektor lama masih menulis ke berkas aslinya.
    lite = sqlite3.connect(f"file:{path}?mode=ro&immutable=1", uri=True)
    already_utc = bool(lite.execute(
        "SELECT COUNT(*) FROM sqlite_master WHERE name='tz_correction'").fetchone()[0])
    tz_fix = not already_utc
    with Store(args.database_url, args.schema) as store:
        pg = store.conn
        if not args.verify_only:
            runs_lite = {r[0] for r in lite.execute("SELECT run_id FROM run")}
            runs_pg = {r["run_id"] for r in pg.execute("SELECT run_id FROM run")}
            newer = runs_pg - runs_lite
            if newer and not args.force:
                print(f"PostgreSQL sudah memuat {len(newer)} lintasan yang tidak ada di SQLite "
                      f"(contoh: {sorted(newer)[-1]}). Data Worker berbasis PostgreSQL tampaknya "
                      "sudah berjalan. Hentikan, atau ulangi dengan --force.", file=sys.stderr)
                return 2
            print(f"sumber : {path}")
            print(f"tujuan : skema {args.schema!r}; koreksi zona waktu "
                  f"{'-7 jam (sumber belum dikonversi)' if tz_fix else 'tidak perlu (sumber sudah UTC)'}")
            with pg.transaction():
                pg.execute("SELECT pg_advisory_xact_lock(hashtext(%s))",
                           (f"spku-write:{args.schema}",))
                for table, columns, key, shifted in TABLES:
                    written = migrate_table(pg, lite, table, columns, key,
                                            shifted if tz_fix else set())
                    print(f"  {table:22s} {written:>8d} baris ditulis/diperbarui")
                pg.execute("""CREATE TABLE IF NOT EXISTS migration_log (
                                  id BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
                                  migrated_utc TIMESTAMPTZ NOT NULL DEFAULT now(),
                                  source TEXT NOT NULL,
                                  tz_shift_hours INTEGER NOT NULL,
                                  note TEXT)""")
                pg.execute(
                    "INSERT INTO migration_log (source, tz_shift_hours, note) VALUES (%s,%s,%s)",
                    (str(path), -7 if tz_fix else 0,
                     "label halaman rinci SPKU adalah WIB; dikoreksi ke UTC saat migrasi"
                     if tz_fix else "sumber sudah UTC (tabel tz_correction)"),
                )
        ok = verify(pg, lite, tz_fix)
    lite.close()
    print("\nverifikasi:", "LOLOS" if ok else "GAGAL")
    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
