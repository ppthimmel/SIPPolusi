"""CLI baseline IDW Spatial Downscaling Model (TI-AI-04).

Dijalankan dari ``src/data_worker``:

    # 1. Bekukan snapshot dataset dan split (sekali; lingkup TI-AI-03).
    #    DSN dibaca dari DATABASE_URL dan tidak pernah dicetak; sesi read-only.
    python -m spatial_model.baseline build-dataset --from-db --out ../../docs/experiments/TI-AI-04/dataset
    #    atau dari ekspor Parquet skema ground_truth:
    python -m spatial_model.baseline build-dataset --export-dir <dir> --out <dataset_dir>

    # 2. Jalankan baseline dari manifest yang sama, kapan pun.
    python -m spatial_model.baseline run --manifest ../../docs/experiments/TI-AI-04/dataset/manifest.json \\
        --out ../../docs/experiments/TI-AI-04/runs

Konfigurasi bawaan: ``spatial_model/baseline/configs/idw_baseline.toml``.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import logging
import os
import pathlib
import sys
import tomllib

from spatial_model.baseline.dataset import build_dataset, load_raw_from_export, load_raw_from_postgres
from spatial_model.baseline.run import code_version, run_baseline

DEFAULT_CONFIG = pathlib.Path(__file__).resolve().parent / "configs" / "idw_baseline.toml"


def _build(args: argparse.Namespace) -> int:
    config = tomllib.loads(pathlib.Path(args.config).read_text(encoding="utf-8"))
    end_utc = args.end_utc or config["dataset"].get("snapshot_end_utc")
    if args.split_from:
        # Pakai ulang batas split yang sudah dibekukan, mis. ketika snapshot diperbarui.
        previous = json.loads(pathlib.Path(args.split_from).read_text(encoding="utf-8"))
        bounds = previous["split"]["boundaries"]
        config["dataset"]["split"] |= {"train_end": bounds["train_end"], "val_end": bounds["val_end"]}
    if args.from_db:
        dsn = os.environ.get("DATABASE_URL")
        if not dsn:
            raise SystemExit("DATABASE_URL tidak diatur")
        stations, observations, source = load_raw_from_postgres(dsn, end_utc, args.schema)
    else:
        stations, observations, source = load_raw_from_export(pathlib.Path(args.export_dir), end_utc)
    source["config_sha256"] = hashlib.sha256(pathlib.Path(args.config).read_bytes()).hexdigest()
    manifest = build_dataset(config, stations, observations, source, pathlib.Path(args.out), code_version())
    print(json.dumps({k: manifest[k] for k in ("dataset_version", "split", "summary", "qc_dropped_rows")},
                     indent=2, ensure_ascii=False, default=str))
    return 0


def _run(args: argparse.Namespace) -> int:
    run_dir = run_baseline(pathlib.Path(args.config), pathlib.Path(args.manifest), pathlib.Path(args.out))
    print(run_dir)
    return 0


def main(argv: list[str] | None = None) -> int:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)-7s %(name)s: %(message)s")
    parser = argparse.ArgumentParser(prog="python -m spatial_model.baseline", description=__doc__,
                                     formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = parser.add_subparsers(dest="command", required=True)

    b = sub.add_parser("build-dataset", help="bentuk dan bekukan dataset stasiun-jam beserta manifest")
    src = b.add_mutually_exclusive_group(required=True)
    src.add_argument("--from-db", action="store_true", help="baca skema ground_truth lewat DATABASE_URL")
    src.add_argument("--export-dir", help="direktori ekspor Parquet skema ground_truth")
    b.add_argument("--schema", default="ground_truth")
    b.add_argument("--end-utc", help="batas atas eksklusif snapshot; bawaan dari konfigurasi")
    b.add_argument("--split-from", help="manifest lama yang batas split-nya dipakai ulang")
    b.add_argument("--config", default=str(DEFAULT_CONFIG))
    b.add_argument("--out", required=True)
    b.set_defaults(func=_build)

    r = sub.add_parser("run", help="jalankan evaluasi baseline dari manifest dataset")
    r.add_argument("--manifest", required=True)
    r.add_argument("--config", default=str(DEFAULT_CONFIG))
    r.add_argument("--out", required=True)
    r.set_defaults(func=_run)

    args = parser.parse_args(argv)
    return args.func(args)


if __name__ == "__main__":
    sys.exit(main())
