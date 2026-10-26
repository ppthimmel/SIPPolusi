"""CLI snapshot dataset (TI-AI-03).

    python -m spatial_model.snapshot build --ground-truth <ekspor> --out <folder baru> [--config ...]
    python -m spatial_model.snapshot verify <folder snapshot>
"""

from __future__ import annotations

import argparse
import json
import sys

from .build import DEFAULT_CONFIG, build_snapshot, verify_checksums


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(prog="python -m spatial_model.snapshot", description=__doc__,
                                     formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = parser.add_subparsers(dest="command", required=True)
    b = sub.add_parser("build", help="bangun snapshot ke folder baru (tidak menimpa)")
    b.add_argument("--ground-truth", required=True, help="folder ekspor Parquet ground_truth")
    b.add_argument("--out", required=True)
    b.add_argument("--config", default=str(DEFAULT_CONFIG))
    b.add_argument("--dataset-processed", default=None)
    v = sub.add_parser("verify", help="periksa berkas terhadap CHECKSUMS.sha256")
    v.add_argument("directory")
    args = parser.parse_args(argv)

    if args.command == "build":
        manifest = build_snapshot(args.ground_truth, args.out, args.config, args.dataset_processed)
        print(json.dumps(dict(version=manifest["version"], counts=manifest["counts"], leakage=manifest["leakage"]),
                         indent=2, ensure_ascii=False))
        return 0 if manifest["leakage"]["passed"] else 1
    bad = verify_checksums(args.directory)
    print("utuh" if not bad else "berubah/hilang: " + ", ".join(bad))
    return 0 if not bad else 1


if __name__ == "__main__":
    sys.exit(main())
