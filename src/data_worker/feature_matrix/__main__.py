"""CLI FeatureMatrix ST-GNN (TI-AI-02).

    python -m feature_matrix spec
    python -m feature_matrix export --ground-truth <folder ekspor> --out <folder>
    python -m feature_matrix quality <folder>
"""

from __future__ import annotations

import argparse
import json
import subprocess
import sys
from pathlib import Path

from .grid import active_cells
from .labels import INPUT_FILES, station_hour_targets
from .prepare_stgnn import build_grid_graph, export_stgnn_inputs, prepare_stgnn_labels, stgnn_hash
from .quality import write_report
from .sources import DATASET_PROCESSED, VIIRS_RULES, WEATHER_DELAY_HOURS, SourceReader
from .spec import spec_document

PACKAGE = Path(__file__).resolve().parent
SPEC_PATH = PACKAGE / "feature_spec.json"

ASSUMPTIONS = dict(
    weather_availability="Open-Meteo Historical Forecast menyambung jam-jam pertama setiap run; waktu terbit tidak "
                         "tercatat. Asumsi: nilai valid time t tersedia pada awal run 6 jam yang memuat t + "
                         "weather_delay_hours; dipakai valid time terbaru yang tersedia pada time_utc.",
    geoscf_availability="Server Last-Modified is the assumed availability proxy; first publication and product "
                        "production times are not verified.",
    satellite_availability="produced_at used as availability proxy",
    target_units="ug/m3; portal tidak menyatakan satuan, terverifikasi terhadap ISPU portal "
                 "(scripts/check_ispu_units.py)",
    target_policy="Mean valid readings in [hour start, hour end), target timestamp at hour end; existing source "
                  "QC and exclusions preserved",
)


def _git_commit() -> dict:
    def run(*args):
        return subprocess.run(["git", *args], cwd=PACKAGE, capture_output=True, text=True).stdout.strip()
    return dict(commit=run("rev-parse", "HEAD"), dirty=bool(run("status", "--porcelain", "--", str(PACKAGE))))


def export(args) -> dict:
    delay = None if args.weather_delay_hours < 0 else args.weather_delay_hours
    reader = SourceReader(args.dataset_processed, viirs_cell_rule=args.viirs_cell_rule, weather_delay_hours=delay)
    targets = station_hour_targets(args.ground_truth, args.start, args.end)
    nodes, edges = build_grid_graph(active_cells(), targets.grid_id.unique().tolist(), args.context_hops)
    labels = prepare_stgnn_labels(targets, nodes, args.train_end, args.validation_end)
    gt = Path(args.ground_truth)
    sources = {f"dataset_processed/{p.relative_to(reader.root).as_posix()}": stgnn_hash(p)
               for p in reader.input_files()}
    sources |= {f"ground_truth/{gt.name}/{name}": stgnn_hash(gt / name) for name in INPUT_FILES}
    provenance = dict(
        sources_sha256=sources, **ASSUMPTIONS, context_hops=args.context_hops,
        viirs_cell_rule=args.viirs_cell_rule, weather_delay_hours=delay,
        weather_run_interval_hours=reader.weather_run_interval_hours,
        arguments=dict(start=args.start, end=args.end, train_end=args.train_end,
                       validation_end=args.validation_end, window=args.window),
        code=_git_commit(), basis="bundle ST-GNN jakarta_processed_bundle_20261007 (FedrianzD)")
    manifest = export_stgnn_inputs(nodes, edges, labels, reader, args.out, window=args.window,
                                   provenance=provenance)
    report = write_report(args.out)
    print(f"Selesai: {manifest['feature_rows']:,} baris fitur, {manifest['labels']:,} label; "
          f"pemeriksaan {'lolos' if report['passed'] else 'GAGAL'}")
    return report


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(prog="python -m feature_matrix", description=__doc__,
                                     formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = parser.add_subparsers(dest="command", required=True)
    sub.add_parser("spec", help="tulis data dictionary ke feature_matrix/feature_spec.json")
    e = sub.add_parser("export", help="label, graf dan fitur grid-jam ST-GNN")
    e.add_argument("--ground-truth", required=True, help="folder ekspor Parquet ground_truth")
    e.add_argument("--out", required=True)
    e.add_argument("--dataset-processed", default=str(DATASET_PROCESSED))
    e.add_argument("--start", default="2026-09-13T01:00Z", help="label pertama (time_utc, inklusif)")
    e.add_argument("--end", default="2026-10-01T00:00Z", help="batas label (time_utc, eksklusif)")
    e.add_argument("--train-end", default="2026-09-25T00:00Z")
    e.add_argument("--validation-end", default="2026-09-28T00:00Z")
    e.add_argument("--window", type=int, default=24)
    e.add_argument("--context-hops", type=int, default=1)
    e.add_argument("--viirs-cell-rule", choices=VIIRS_RULES, default="tile")
    e.add_argument("--weather-delay-hours", type=float, default=WEATHER_DELAY_HOURS,
                   help="jeda terbit run Open-Meteo yang diasumsikan; negatif = nilai valid time τ (aturan bundle)")
    q = sub.add_parser("quality", help="tulis ulang quality_report.{json,md}")
    q.add_argument("directory")
    args = parser.parse_args(argv)

    if args.command == "spec":
        SPEC_PATH.write_text(json.dumps(spec_document(), indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
        print(SPEC_PATH)
        return 0
    report = export(args) if args.command == "export" else write_report(args.directory)
    return 0 if report["passed"] else 1


if __name__ == "__main__":
    sys.exit(main())
