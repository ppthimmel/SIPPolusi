"""Pemeriksaan kualitas FeatureMatrix sebelum dataset dibekukan (TI-AI-02).

Melaporkan cakupan waktu/ruang, duplikasi, proporsi nilai hilang, kesesuaian
skema dengan data dictionary, konsistensi nilai kosong terhadap penanda
ketersediaan, dan kebocoran waktu (data dipakai sebelum tersedia).
"""

from __future__ import annotations

import numpy as np
import pandas as pd

from feature_matrix.spec import FEATURE_NAMES, FEATURES, FEATURES_BY_NAME

#: Kolom nilai per sumber dan penanda ketersediaannya.
SOURCE_COLUMNS = {
    "geoscf_available": ["geoscf_pm25_ugm3", "geoscf_no2_ugm3"],
    "s5p_available": ["s5p_no2_umol_m2"],
    "met_available": ["met_temperature_2m_c", "met_relative_humidity_2m_pct", "met_wind_speed_10m_ms",
                      "met_boundary_layer_height_m", "met_precipitation_mm", "met_surface_pressure_hpa"],
    "xroad_available": ["road_density_km_km2", "dist_arterial_m"],
    "land_available": ["land_ndvi", "land_ndbi", "land_lst_c"],
    "act_available": ["act_ntl_nw_cm2_sr"],
    "sensor_available": ["dist_nearest_sensor_m"],
}


def _check(name: str, passed: bool, detail) -> dict:
    return {"check": name, "status": "lolos" if passed else "gagal", "detail": detail}


def quality_report(fm: pd.DataFrame, audit: pd.DataFrame) -> dict:
    checks = []
    # Skema
    missing_cols = [c for c in FEATURE_NAMES if c not in fm.columns]
    extra_cols = [c for c in fm.columns if c not in FEATURES_BY_NAME]
    dtype_mismatch = {c: str(fm[c].dtype) for c in FEATURE_NAMES if c in fm.columns
                      and not str(fm[c].dtype).startswith(FEATURES_BY_NAME[c].dtype.split("[")[0])}
    checks.append(_check("kolom sesuai data dictionary", not missing_cols and not extra_cols,
                         {"hilang": missing_cols, "tidak terdaftar": extra_cols}))
    checks.append(_check("tipe data sesuai data dictionary", not dtype_mismatch, dtype_mismatch))

    # Ketertelusuran dan duplikasi
    windows = fm["time_window_start"]
    dup = int(fm.duplicated(["grid_id", "time_window_start"]).sum())
    checks.append(_check("tidak ada kunci (grid_id, time_window_start) ganda", dup == 0, {"duplikat": dup}))
    not_hour = int((windows != windows.dt.floor("h")).sum())
    checks.append(_check("time_window_start tepat di awal jam UTC", not_hour == 0, {"pelanggaran": not_hour}))
    n_cells, n_windows = fm["grid_id"].nunique(), windows.nunique()
    complete = len(fm) == n_cells * n_windows
    checks.append(_check("setiap sel memiliki setiap time window", complete,
                         {"baris": int(len(fm)), "sel × time window": int(n_cells * n_windows)}))

    # Missingness eksplisit: nilai kosong ⇔ penanda false
    consistency = {}
    for flag, cols in SOURCE_COLUMNS.items():
        any_value = fm[cols].notna().any(axis=1)
        consistency[flag] = {
            "tersedia_tanpa_nilai": int((fm[flag] & ~any_value).sum()),
            "tidak_tersedia_tetapi_bernilai": int((~fm[flag] & any_value).sum()),
        }
    bad = sum(v["tersedia_tanpa_nilai"] + v["tidak_tersedia_tetapi_bernilai"] for v in consistency.values())
    checks.append(_check("nilai kosong konsisten dengan penanda ketersediaan", bad == 0, consistency))

    # Kebocoran waktu
    tau = audit["inference_time"]
    geos_late = int((audit["geoscf_available_at"].notna() & (audit["geoscf_available_at"] > tau)).sum())
    s5p_late = int((audit["s5p_max_produced_at"].notna() & (audit["s5p_max_produced_at"] > tau)).sum())
    met_off = int((audit["met_time"].notna() & (audit["met_time"] != audit["time_window_start"])).sum())
    future_valid = int((audit["geoscf_valid_time"].notna()
                        & (audit["geoscf_valid_time"] > audit["time_window_start"])).sum())
    checks.append(_check("GEOS-CF: available_at_utc <= waktu inferensi", geos_late == 0, {"pelanggaran": geos_late}))
    checks.append(_check("GEOS-CF: valid time <= time window", future_valid == 0, {"pelanggaran": future_valid}))
    checks.append(_check("Sentinel-5P: produced_at <= waktu inferensi", s5p_late == 0, {"pelanggaran": s5p_late}))
    checks.append(_check("Open-Meteo: jam sama dengan time window", met_off == 0, {"pelanggaran": met_off}))
    after = int(audit["static_after_inference"].sum())
    checks.append({"check": "fitur statis dari batas waktu sesudah waktu inferensi",
                   "status": "dicatat" if after else "lolos",
                   "detail": {"time_window": after,
                              "catatan": "hanya diizinkan dengan strict_static=False (kebijakan snapshot TI-AI-03)"}})

    value_cols = [f.name for f in FEATURES if f.group not in ("index", "availability")]
    missing = fm[value_cols].isna().mean().round(4).to_dict()
    in_bbox = fm[fm["in_study_bbox"]]
    availability = {flag: {"seluruh": round(float(fm[flag].mean()), 4),
                           "di_bbox_studi": round(float(in_bbox[flag].mean()), 4) if len(in_bbox) else None}
                    for flag in SOURCE_COLUMNS}
    numeric = [c for c in value_cols if fm[c].dtype.kind == "f"]
    ranges = {c: {k: (None if pd.isna(v) else round(float(v), 4)) for k, v in
                  fm[c].quantile([0.0, 0.01, 0.5, 0.99, 1.0]).rename(
                      {0.0: "min", 0.01: "p01", 0.5: "p50", 0.99: "p99", 1.0: "max"}).items()}
              for c in numeric}
    per_window = (fm.groupby("time_window_start")[list(SOURCE_COLUMNS)].mean()
                  .agg(["min", "mean", "max"]).round(4).to_dict())
    return {
        "coverage": {
            "rows": int(len(fm)),
            "cells": int(n_cells),
            "cells_in_study_bbox": int(fm.loc[fm["in_study_bbox"], "grid_id"].nunique()),
            "time_windows": int(n_windows),
            "time_range": [str(windows.min()), str(windows.max())],
            "static_cutoff": sorted({str(c) for c in audit["static_cutoff"]}),
        },
        "checks": checks,
        "all_passed": all(c["status"] != "gagal" for c in checks),
        "missing_fraction": missing,
        "availability_fraction": availability,
        "availability_per_window": per_window,
        "value_ranges": ranges,
    }


def quality_markdown(report: dict, title: str) -> str:
    cov = report["coverage"]
    lines = [f"# {title}", "",
             f"- Baris: {cov['rows']:,} ({cov['cells']:,} sel × {cov['time_windows']:,} time window)",
             f"- Sel di bbox wilayah studi: {cov['cells_in_study_bbox']:,}",
             f"- Rentang: {cov['time_range'][0]} sampai {cov['time_range'][1]}",
             f"- Batas waktu fitur statis: {', '.join(cov['static_cutoff'][:3])}"
             + (" …" if len(cov["static_cutoff"]) > 3 else ""),
             f"- Seluruh pemeriksaan: **{'lolos' if report['all_passed'] else 'ada yang gagal'}**", "",
             "## Pemeriksaan", "", "| Pemeriksaan | Status | Rincian |", "|---|---|---|"]
    for c in report["checks"]:
        lines.append(f"| {c['check']} | {c['status']} | `{c['detail']}` |")
    lines += ["", "## Ketersediaan sumber", "", "| Penanda | Seluruh sel | Sel di bbox studi |", "|---|---|---|"]
    for k, v in report["availability_fraction"].items():
        lines.append(f"| {k} | {v['seluruh']:.1%} | " + (f"{v['di_bbox_studi']:.1%} |" if v["di_bbox_studi"] is not None else "– |"))
    lines += ["", "## Proporsi nilai hilang", "", "| Fitur | Hilang |", "|---|---|"]
    lines += [f"| {k} | {v:.1%} |" for k, v in report["missing_fraction"].items()]
    lines += ["", "## Rentang nilai", "", "| Fitur | min | p01 | p50 | p99 | max |", "|---|---|---|---|---|---|"]
    for k, v in report["value_ranges"].items():
        lines.append(f"| {k} | " + " | ".join("–" if v[q] is None else f"{v[q]:g}" for q in
                                            ("min", "p01", "p50", "p99", "max")) + " |")
    return "\n".join(lines) + "\n"
