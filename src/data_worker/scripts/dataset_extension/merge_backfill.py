"""Merge backfilled feature files into a copy of dataset_processed.

Usage: python merge_features.py ORIGINAL_DATASET_PROCESSED BACKFILL_DIR OUT_DIR
Original rows win wherever both have the same key; the overlap is compared and reported.
"""
import json
import shutil
import sys
from pathlib import Path

import numpy as np
import pandas as pd

KEYS = {
    "geos-cf/geoscf_cell_hourly.parquet": ["time_window_start", "cell_id"],
    "geos-cf/geoscf_fcst_cell_hourly.parquet": ["init_utc", "time_window_start", "cell_id"],
    "open_meteo/hourly.parquet": ["time_utc"],
    "sentinel5p/observations.parquet": ["scene_id", "grid_id"],
}
COMPARE = {
    "geos-cf/geoscf_cell_hourly.parquet": ["pm25_ugm3", "no2_ugm3", "available_at_utc"],
    "geos-cf/geoscf_fcst_cell_hourly.parquet": ["pm25_ugm3", "no2_ugm3", "available_at_utc"],
    "open_meteo/hourly.parquet": ["temperature_2m", "precipitation", "boundary_layer_height"],
    "sentinel5p/observations.parquet": ["no2_mol_m2", "available", "produced_at"],
}


def compare(old, new, keys, cols):
    m = old.merge(new, on=keys, suffixes=("_old", "_new"))
    out = {"overlap_rows": len(m)}
    for c in cols:
        a, b = m[f"{c}_old"], m[f"{c}_new"]
        if pd.api.types.is_numeric_dtype(a) and not pd.api.types.is_bool_dtype(a):
            d = (a.astype(float) - b.astype(float)).abs()
            out[c] = {"max_abs_diff": float(np.nanmax(d)) if len(d) else 0.0,
                      "mismatched_nan": int((a.isna() != b.isna()).sum())}
        else:
            out[c] = {"unequal": int((a.astype(str) != b.astype(str)).sum())}
    return out


def main(orig: Path, back: Path, out: Path) -> None:
    if out.exists():
        raise SystemExit(f"{out} exists")
    shutil.copytree(orig, out, ignore=shutil.ignore_patterns("OSM"))
    report = {"original": str(orig), "backfill": str(back), "files": {}}
    for rel, keys in KEYS.items():
        old = pd.read_parquet(orig / rel)
        new = pd.read_parquet(back / rel)[old.columns]
        for c in old.columns:  # identical dtypes, including categoricals
            if str(new[c].dtype) != str(old[c].dtype):
                new[c] = new[c].astype(old[c].dtype)
        info = compare(old, new, keys, COMPARE[rel])
        added = new.merge(old[keys], on=keys, how="left", indicator=True)
        added = added[added._merge == "left_only"].drop(columns="_merge")
        merged = pd.concat([old, added[old.columns]], ignore_index=True).sort_values(keys).reset_index(drop=True)
        for c in old.columns:
            if str(merged[c].dtype) != str(old[c].dtype):
                merged[c] = merged[c].astype(old[c].dtype)
        merged.to_parquet(out / rel, index=False)
        info.update(rows_original=len(old), rows_added=len(added), rows_total=len(merged))
        report["files"][rel] = info
        print(rel, json.dumps(info, default=str))
    # Landsat: add scenes that are not in the original folder.
    have = {p.stem for p in (orig / "landsat/scenes").glob("*.parquet")}
    added = []
    for p in sorted((back / "landsat/scenes").glob("*.parquet")):
        if p.stem not in have:
            shutil.copyfile(p, out / "landsat/scenes" / p.name)
            added.append(p.stem)
    times = pd.read_parquet(orig / "landsat/scene_times.parquet")
    new_times = pd.read_parquet(back / "landsat/scene_times.parquet")
    new_times = new_times[new_times.scene_id.isin(added)][times.columns]
    pd.concat([times, new_times], ignore_index=True).sort_values("observed_at").reset_index(drop=True) \
        .to_parquet(out / "landsat/scene_times.parquet", index=False)
    report["files"]["landsat/scenes"] = {"scenes_original": len(have), "scenes_added": added}
    print("landsat added", added)
    reports = {p.stem: json.loads(p.read_text()) for p in sorted((back / "reports").glob("*.json"))}
    for r in reports.values():
        r.pop("trace", None)
    report["backfill_reports"] = reports
    report["not_backfilled"] = {"viirs": "VNP46A2 needs EARTHDATA_TOKEN; the as-of reader carries the last "
                                         "September product forward with its age in ntl_age_hours"}
    (out / "BACKFILL_MANIFEST.json").write_text(json.dumps(report, indent=2, default=str) + "\n", encoding="utf-8")


if __name__ == "__main__":
    main(Path(sys.argv[1]), Path(sys.argv[2]), Path(sys.argv[3]))
