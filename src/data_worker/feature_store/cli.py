"""Perintah ``python main.py features ...`` (TI-SE-02).

    python main.py features status [--hours 48] [--json]      # pengambilan per sumber, ketersediaan, ukuran tabel
    python main.py features fetch [--window-start ...]       # hanya langkah konektor + QC, tanpa FeatureMatrix
    python main.py features seed [--dataset-processed DIR] [--since landsat=2026-01-01 ...]
    python main.py features rebuild --window-start 2026-10-27T04:00Z   # bangun ulang dan periksa feature_version

``status`` dan ``rebuild`` hanya membaca. ``fetch`` dan ``seed`` menulis ke skema
``features`` pada ``DATABASE_URL``.
"""

from __future__ import annotations

import argparse
import datetime as dt
import json
import os

import pandas as pd

from contracts import TimeWindow

from . import steps
from .seed import SEED_SOURCES, seed
from .store import FeatureStore, now_utc


def _window(text: str | None) -> TimeWindow:
    if not text:
        return TimeWindow.just_ended()
    moment = pd.Timestamp(text.replace("Z", "+00:00"))
    if moment.tzinfo is None:
        raise SystemExit("--window-start wajib menyertakan zona, misalnya 2026-10-27T04:00Z")
    return TimeWindow.starting_at(moment.to_pydatetime())


def status(store: FeatureStore, hours: int, as_json: bool) -> int:
    now = now_utc()
    log = store.fetch_log(now - dt.timedelta(hours=hours))
    summary = {}
    if not log.empty:
        log = log[~log.source.str.startswith("seed:")]
        for source, group in log.groupby("source"):
            last = group.iloc[-1]
            summary[source] = {
                "fetches": len(group), "status_counts": group.status.value_counts().to_dict(),
                "last_status": last.status, "last_started_utc": last.started_utc.isoformat(),
                "last_note": last.note if isinstance(last.note, str) else None,
                "products_new": int(group.products_new.sum()),
                "rows_written": int(group.rows_written.sum()), "retried_requests": int(group.retried_requests.sum()),
                "failed_requests": int(group.failed_requests.sum())}
    tau = pd.Timestamp(TimeWindow.just_ended(now).end)
    report = {"now_utc": now.isoformat(), "hours": hours, "sources": summary,
              "availability_at": tau.isoformat(), "availability": steps.source_availability(store, tau),
              "table_mb": {k: round(v / 1e6, 2) for k, v in store.table_sizes().items()}}
    if as_json:
        print(json.dumps(report, indent=2, ensure_ascii=False, default=str))
    else:
        print(f"Pengambilan sumber {hours} jam terakhir (sampai {now:%Y-%m-%d %H:%MZ}):")
        for source, info in summary.items():
            print(f"  {source:12s} {info['last_status']:11s} terakhir {info['last_started_utc'][:16]}Z; "
                  f"{info['fetches']} kali {info['status_counts']}; produk baru {info['products_new']}, "
                  f"diulang {info['retried_requests']}, gagal {info['failed_requests']}"
                  + (f"; {info['last_note']}" if info["last_note"] else ""))
        print(f"Ketersediaan pada τ = {tau:%Y-%m-%d %H:%MZ}:")
        for source, info in report["availability"].items():
            age = "-" if info["age_hours"] is None else f"{info['age_hours']:.1f} jam"
            print(f"  {source:12s} {'tersedia' if info['available'] else 'TIDAK TERSEDIA':15s} terbaru "
                  f"{info['latest'] or '-'} (umur {age})")
        print("Ukuran tabel (MB):", report["table_mb"])
    bad = [s for s, v in summary.items() if v["last_status"] == "failed"]
    return 1 if bad else 0


def main(argv: list[str]) -> int:
    parser = argparse.ArgumentParser(prog="python main.py features", description=__doc__.splitlines()[0])
    sub = parser.add_subparsers(dest="command", required=True)
    p = sub.add_parser("status", help="pengambilan per sumber, ketersediaan pada τ terakhir, ukuran tabel")
    p.add_argument("--hours", type=int, default=48)
    p.add_argument("--json", action="store_true")
    p = sub.add_parser("fetch", help="jalankan konektor dan kendali mutu untuk satu time window")
    p.add_argument("--window-start")
    p = sub.add_parser("seed", help="muat arsip dataset_processed ke skema features")
    p.add_argument("--dataset-processed")
    p.add_argument("--sources", nargs="+", choices=SEED_SOURCES, default=list(SEED_SOURCES))
    p.add_argument("--since", nargs="*", default=[], metavar="SUMBER=TANGGAL",
                   help="awal data per sumber; bawaan batas retensi")
    p = sub.add_parser("rebuild", help="bangun ulang FeatureMatrix satu time window dan periksa feature_version")
    p.add_argument("--window-start", required=True)
    p.add_argument("--out", help="tulis Parquet hasil bangun ulang ke berkas ini")
    args = parser.parse_args(argv)

    dsn = os.environ.get("DATABASE_URL")
    if not dsn:
        print("DATABASE_URL belum diatur")
        return 2
    if args.command == "fetch":
        from acquisition import schedule_acquisition

        window = _window(args.window_start)
        chosen = [("fetch_satellite_archive", steps.fetch_satellite_archive),
                  ("fetch_tropospheric_column", steps.fetch_tropospheric_column),
                  ("fetch_meteo", steps.fetch_meteo), ("run_quality_control", steps.run_quality_control)]
        report = schedule_acquisition(window, steps=chosen)
        print(json.dumps(report, indent=2, ensure_ascii=False, default=str))
        return 1 if report["failed"] else 0
    with FeatureStore.connect(dsn) as store:
        store.ensure_schema()
        if args.command == "status":
            return status(store, args.hours, args.json)
        if args.command == "seed":
            from feature_matrix.sources import DATASET_PROCESSED

            since = dict(item.split("=", 1) for item in args.since)
            result = seed(store, args.dataset_processed or DATASET_PROCESSED, sources=args.sources, since=since)
            print(json.dumps({"seeded": result, "table_mb": {k: round(v / 1e6, 2)
                                                              for k, v in store.table_sizes().items()}}, indent=2))
            return 0
        window = _window(args.window_start)
        matrix, manifest = steps.read_features(store, window)
        if args.out:
            matrix.to_parquet(args.out, index=False, compression="zstd")
        print(json.dumps({"time_window": str(window), "feature_version": manifest["feature_version"],
                          "verified": True, "built_utc": manifest["built_utc"], "cells": len(matrix),
                          "columns": matrix.shape[1]}, indent=2, default=str))
        return 0
