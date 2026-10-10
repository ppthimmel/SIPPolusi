"""build_feature_matrix (Dokumen Desain Tabel 3.8, B18–B20; TI-AI-02).

Satu baris per (sel grid kanonik, time window). Untuk time window t dengan
waktu inferensi τ = t + 1 jam:

* GEOS-CF: valid time terbaru dengan ``available_at_utc <= τ`` (as-of), dengan
  umur ``geoscf_age_h``; tidak tersedia bila umur > 72 jam.
* Sentinel-5P: per sel 5 km, pengamatan terbaru dengan ``produced_at <= τ``.
* Open-Meteo: nilai pada jam t (diasumsikan tersedia pada τ).
* Fitur statis (Xroad, Xland, Xactivity) dari ``static_cutoff``. Bila tidak
  diberikan, ``static_cutoff`` = awal hari UTC dari τ, sehingga tetap <= τ.
  ``static_cutoff`` > τ hanya diizinkan bila ``strict_static=False``: kebijakan
  snapshot TI-AI-03, di mana komposit statis dibekukan pada akhir periode
  pelatihan dan dipakai untuk seluruh periode tersebut (dicatat di audit).
* ``dist_nearest_sensor_m`` hanya dari himpunan ``sensors`` yang diberikan
  (pada TI-AI-03, stasiun non-uji per fold).

Nilai yang tidak tersedia dibiarkan kosong (NaN) dengan penanda ``*_available``
bernilai false; tidak ada imputasi. ``build_feature_matrix`` juga
mengembalikan tabel audit per time window (valid time GEOS-CF dan waktu
tersedianya, produced_at Sentinel-5P terbaru yang dipakai) untuk pemeriksaan
kebocoran.
"""

from __future__ import annotations

import numpy as np
import pandas as pd

from contracts import JAKARTA_BBOX
from feature_matrix.sources import MAX_AGE_H, Sources, as_utc, nearest_sensor_distance
from feature_matrix.spec import FEATURE_NAMES, FEATURES_BY_NAME, INFERENCE_LAG_HOURS

NS_PER_H = 3_600_000_000_000


def _windows(time_windows) -> pd.DatetimeIndex:
    idx = as_utc(time_windows)
    if (idx != idx.floor("h")).any():
        raise ValueError("time_window_start wajib tepat di awal jam (UTC)")
    if idx.duplicated().any():
        raise ValueError("time window ganda")
    return idx.sort_values()


def static_features(src: Sources, cells: np.ndarray, cutoff: pd.Timestamp) -> pd.DataFrame:
    """Xroad, Xland, Xactivity untuk sel terpilih dengan batas waktu ``cutoff``."""
    parts = [src.xroad(), src.xland(cutoff), src.xactivity(cutoff)]
    return pd.concat([p.loc[cells] for p in parts], axis=1)


def build_feature_matrix(
    time_windows,
    cells=None,
    *,
    sources: Sources | None = None,
    static_cutoff=None,
    sensors: pd.DataFrame | None = None,
    strict_static: bool = True,
) -> tuple[pd.DataFrame, pd.DataFrame]:
    """FeatureMatrix untuk ``cells`` (grid_id; bawaan seluruh grid) × ``time_windows``.

    ``sensors`` berkolom ``x_utm``, ``y_utm`` (EPSG:32748). Mengembalikan
    (FeatureMatrix, audit).
    """
    src = sources or Sources()
    windows = _windows(time_windows)
    grid = src.grid
    cells = grid.grid_id if cells is None else np.unique(np.asarray(cells, dtype=np.int64))
    if (cells < 0).any() or (cells >= grid.spec.n_cells).any():
        raise ValueError("grid_id di luar grid kanonik")

    lon, lat = grid.lon[cells], grid.lat[cells]
    min_lon, min_lat, max_lon, max_lat = JAKARTA_BBOX.as_tuple()
    base = pd.DataFrame({
        "grid_id": cells,
        "x_utm": grid.x[cells],
        "y_utm": grid.y[cells],
        "in_study_bbox": (lon >= min_lon) & (lon <= max_lon) & (lat >= min_lat) & (lat <= max_lat),
    })
    if sensors is not None and len(sensors):
        base["dist_nearest_sensor_m"] = nearest_sensor_distance(base["x_utm"], base["y_utm"],
                                                                sensors["x_utm"], sensors["y_utm"])
        base["sensor_available"] = True
    else:
        base["dist_nearest_sensor_m"] = np.nan
        base["sensor_available"] = False

    geos = src.geoscf()
    geos_cell = geos["cell_of_grid"][cells]
    s5p = src.s5p()
    s5p_cell = s5p["cell_of_grid"][cells]
    met = src.open_meteo()

    fixed_cutoff = None if static_cutoff is None else as_utc([static_cutoff])[0]
    static_cache: dict = {}
    frames, audit = [], []
    for t in windows:
        tau = t + pd.Timedelta(hours=INFERENCE_LAG_HOURS)
        cutoff = fixed_cutoff if fixed_cutoff is not None else tau.floor("D")
        if strict_static and cutoff > tau:
            raise ValueError("static_cutoff melewati waktu inferensi; pakai strict_static=False bila disengaja")
        if cutoff not in static_cache:
            static_cache[cutoff] = static_features(src, cells, cutoff).reset_index(drop=True)
        f = pd.concat([base, static_cache[cutoff]], axis=1)
        f.insert(1, "time_window_start", t)
        f.insert(2, "inference_time", tau)
        t_ns, tau_ns = t.value, tau.value
        row = {"time_window_start": t, "inference_time": tau, "static_cutoff": cutoff,
               "static_after_inference": bool(cutoff > tau)}

        # GEOS-CF as-of
        pos = np.searchsorted(geos["avail_at"], tau_ns, side="right") - 1
        if pos >= 0:
            v_ns = int(geos["latest_valid"][pos])
            age = (t_ns - v_ns) / NS_PER_H
            v = pd.Timestamp(v_ns, tz="UTC")
            vals = geos["values"].reindex(pd.MultiIndex.from_arrays([np.full(len(cells), v), geos_cell]))
            ok = age <= MAX_AGE_H
            f["geoscf_pm25_ugm3"] = np.where(ok & vals["available_pm25"].fillna(False).to_numpy(bool),
                                             vals["pm25_ugm3"].to_numpy(), np.nan)
            f["geoscf_no2_ugm3"] = np.where(ok & vals["available_no2"].fillna(False).to_numpy(bool),
                                            vals["no2_ugm3"].to_numpy(), np.nan)
            f["geoscf_age_h"] = age if ok else np.nan
            row["geoscf_valid_time"] = v
            row["geoscf_available_at"] = geos["available_at_of_valid"].loc[v]
        else:
            f["geoscf_pm25_ugm3"] = f["geoscf_no2_ugm3"] = f["geoscf_age_h"] = np.nan
            row.update(geoscf_valid_time=pd.NaT, geoscf_available_at=pd.NaT)
        f["geoscf_available"] = f["geoscf_pm25_ugm3"].notna() | f["geoscf_no2_ugm3"].notna()

        # Sentinel-5P as-of per sel 5 km
        no2 = np.full(len(cells), np.nan)
        prec = np.full(len(cells), np.nan)
        age5 = np.full(len(cells), np.nan)
        latest_produced = pd.NaT
        for c in np.unique(s5p_cell[s5p_cell >= 0]):
            d = s5p["per_cell"].get(int(c))
            if d is None:
                continue
            p = np.searchsorted(d["produced"], tau_ns, side="right") - 1
            if p < 0:
                continue
            j = d["best"][p]
            a = (t_ns - d["observed"][j]) / NS_PER_H
            if a > MAX_AGE_H:
                continue
            m = s5p_cell == c
            no2[m], prec[m], age5[m] = d["no2"][j], d["precision"][j], a
            produced = pd.Timestamp(int(d["produced"][j]), tz="UTC")
            latest_produced = produced if pd.isna(latest_produced) else max(latest_produced, produced)
        f["s5p_no2_umol_m2"], f["s5p_no2_precision_umol_m2"], f["s5p_age_h"] = no2, prec, age5
        f["s5p_available"] = np.isfinite(no2)
        row["s5p_max_produced_at"] = latest_produced

        # Open-Meteo pada jam t
        if t in met.index:
            for col in met.columns:
                f[col] = met.at[t, col]
        else:
            for col in met.columns:
                f[col] = np.nan if col != "met_available" else False
        row["met_time"] = t if t in met.index else pd.NaT
        frames.append(f)
        audit.append(row)

    fm = pd.concat(frames, ignore_index=True)[FEATURE_NAMES]
    for name in FEATURE_NAMES:
        dtype = FEATURES_BY_NAME[name].dtype
        if dtype.startswith("datetime"):
            continue
        fm[name] = fm[name].astype("bool" if dtype == "bool" else dtype)
    return fm, pd.DataFrame(audit)
