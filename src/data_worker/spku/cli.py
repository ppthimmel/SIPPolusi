"""Antarmuka baris perintah kolektor."""

from __future__ import annotations

import argparse
import csv
import datetime as dt
import json
import logging
import sys

from .collect import run_full, run_snapshot
from .config import Config
from .monitor import format_health, health
from .store import Store


def _log(verbose: bool) -> None:
    logging.basicConfig(
        level=logging.DEBUG if verbose else logging.INFO,
        format="%(asctime)s %(levelname)-7s %(name)s: %(message)s",
        datefmt="%Y-%m-%dT%H:%M:%S%z",
        stream=sys.stderr,
    )


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="spku",
        description="Kolektor data kualitas udara portal Udara Jakarta.",
    )
    parser.add_argument("-c", "--config", help="berkas konfigurasi TOML")
    parser.add_argument("-v", "--verbose", action="store_true")
    sub = parser.add_subparsers(dest="command", required=True)

    p_collect = sub.add_parser("collect", help="lintasan penuh seluruh stasiun")
    p_collect.add_argument("--only", nargs="*", help="batasi pada UUID atau kode stasiun")

    sub.add_parser("snapshot", help="lintasan ringan satu permintaan ke beranda")
    sub.add_parser("health", help="laporan kesehatan pengumpulan")

    p_export = sub.add_parser("export", help="ekspor observasi ke CSV")
    p_export.add_argument("output")
    p_export.add_argument("--since", help="batas bawah waktu, format ISO (lihat catatan zona)")
    p_export.add_argument("--until", help="batas atas waktu, format ISO (eksklusif)")
    p_export.add_argument("--metric", help="batasi pada satu parameter")
    p_export.add_argument("--include-flagged", action="store_true",
                          help="sertakan baris bertanda kendali mutu")

    args = parser.parse_args(argv)
    _log(args.verbose)
    config = Config.load(args.config)

    if args.command == "collect":
        print(json.dumps(run_full(config, only=args.only), indent=2, ensure_ascii=False))
    elif args.command == "snapshot":
        print(json.dumps(run_snapshot(config), indent=2, ensure_ascii=False, default=str))
    elif args.command == "health":
        with Store(config.database, config.db_schema) as store:
            print(format_health(health(store, config.stale_alert_hours, config.stale_alert_ratio)))
    elif args.command == "export":
        _export(config, args)
    return 0


def _parse_bound(text: str, label: str) -> str:
    """Ubah masukan pengguna menjadi cap waktu UTC yang sebanding dengan basis data.

    Seluruh cap waktu di basis data ditulis berakhiran ``+00:00``. Membandingkan
    string mentah dari pengguna akan salah diam-diam: ``...Z`` dan ``...+07:00``
    keduanya mengurut sebelum ``...+00:00`` secara leksikografis, sehingga baris
    yang seharusnya masuk justru terbuang tanpa pesan galat.
    """
    raw = text.strip()
    candidate = raw[:-1] + "+00:00" if raw.endswith("Z") else raw
    try:
        moment = dt.datetime.fromisoformat(candidate)
    except ValueError:
        try:
            moment = dt.datetime.combine(
                dt.date.fromisoformat(candidate), dt.time.min, tzinfo=dt.timezone.utc
            )
        except ValueError:
            raise SystemExit(f"{label} tidak dapat dibaca: {text!r}") from None
    if moment.tzinfo is None:
        moment = moment.replace(tzinfo=dt.timezone.utc)
        print(
            f"{label} {text!r} ditafsirkan sebagai UTC; "
            "tambahkan '+07:00' bila yang dimaksud waktu Jakarta.",
            file=sys.stderr,
        )
    return moment.astimezone(dt.timezone.utc).isoformat(timespec="seconds")


def _export(config: Config, args) -> None:
    where = ["1=1"]
    params: list = []
    if args.since:
        where.append("o.ts_utc >= %s")
        params.append(_parse_bound(args.since, "--since"))
    if getattr(args, "until", None):
        where.append("o.ts_utc < %s")
        params.append(_parse_bound(args.until, "--until"))
    if args.metric:
        where.append("o.metric = %s")
        params.append(args.metric)
    if not args.include_flagged:
        where.append("o.qc = ''")
    query = f"""
        SELECT s.initial, s.name, s.type, s.lat, s.lng,
               o.metric, o.ts_utc, o.value, o.qc
        FROM observation o JOIN station s ON s.uuid = o.station_uuid
        WHERE {' AND '.join(where)}
        ORDER BY o.ts_utc, s.initial, o.metric
    """
    with Store(config.database, config.db_schema) as store:
        rows = store.conn.execute(query, params)
        with open(args.output, "w", newline="", encoding="utf-8") as handle:
            writer = csv.writer(handle)
            writer.writerow(
                ["initial", "name", "type", "lat", "lng", "metric", "ts_utc", "value", "qc"]
            )
            count = 0
            for row in rows:
                writer.writerow(list(row.values()))
                count += 1
    print(f"{count} baris ditulis ke {args.output}", file=sys.stderr)


if __name__ == "__main__":
    raise SystemExit(main())
