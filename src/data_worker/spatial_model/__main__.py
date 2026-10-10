"""Inferensi satu time window sampai EdgeWeight (TI-AI-05), dijalankan manual.

Dari ``src/data_worker``:

    python -m spatial_model infer --window-start 2026-10-06T08:00Z --out <dir> [--write-cache]

* Ground truth dan graf road network dibaca dari ``DATABASE_URL`` (sesi read-only).
* Dengan ``--write-cache``, EdgeWeight ditulis ke Spatial Pollution Cache DB pada
  ``CACHE_DATABASE_URL``, atau ``DATABASE_URL`` bila tidak diatur. Basis data
  tujuan harus memuat ``osm.road_edge`` versi graf yang sama, karena geometri
  ruas disalin di dalam SQL.
* ``--out`` menerima artefak penelusuran per run. DSN tidak pernah dicetak.
"""

from __future__ import annotations

import argparse
import dataclasses
import datetime as dt
import json
import logging
import os
import pathlib
import sys

from contracts import TimeWindow


def _window(text: str) -> TimeWindow:
    value = text.strip()
    if value.endswith("Z"):
        value = value[:-1] + "+00:00"
    moment = dt.datetime.fromisoformat(value)
    if moment.tzinfo is None:
        raise SystemExit("--window-start wajib menyertakan zona, misalnya 2026-10-06T08:00Z")
    return TimeWindow.starting_at(moment)


def _infer(args: argparse.Namespace) -> int:
    import psycopg

    from spatial_model import cache
    from spatial_model.inference import load_ground_truth_window, load_idw_settings, run_downscale_inference

    source_dsn = os.environ.get("DATABASE_URL")
    if not source_dsn:
        raise SystemExit("DATABASE_URL tidak diatur")
    window = _window(args.window_start)
    settings = load_idw_settings()
    with psycopg.connect(source_dsn, options="-c default_transaction_read_only=on") as src:
        ground_truth = load_ground_truth_window(src, window, settings["qc"], args.ground_truth_schema)
        graph_version = cache.read_active_graph_version(src)
        if graph_version is None:
            raise SystemExit("tidak ada graf road network berstatus active")
        edges = cache.read_road_edges(src, graph_version)
    out = pathlib.Path(args.out)
    target = None
    if args.write_cache:
        target = psycopg.connect(os.environ.get("CACHE_DATABASE_URL") or source_dsn, autocommit=True)
    try:
        summary = run_downscale_inference(window, ground_truth=ground_truth, edges=edges,
                                          graph_version=graph_version, conn=target, artifact_dir=out,
                                          settings=settings, force=args.force, pieces_cache_dir=out / "pieces")
    finally:
        if target is not None:
            target.close()
    print(json.dumps(dataclasses.asdict(summary), indent=2, ensure_ascii=False, default=str))
    return 0 if summary.status in ("complete", "skipped") else 1


def main(argv: list[str] | None = None) -> int:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)-7s %(name)s: %(message)s")
    parser = argparse.ArgumentParser(prog="python -m spatial_model", description=__doc__,
                                     formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = parser.add_subparsers(dest="command", required=True)
    p = sub.add_parser("infer", help="estimasi grid sampai EdgeWeight untuk satu time window")
    p.add_argument("--window-start", required=True)
    p.add_argument("--out", required=True, help="direktori artefak penelusuran")
    p.add_argument("--write-cache", action="store_true", help="tulis EdgeWeight ke Spatial Pollution Cache DB")
    p.add_argument("--force", action="store_true", help="tulis ulang walau time window sudah complete")
    p.add_argument("--ground-truth-schema", default="ground_truth")
    p.set_defaults(func=_infer)
    args = parser.parse_args(argv)
    return args.func(args)


if __name__ == "__main__":
    sys.exit(main())
