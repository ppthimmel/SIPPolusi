"""CLI FeatureMatrix (TI-AI-02). Dijalankan dari ``src/data_worker``:

    # data dictionary → feature_matrix/feature_spec.json
    python -m feature_matrix spec

    # FeatureMatrix untuk sel stasiun, mode operasional (fitur statis s.d. awal hari waktu inferensi)
    python -m feature_matrix build --start 2026-09-13T00:00Z --end 2026-09-30T23:00Z --cells stations --out <dir>

    # satu time window untuk seluruh grid
    python -m feature_matrix build --start 2026-09-28T04:00Z --cells all --out <dir>

Keluaran: features.parquet, audit.parquet, quality_report.{json,md}, manifest.json.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import pathlib
import sys

import pandas as pd

from feature_matrix.build import build_feature_matrix
from feature_matrix.quality import quality_markdown, quality_report
from feature_matrix.sources import DEFAULT_ROOT, Sources, sha256_file
from feature_matrix.spec import CANONICAL_GRID, SPEC_VERSION, write_feature_spec

SPEC_PATH = pathlib.Path(__file__).resolve().parent / "feature_spec.json"


def _content_sha256(df: pd.DataFrame) -> str:
    ordered = df.sort_values(["grid_id", "time_window_start"]).reset_index(drop=True)
    return hashlib.sha256(pd.util.hash_pandas_object(ordered, index=False).to_numpy().tobytes()).hexdigest()


def _cells(src: Sources, which: str):
    if which == "all":
        return None
    if which == "stations":
        st = src.stations()
        return st.loc[st["grid_id"] >= 0, "grid_id"].to_numpy()
    if which == "bbox":
        from contracts import JAKARTA_BBOX
        g = src.grid
        lo, la, hi, ha = JAKARTA_BBOX.as_tuple()
        return g.grid_id[(g.lon >= lo) & (g.lon <= hi) & (g.lat >= la) & (g.lat <= ha)]
    raise SystemExit(f"--cells tidak dikenal: {which}")


def _build(args) -> int:
    from spatial_model.baseline.run import code_version

    src = Sources(pathlib.Path(args.root))
    end = args.end or args.start
    windows = pd.date_range(pd.Timestamp(args.start.replace("Z", "+00:00")),
                            pd.Timestamp(end.replace("Z", "+00:00")), freq="h")
    stations = src.stations()
    stations = stations[stations["grid_id"] >= 0]
    fm, audit = build_feature_matrix(windows, _cells(src, args.cells), sources=src,
                                     static_cutoff=args.static_cutoff, sensors=stations,
                                     strict_static=not args.non_strict_static)
    out = pathlib.Path(args.out)
    out.mkdir(parents=True, exist_ok=True)
    fm.to_parquet(out / "features.parquet", index=False)
    audit.to_parquet(out / "audit.parquet", index=False)
    report = quality_report(fm, audit)
    (out / "quality_report.json").write_text(json.dumps(report, indent=2, ensure_ascii=False, default=str),
                                             encoding="utf-8")
    (out / "quality_report.md").write_text(quality_markdown(report, f"Laporan kualitas FeatureMatrix ({args.cells})"),
                                           encoding="utf-8")
    manifest = {
        "spec_version": SPEC_VERSION,
        "feature_spec_sha256": sha256_file(SPEC_PATH),
        "grid": CANONICAL_GRID.as_dict(),
        "arguments": {k: v for k, v in vars(args).items() if k != "func"},
        "rows": int(len(fm)),
        "content_sha256": _content_sha256(fm),
        "files": {name: sha256_file(out / name) for name in ("features.parquet", "audit.parquet")},
        "inputs": {k: src.inputs[k] for k in sorted(src.inputs)},
        "sensors": "stasiun SPKU pada grid kanonik (geos-cf/spku_stations_geoscf_mapping.csv)",
        "landsat_scenes": {str(c): src.xland_scenes(c) for c in sorted(set(audit["static_cutoff"]))},
        "quality_all_passed": report["all_passed"],
        "code": code_version(),
    }
    (out / "manifest.json").write_text(json.dumps(manifest, indent=2, ensure_ascii=False, default=str),
                                       encoding="utf-8")
    print(json.dumps({"rows": manifest["rows"], "content_sha256": manifest["content_sha256"][:16],
                      "quality_all_passed": report["all_passed"], "out": str(out)}, indent=2))
    return 0 if report["all_passed"] else 1


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="python -m feature_matrix", description=__doc__,
                                     formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = parser.add_subparsers(dest="command", required=True)
    s = sub.add_parser("spec", help="tulis feature_spec.json dari spec.py")
    s.add_argument("--out", default=str(SPEC_PATH))
    s.set_defaults(func=lambda a: (write_feature_spec(pathlib.Path(a.out)), print(a.out), 0)[-1])
    b = sub.add_parser("build", help="bangun FeatureMatrix dan laporan kualitas")
    b.add_argument("--start", required=True)
    b.add_argument("--end")
    b.add_argument("--cells", choices=("stations", "bbox", "all"), default="stations")
    b.add_argument("--static-cutoff")
    b.add_argument("--non-strict-static", action="store_true",
                   help="izinkan static_cutoff sesudah waktu inferensi (kebijakan snapshot)")
    b.add_argument("--root", default=str(DEFAULT_ROOT))
    b.add_argument("--out", required=True)
    b.set_defaults(func=_build)
    args = parser.parse_args(argv)
    return args.func(args)


if __name__ == "__main__":
    sys.exit(main())
