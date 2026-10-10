"""Verifikasi satuan konsentrasi ground truth SPKU terhadap ISPU yang dipublikasikan portal.

Portal tidak mencantumkan satuan konsentrasi. ISPU (Permen LHK 14/2020) dihitung
dari konsentrasi rata-rata 24 jam dengan batas dalam µg/m³. Bila ISPU yang
dihitung ulang dari ``observation`` dengan batas µg/m³ sama dengan
``observation_ispu`` portal, konsentrasi tersebut dalam µg/m³. Hipotesis ppb
untuk NO2 diuji dengan faktor 1,88 µg/m³ per ppb (25 °C, 1 atm).

    python scripts/check_ispu_units.py <folder ekspor ground_truth> [--out laporan.json]
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path

import numpy as np
import pandas as pd

ISPU = [0, 50, 100, 200, 300, 500]
#: Batas konsentrasi (µg/m³) per ISPU pada Lampiran Permen LHK P.14/2020.
BREAKPOINTS = {"PM25": [0, 15.5, 55.4, 150.4, 250.4, 500], "PM10": [0, 50, 150, 350, 420, 500],
               "NO2": [0, 80, 200, 1130, 2260, 3000], "SO2": [0, 52, 180, 400, 800, 1200],
               "CO": [0, 4000, 8000, 15000, 30000, 45000]}
HYPOTHESES = {"NO2": {"ppb": 1.88}}


def check(export_dir: Path, window_hours: int = 24, tolerance: float = 2.0) -> dict:
    obs = pd.read_parquet(export_dir / "observation.parquet")
    published = pd.read_parquet(export_dir / "observation_ispu.parquet")
    out = {}
    for metric, bp in BREAKPOINTS.items():
        o = obs[(obs.metric == metric) & (obs.qc == "") & (obs.value > 0)]
        hourly = (o.groupby(["station_uuid", o.ts_utc.dt.floor("h").rename("ts_utc")]).value.mean()
                  .rename("c").reset_index().sort_values(["station_uuid", "ts_utc"]))
        hourly["mean"] = hourly.groupby("station_uuid").c.transform(
            lambda s: s.rolling(window_hours, min_periods=window_hours * 3 // 4).mean())
        joined = hourly.merge(published[published.metric == metric][["station_uuid", "ts_utc", "ispu"]],
                              on=["station_uuid", "ts_utc"]).dropna(subset=["mean", "ispu"])
        result = {}
        for unit, factor in {"ugm3": 1.0, **HYPOTHESES.get(metric, {})}.items():
            err = np.abs(np.interp(joined["mean"] * factor, bp, ISPU) - joined["ispu"])
            result[unit] = dict(n=int(len(err)), median_abs_diff=round(float(err.median()), 2),
                                within_tolerance=round(float((err <= tolerance).mean()), 4))
        out[metric] = result
    return dict(window_hours=window_hours, tolerance_ispu=tolerance, results=out)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("export_dir", type=Path)
    parser.add_argument("--out", type=Path)
    args = parser.parse_args()
    report = check(args.export_dir)
    text = json.dumps(report, indent=2, ensure_ascii=False)
    if args.out:
        args.out.write_text(text + "\n", encoding="utf-8")
    print(text)


if __name__ == "__main__":
    main()
