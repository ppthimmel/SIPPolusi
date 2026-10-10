"""Laporan pemeriksaan FeatureMatrix: skema, cakupan, duplikasi, nilai hilang, kebocoran waktu."""

from __future__ import annotations

import json
from pathlib import Path

import numpy as np
import pandas as pd

from .prepare_stgnn import AVAILABILITY_FLAGS, FLAGGED_VALUES, TARGETS, stgnn_hash
from .spec import LABEL_COLUMNS, feature_columns

SATELLITES = ["ndvi", "ndbi", "lst_c", "no2_mol_m2", "ntl"]
AGES = {"Landsat NDVI": "ndvi_age_hours", "Sentinel-5P NO2": "no2_mol_m2_age_hours", "VIIRS NTL": "ntl_age_hours",
        "GEOS-CF PM2.5": "geoscf_pm25_age_hours", "GEOS-CF NO2": "geoscf_no2_age_hours"}


def _num(n) -> str:
    return f"{n:,}".replace(",", ".")


def _check(checks, name, ok, detail):
    checks.append(dict(check=name, status="lolos" if ok else "gagal", detail=detail))


def _schema_ok(frame, spec):
    names = [c["name"] for c in spec]
    wrong = [c["name"] for c in spec if c["name"] in frame and not str(frame[c["name"]].dtype).startswith(
        {"timestamp[ns, UTC]": "datetime64[ns, UTC]"}.get(c["dtype"], c["dtype"]))]
    return list(frame.columns) == names and not wrong, wrong


def quality_report(directory: Path | str) -> dict:
    directory = Path(directory)
    manifest = json.loads((directory / "manifest.json").read_text(encoding="utf-8"))
    features = pd.read_parquet(directory / "features.parquet")
    labels = pd.read_parquet(directory / "labels.parquet")
    nodes = pd.read_parquet(directory / "nodes.parquet")
    checks: list[dict] = []

    hashes = {name: stgnn_hash(directory / name) for name in manifest.get("outputs", {})}
    _check(checks, "manifest lengkap dan hash keluaran cocok",
           manifest["status"] == "complete" and hashes == manifest["outputs"], f"status {manifest['status']}")

    ok, wrong = _schema_ok(features, feature_columns())
    _check(checks, "kolom dan tipe fitur sesuai data dictionary", ok,
           f"{features.shape[1]} kolom" + (f"; tipe berbeda: {wrong}" if wrong else ""))
    ok, wrong = _schema_ok(labels, LABEL_COLUMNS)
    _check(checks, "kolom dan tipe label sesuai data dictionary", ok,
           f"{labels.shape[1]} kolom" + (f"; tipe berbeda: {wrong}" if wrong else ""))

    hours = pd.date_range(manifest["start_utc"], manifest["end_utc_exclusive"], freq="h", inclusive="left")
    expected = len(nodes) * len(hours)
    complete = (len(features) == expected and set(features.grid_id) == set(nodes.grid_id)
                and features.groupby("time_utc").size().reindex(hours).eq(len(nodes)).all())
    _check(checks, "cakupan sel × jam lengkap", complete,
           f"{_num(len(nodes))} sel × {_num(len(hours))} jam = {_num(expected)}; tersedia {_num(len(features))} baris")
    dup_f = int(features.duplicated(["grid_id", "time_utc"]).sum())
    dup_l = int(labels.duplicated(["station_uuid", "time_utc"]).sum())
    _check(checks, "tanpa kunci ganda", dup_f == 0 and dup_l == 0,
           f"fitur (grid_id, time_utc): {dup_f}; label (station_uuid, time_utc): {dup_l}")

    flag_bad = {f: int((features[f] != np.isfinite(features[v].to_numpy(dtype="float64"))).sum())
                for v, f in zip(FLAGGED_VALUES, AVAILABILITY_FLAGS)}
    _check(checks, "penanda ketersediaan konsisten dengan NaN", not any(flag_bad.values()),
           f"{sum(flag_bad.values())} pelanggaran pada {len(AVAILABILITY_FLAGS)} penanda")

    leaks = {}
    for s in SATELLITES:
        valid = features[s].notna()
        leaks[s] = int((features.loc[valid, [f"{s}_observed_at", f"{s}_produced_at"]]
                        .gt(features.loc[valid, "time_utc"], axis=0).any(axis=1)
                        | features.loc[valid, [f"{s}_observed_at", f"{s}_produced_at"]].isna().any(axis=1)).sum())
    for var in ("pm25", "no2"):
        valid = features[f"geoscf_{var}_ugm3"].notna()
        cols = [f"geoscf_{var}_time_window_end", f"geoscf_{var}_available_at_utc"]
        leaks[f"geoscf_{var}"] = int((features.loc[valid, cols].gt(features.loc[valid, "time_utc"], axis=0)
                                      .any(axis=1) | features.loc[valid, cols].isna().any(axis=1)).sum())
    w = features.weather_time_utc.notna()
    leaks["weather"] = int((features.loc[w, "weather_time_utc"] != features.loc[w, "time_utc"]).sum())
    leaks["target_in_features"] = len(set(TARGETS) & set(features.columns))
    _check(checks, "tanpa kebocoran waktu (sumber tersedia <= time_utc, tanpa label di fitur)",
           not any(leaks.values()), f"{sum(leaks.values())} pelanggaran")

    keys = pd.MultiIndex.from_frame(labels[["grid_id", "time_utc"]])
    have = pd.MultiIndex.from_frame(features[["grid_id", "time_utc"]])
    missing_keys = int((~keys.isin(have)).sum())
    _check(checks, "setiap label memiliki baris fitur", missing_keys == 0, f"{missing_keys} label tanpa fitur")

    missing = {v: round(float(features[v].isna().mean()), 6) for v in FLAGGED_VALUES}
    per_hour = features.groupby("time_utc")[AVAILABILITY_FLAGS].mean()
    labelled = features.grid_id.isin(set(labels.grid_id))
    report = dict(
        directory=str(directory), feature_rows=len(features), feature_columns=features.shape[1],
        nodes=len(nodes), label_nodes=int(labels.grid_id.nunique()), hours=len(hours),
        start_utc=manifest["start_utc"], end_utc_exclusive=manifest["end_utc_exclusive"],
        checks=checks, missing_fraction=missing,
        availability_fraction={f: round(float(features[f].mean()), 6) for f in AVAILABILITY_FLAGS},
        availability_per_hour_min={f: round(float(per_hour[f].min()), 6) for f in AVAILABILITY_FLAGS},
        temporal_leakage=leaks,
        age_hours={k: dict(min=float(features[c].min()), median=float(features[c].median()),
                           max=float(features[c].max())) for k, c in AGES.items()},
        sensor_distance_m=dict(
            labelled_nodes=dict(max=float(features.loc[labelled, "dist_nearest_sensor_m"].max())),
            context_nodes=dict(min=float(features.loc[~labelled, "dist_nearest_sensor_m"].min()),
                               max=float(features.loc[~labelled, "dist_nearest_sensor_m"].max()))),
        labels=dict(rows=len(labels), stations=int(labels.station_uuid.nunique()),
                    finite={t: int(labels[t].notna().sum()) for t in TARGETS},
                    splits={s: dict(rows=int(len(g)), stations=int(g.station_uuid.nunique()),
                                    first=str(g.time_utc.min()), last=str(g.time_utc.max()))
                            for s, g in sorted(labels.groupby("split"),
                                               key=lambda x: ["train", "validation", "test"].index(x[0]))}),
    )
    report["passed"] = all(c["status"] == "lolos" for c in checks)
    return report


def report_markdown(report: dict) -> str:
    pct = lambda x: f"{100 * x:.1f}%".replace(".", ",")  # noqa: E731
    num = _num
    dec = lambda x: f"{x:.1f}".replace(".", ",")  # noqa: E731
    lab = report["labels"]
    lines = [
        "# Laporan kualitas FeatureMatrix ST-GNN", "",
        f"{num(report['feature_rows'])} baris × {report['feature_columns']} kolom: {num(report['nodes'])} sel "
        f"({report['label_nodes']} berlabel) × {report['hours']} jam, {report['start_utc']} s.d. "
        f"{report['end_utc_exclusive']} (eksklusif).", "",
        "| Pemeriksaan | Status | Rincian |", "|---|---|---|",
        *[f"| {c['check']} | {c['status']} | {c['detail']} |" for c in report["checks"]], "",
        "## Ketersediaan per fitur", "",
        "| Penanda | Tersedia | Minimum per jam |", "|---|---|---|",
        *[f"| `{f}` | {pct(v)} | {pct(report['availability_per_hour_min'][f])} |"
          for f, v in report["availability_fraction"].items()], "",
        "## Umur data (jam)", "", "| Sumber | Min | Median | Maks |", "|---|---|---|---|",
        *[f"| {k} | {dec(v['min'])} | {dec(v['median'])} | {dec(v['max'])} |" for k, v in report["age_hours"].items()],
        "",
        "## Label", "",
        f"{num(lab['rows'])} baris stasiun-jam dari {lab['stations']} stasiun; PM2.5 finite "
        f"{num(lab['finite']['target_pm25'])}, NO2 finite {num(lab['finite']['target_no2'])}.", "",
        "| Split | Baris | Stasiun | Awal | Akhir |", "|---|---|---|---|---|",
        *[f"| {s} | {num(v['rows'])} | {v['stations']} | {v['first']} | {v['last']} |"
          for s, v in lab["splits"].items()], "",
    ]
    return "\n".join(lines)


def write_report(directory: Path | str) -> dict:
    directory = Path(directory)
    report = quality_report(directory)
    (directory / "quality_report.json").write_text(json.dumps(report, indent=2, ensure_ascii=False) + "\n",
                                                   encoding="utf-8")
    (directory / "quality_report.md").write_text(report_markdown(report), encoding="utf-8")
    return report
