"""Analisis galat baseline menurut wilayah, waktu, dan ketersediaan data pendukung.

Dimensi analisis (ruang lingkup TI-AI-04):

* wilayah: per stasiun, per kota administrasi, dan per tipe stasiun;
* waktu: jam lokal (WIB), hari kerja/akhir pekan, dan per tanggal;
* ketersediaan data pendukung: jarak ke stasiun sumber terdekat pada time
  window yang sama, jumlah stasiun sumber yang tersedia, dan porsi stasiun
  Reference di antara sumber yang dipakai.

Untuk IDW, satu-satunya "fitur" adalah pengukuran sensor darat di sekitar
lokasi, sehingga ketersediaan fitur diukur dari kerapatan dan jenis
stasiun sumber. Kelompok dengan RMSE di atas ``high_error_factor`` × RMSE
agregat ditandai galat besar; kelompok dengan n di bawah
``min_obs_per_station`` ditandai data terbatas.
"""

from __future__ import annotations

import numpy as np
import pandas as pd

from spatial_model.baseline.evaluate import STATION_META, compute_metrics, grouped_metrics

WEEKDAY_ID = ["Senin", "Selasa", "Rabu", "Kamis", "Jumat", "Sabtu", "Minggu"]


def add_analysis_columns(pred: pd.DataFrame, eval_cfg: dict) -> pd.DataFrame:
    tz = eval_cfg.get("local_timezone", "Asia/Jakarta")
    local = pred["window_start_utc"].dt.tz_convert(tz)
    bins_km = eval_cfg.get("distance_bins_km", [0, 1, 2, 3, 5, 10, 50])
    out = pred.assign(
        error=pred["predicted"] - pred["observed"],
        hour_local=local.dt.hour,
        date_local=local.dt.date.astype(str),
        day_type=np.where(local.dt.dayofweek >= 5, "akhir pekan", "hari kerja"),
        nearest_source_km_bin=pd.cut(pred["nearest_source_m"] / 1000.0, bins_km, right=False).astype(str),
        sources_available_bin=pd.cut(pred["n_sources_available"], [0, 5, 10, 25, 50, 75, 100, 1000],
                                     right=False).astype(str),
        reference_fraction_bin=pd.cut(pred["reference_fraction_used"], [-0.01, 0.0, 0.25, 0.5, 1.0],
                                      labels=["0", "(0, 0,25]", "(0,25, 0,5]", "(0,5, 1]"]).astype(str),
    )
    return out


def per_station(pred: pd.DataFrame, overall: dict[str, dict], eval_cfg: dict) -> pd.DataFrame:
    table = grouped_metrics(pred, ["pollutant", "station_uuid"])
    meta = pred.drop_duplicates("station_uuid")[STATION_META]
    table = table.merge(meta, on="station_uuid", how="left")
    # Jarak ke stasiun lain terdekat yang melaporkan polutan yang sama.
    nn = []
    for pollutant, g in table.groupby("pollutant"):
        xy = g[["x", "y"]].to_numpy()
        d = np.sqrt(((xy[:, None, :] - xy[None, :, :]) ** 2).sum(axis=2))
        np.fill_diagonal(d, np.inf)
        nn.append(pd.Series(d.min(axis=1) / 1000.0, index=g.index))
    table["nearest_station_km"] = pd.concat(nn)
    median_nearest = pred.groupby(["pollutant", "station_uuid"])["nearest_source_m"].median() / 1000.0
    table = table.merge(median_nearest.rename("median_nearest_source_km").reset_index(),
                        on=["pollutant", "station_uuid"], how="left")
    factor = float(eval_cfg.get("high_error_factor", 1.5))
    min_obs = int(eval_cfg.get("min_obs_per_station", 24))
    table["high_error"] = table.apply(lambda r: r["rmse"] > factor * overall[r["pollutant"]]["rmse"], axis=1)
    table["limited_data"] = table["n"] < min_obs
    return table.sort_values(["pollutant", "rmse"], ascending=[True, False]).reset_index(drop=True)


BREAKDOWNS = {
    "by_kota": ["kota"],
    "by_station_type": ["type"],
    "by_suspected_low_bias": ["suspected_low_bias"],
    "by_hour_local": ["hour_local"],
    "by_day_type": ["day_type"],
    "by_date_local": ["date_local"],
    "by_nearest_source_km": ["nearest_source_km_bin"],
    "by_sources_available": ["sources_available_bin"],
    "by_reference_fraction_used": ["reference_fraction_bin"],
}


def breakdowns(pred: pd.DataFrame, overall: dict[str, dict], eval_cfg: dict) -> dict[str, pd.DataFrame]:
    factor = float(eval_cfg.get("high_error_factor", 1.5))
    min_obs = int(eval_cfg.get("min_obs_per_station", 24))
    tables = {}
    for name, cols in BREAKDOWNS.items():
        t = grouped_metrics(pred, ["pollutant", *cols])
        t["rmse_ratio"] = t.apply(lambda r: r["rmse"] / overall[r["pollutant"]]["rmse"], axis=1)
        t["high_error"] = t["rmse_ratio"] > factor
        t["limited_data"] = t["n"] < min_obs
        tables[name] = t.sort_values(["pollutant", *cols]).reset_index(drop=True)
    return tables


def overall_metrics(pred: pd.DataFrame) -> dict[str, dict]:
    """Agregat lintas stasiun: pooled (seluruh stasiun-jam) dan rata-rata metrik per stasiun."""
    result = {}
    for pollutant, g in pred.groupby("pollutant"):
        pooled = compute_metrics(g["observed"], g["predicted"])
        per = grouped_metrics(g, ["station_uuid"])
        pooled["n_stations"] = int(per["n"].gt(0).sum())
        pooled["n_windows"] = int(g["window_start_utc"].nunique())
        pooled["coverage"] = float(g["predicted"].notna().mean())
        pooled["station_mean"] = {m: float(per[m].mean()) for m in ("mae", "rmse", "r2", "bias")}
        pooled["station_median"] = {m: float(per[m].median()) for m in ("mae", "rmse", "r2", "bias")}
        result[pollutant] = pooled
    return result


def colocated_pairs(stations: pd.DataFrame, max_m: float) -> list[tuple[pd.Series, pd.Series, float]]:
    """Pasangan stasiun yang berimpit (jarak < max_m) pada tabel per stasiun satu polutan."""
    s = stations.reset_index(drop=True)
    xy = s[["x", "y"]].to_numpy()
    d = np.sqrt(((xy[:, None, :] - xy[None, :, :]) ** 2).sum(axis=2))
    i, j = np.where(np.triu(d < max_m, 1))
    return [(s.iloc[a], s.iloc[b], float(d[a, b])) for a, b in zip(i, j)]


def findings(
    overall: dict[str, dict],
    stations: pd.DataFrame,
    tables: dict[str, pd.DataFrame],
    eval_cfg: dict,
) -> list[str]:
    """Butir temuan otomatis: area/keadaan dengan galat besar atau data pendukung terbatas."""
    factor = float(eval_cfg.get("high_error_factor", 1.5))
    items: list[str] = []
    for pollutant, m in overall.items():
        name = pollutant.upper().replace("PM25", "PM2.5")
        st = stations[stations["pollutant"] == pollutant]
        items.append(
            f"{name}: RMSE {m['rmse']:.2f} µg/m³, MAE {m['mae']:.2f} µg/m³, R² {m['r2']:.3f}, "
            f"bias {m['bias']:+.2f} µg/m³ pada n = {m['n']} stasiun-jam dari {m['n_stations']} stasiun."
        )
        high = st[st["high_error"]]
        if len(high):
            top = ", ".join(
                f"{r.kode} {r['name']} (RMSE {r.rmse:.1f}, bias {r.bias:+.1f}, stasiun terdekat {r.nearest_station_km:.1f} km)"
                for _, r in high.head(8).iterrows()
            )
            items.append(f"{name}: {len(high)} stasiun dengan RMSE > {factor:g}× agregat: {top}.")
        limited = st[st["limited_data"]]
        if len(limited):
            items.append(
                f"{name}: {len(limited)} stasiun memiliki pengamatan kurang dari "
                f"{eval_cfg.get('min_obs_per_station', 24)} stasiun-jam pada split ini: "
                + ", ".join(limited["kode"].astype(str).head(12)) + "."
            )
        for table_name, label in (
            ("by_kota", "wilayah"),
            ("by_hour_local", "jam WIB"),
            ("by_nearest_source_km", "jarak ke stasiun sumber terdekat (km)"),
            ("by_sources_available", "jumlah stasiun sumber tersedia"),
            ("by_reference_fraction_used", "porsi stasiun Reference di antara sumber"),
            ("by_station_type", "tipe stasiun uji"),
            ("by_suspected_low_bias", "dugaan bias rendah stasiun uji"),
        ):
            t = tables[table_name]
            t = t[(t["pollutant"] == pollutant) & t["high_error"] & ~t["limited_data"]]
            if len(t):
                key = [c for c in t.columns if c not in {"pollutant"} and c in sum(BREAKDOWNS.values(), [])][0]
                groups = ", ".join(f"{r[key]} (RMSE {r['rmse']:.1f}, n {r['n']})" for _, r in t.iterrows())
                items.append(f"{name}: galat besar menurut {label}: {groups}.")
        for a, b, dist in colocated_pairs(st, float(eval_cfg.get("colocated_m", 100.0))):
            items.append(
                f"{name}: {a.kode} ({a.type}) dan {b.kode} ({b.type}) berjarak {dist:.0f} m; pada LOSO "
                f"masing-masing diestimasi hampir seluruhnya dari yang lain, sehingga RMSE {a.rmse:.1f} dan "
                f"{b.rmse:.1f} µg/m³ mencerminkan selisih antarinstrumen (bias {a.bias:+.1f} / {b.bias:+.1f}), "
                "bukan galat interpolasi."
            )
        if m["n_stations"] < 20:
            items.append(
                f"{name}: data pendukung terbatas, hanya {m['n_stations']} stasiun dengan median jarak "
                f"ke stasiun lain terdekat {st['nearest_station_km'].median():.1f} km."
            )
    return items
