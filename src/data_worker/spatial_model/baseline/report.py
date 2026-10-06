"""Visualisasi galat dan laporan Markdown untuk satu run baseline."""

from __future__ import annotations

import pathlib

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt  # noqa: E402
import numpy as np  # noqa: E402
import pandas as pd  # noqa: E402

from spatial_model.baseline.evaluate import METRIC_DEFINITIONS  # noqa: E402

LABEL = {"pm25": "PM2.5", "no2": "NO2"}
UNIT = "µg/m³"


def _save(fig, path: pathlib.Path) -> str:
    fig.tight_layout()
    fig.savefig(path, dpi=130)
    plt.close(fig)
    return path.name


def plot_scatter(pred: pd.DataFrame, out: pathlib.Path) -> str:
    pollutants = sorted(pred["pollutant"].unique())
    fig, axes = plt.subplots(1, len(pollutants), figsize=(5 * len(pollutants), 4.6), squeeze=False)
    for ax, pollutant in zip(axes[0], pollutants):
        g = pred[(pred["pollutant"] == pollutant) & pred["predicted"].notna()]
        for kind, color in (("Sensor", "#4C78A8"), ("Reference", "#E45756")):
            s = g[g["type"] == kind]
            ax.scatter(s["observed"], s["predicted"], s=4, alpha=0.35, color=color, label=kind, linewidths=0)
        hi = float(np.nanpercentile(np.r_[g["observed"], g["predicted"]], 99.5)) if len(g) else 1
        ax.plot([0, hi], [0, hi], color="#555", lw=1)
        ax.set_xlim(0, hi)
        ax.set_ylim(0, hi)
        ax.set_xlabel(f"Ground truth {LABEL[pollutant]} ({UNIT})")
        ax.set_ylabel(f"Estimasi IDW ({UNIT})")
        ax.set_title(f"{LABEL[pollutant]}: estimasi LOSO vs ground truth")
        ax.legend(markerscale=3, frameon=False)
    return _save(fig, out / "scatter_observed_vs_predicted.png")


def plot_station_map(stations: pd.DataFrame, out: pathlib.Path) -> str:
    pollutants = sorted(stations["pollutant"].unique())
    fig, axes = plt.subplots(1, len(pollutants), figsize=(5.6 * len(pollutants), 5.2), squeeze=False)
    for ax, pollutant in zip(axes[0], pollutants):
        s = stations[stations["pollutant"] == pollutant]
        sc = ax.scatter(s["lon"], s["lat"], c=s["rmse"], s=12 + 60 * s["n"] / max(s["n"].max(), 1),
                        cmap="viridis", edgecolors=np.where(s["high_error"], "#d62728", "none"), linewidths=1.2)
        for _, r in s[s["high_error"]].iterrows():
            ax.annotate(str(r["kode"]), (r["lon"], r["lat"]), fontsize=7, xytext=(3, 3), textcoords="offset points")
        ax.set_aspect("equal")
        ax.set_xlabel("Bujur")
        ax.set_ylabel("Lintang")
        ax.set_title(f"{LABEL[pollutant]}: RMSE per stasiun uji\n(tepi merah = galat besar)", fontsize=10)
        fig.colorbar(sc, ax=ax, label=f"RMSE ({UNIT})", shrink=0.8)
    return _save(fig, out / "map_rmse_per_station.png")


def plot_by(tables: dict, name: str, col: str, title: str, xlabel: str, out: pathlib.Path, fname: str,
             order: list | None = None) -> str:
    t = tables[name]
    pollutants = sorted(t["pollutant"].unique())
    fig, axes = plt.subplots(1, len(pollutants), figsize=(5.4 * len(pollutants), 3.8), squeeze=False)
    for ax, pollutant in zip(axes[0], pollutants):
        g = t[t["pollutant"] == pollutant].copy()
        if order is not None:
            g[col] = pd.Categorical(g[col], [o for o in order if o in set(g[col])], ordered=True)
            g = g.sort_values(col)
        x = np.arange(len(g))
        ax.bar(x, g["rmse"], color=np.where(g["high_error"], "#E45756", "#4C78A8"))
        ax.plot(x, g["mae"], "o-", color="#F58518", ms=3, label="MAE")
        ax.set_xticks(x)
        ax.set_xticklabels(g[col].astype(str), rotation=60 if len(g) > 8 else 0, ha="right" if len(g) > 8 else "center",
                           fontsize=7 if len(g) > 12 else 8)
        ax.set_ylabel(f"RMSE (batang), MAE ({UNIT})")
        ax.set_xlabel(xlabel)
        ax.set_title(f"{LABEL[pollutant]}: {title}")
        ax.legend(frameon=False, fontsize=8)
    return _save(fig, out / fname)


def plot_sweep(table: pd.DataFrame, out: pathlib.Path, metric: str = "rmse") -> str:
    pollutants = sorted(table["pollutant"].unique())
    fig, axes = plt.subplots(1, len(pollutants), figsize=(4.8 * len(pollutants), 3.8), squeeze=False)
    for ax, pollutant in zip(axes[0], pollutants):
        g = table[table["pollutant"] == pollutant].pivot(index="power", columns="neighbors", values=metric)
        g = g[sorted(g.columns, key=lambda k: (k == 0, k))]
        im = ax.imshow(g.to_numpy(), cmap="viridis_r", aspect="auto")
        ax.set_xticks(range(g.shape[1]))
        ax.set_xticklabels(["semua" if k == 0 else str(k) for k in g.columns])
        ax.set_yticks(range(g.shape[0]))
        ax.set_yticklabels([f"{p:g}" for p in g.index])
        for i in range(g.shape[0]):
            for j in range(g.shape[1]):
                ax.text(j, i, f"{g.iat[i, j]:.2f}", ha="center", va="center", fontsize=8, color="white")
        ax.set_xlabel("neighbors")
        ax.set_ylabel("power")
        ax.set_title(f"{LABEL[pollutant]}: {metric.upper()} validasi")
        fig.colorbar(im, ax=ax, shrink=0.8)
    return _save(fig, out / "sweep_validation.png")


def plot_grid_map(grid: pd.DataFrame, grid_spec, sources: pd.DataFrame, window, pollutant: str,
                  out: pathlib.Path) -> str:
    values = grid[f"{pollutant}_ugm3"].to_numpy().reshape(grid_spec.ny, grid_spec.nx)
    extent = [grid_spec.x0 / 1000, (grid_spec.x0 + grid_spec.nx * grid_spec.cell_m) / 1000,
              grid_spec.y0 / 1000, (grid_spec.y0 + grid_spec.ny * grid_spec.cell_m) / 1000]
    fig, ax = plt.subplots(figsize=(6.4, 6))
    im = ax.imshow(values, origin="lower", extent=extent, cmap="magma_r")
    s = sources[sources["pollutant"] == pollutant]
    ax.scatter(s["x"] / 1000, s["y"] / 1000, c=s["value_ugm3"], cmap="magma_r", vmin=np.nanmin(values),
               vmax=np.nanmax(values), edgecolors="white", s=22, linewidths=0.8)
    ax.set_xlabel("x EPSG:32748 (km)")
    ax.set_ylabel("y EPSG:32748 (km)")
    ax.set_title(f"{LABEL[pollutant]} grid {grid_spec.cell_m:g} m, IDW\n{window:%Y-%m-%d %H:%M} UTC")
    fig.colorbar(im, ax=ax, label=f"{LABEL[pollutant]} ({UNIT})", shrink=0.8)
    return _save(fig, out / f"grid_{pollutant}_example.png")


# ------------------------------------------------------------------ Markdown


def _md_table(df: pd.DataFrame, cols: list[str], floatfmt: str = ".2f") -> str:
    def fmt(v):
        if isinstance(v, (bool, np.bool_)):
            return "ya" if v else ""
        if isinstance(v, (float, np.floating)):
            return "" if np.isnan(v) else format(v, floatfmt)
        return str(v)

    lines = ["| " + " | ".join(cols) + " |", "|" + "---|" * len(cols)]
    lines += ["| " + " | ".join(fmt(r[c]) for c in cols) + " |" for _, r in df.iterrows()]
    return "\n".join(lines)


def write_report(path: pathlib.Path, ctx: dict) -> None:
    rm = ctx["run_manifest"]
    lines = [
        f"# Baseline IDW Spatial Downscaling Model: {rm['run_id']}",
        "",
        "Dibangkitkan otomatis oleh `python -m spatial_model.baseline run`. "
        "Rujukan: Dokumen Desain subbab 4.3–4.4, Tabel 3.8, isu TI-AI-04.",
        "",
        "## Ringkasan eksperimen",
        "",
        f"- Dataset: `{rm['dataset']['dataset_version']}` "
        f"(manifest SHA-256 `{rm['dataset']['manifest_sha256'][:16]}…`)",
        f"- Sumber ground truth: {rm['dataset']['source_type']}, snapshot {rm['dataset'].get('source_snapshot_utc')}",
        f"- Split dilaporkan: **{rm['evaluation']['report_split']}** "
        f"[{rm['dataset']['split_boundaries']['val_end']}, {rm['dataset']['split_boundaries']['end']})",
        f"- Skema: {rm['evaluation']['cv_scheme']}; model IDW power = {rm['model']['power']:g}, "
        f"neighbors = {rm['model']['neighbors']}, jarak pada {rm['model']['crs']}",
        f"- Seed: {rm['seed']}; commit `{rm['code'].get('git_commit', '?')[:10]}`"
        + (" (working tree tidak bersih)" if rm['code'].get('git_dirty') else ""),
        "",
        "## Metrik utama (split uji, IDW konfigurasi Tabel 3.8)",
        "",
        "Agregat *pooled* atas seluruh stasiun-jam. Kolom `RMSE stasiun` dan `R² stasiun` adalah median "
        "metrik per stasiun (median dipakai karena R² stasiun dengan variasi sangat kecil bernilai negatif ekstrem).",
        "",
    ]
    rows = []
    for pollutant, m in ctx["overall"].items():
        rows.append({"polutan": LABEL[pollutant], "n": m["n"], "stasiun": m["n_stations"],
                     "time window": m["n_windows"],
                     "MAE": m["mae"], "RMSE": m["rmse"], "R²": m["r2"], "bias": m["bias"], "ȳ": m["mean_obs"],
                     "RMSE stasiun": m["station_median"]["rmse"], "R² stasiun": m["station_median"]["r2"]})
    lines.append(_md_table(pd.DataFrame(rows), list(rows[0].keys()), ".3f"))
    lines += ["", "Definisi dan satuan metrik:", ""]
    lines += [f"- `{k}`: {v}" for k, v in METRIC_DEFINITIONS.items()]
    lines += ["", "R² per stasiun dihitung dari deret waktu stasiun tersebut (variasi temporal), "
              "sedangkan R² pooled juga memuat variasi antarstasiun.", ""]

    if ctx.get("grid"):
        g = ctx["grid"]
        lines += ["## Inferensi grid (run_idw_fallback)", "",
                  f"Contoh time window {g['window_start_utc']}: {g['n_cells']} sel {g['grid_spec']['cell_m']:g} m "
                  f"pada {g['grid_spec']['crs']} yang menutup JAKARTA_BBOX, {g['duration_s']} s. Grid belum "
                  "memakai masker daratan, sehingga sel laut di utara ikut diestimasi; median jarak sel ke "
                  f"stasiun terdekat {g['nearest_station_m']['median'] / 1000:.1f} km, maksimum "
                  f"{g['nearest_station_m']['max'] / 1000:.1f} km.", ""]

    if ctx.get("variants"):
        lines += ["## Varian evaluasi", ""]
        vrows = []
        for name, v in ctx["variants"].items():
            for pollutant, m in v["metrics"].items():
                vrows.append({"varian": name, "polutan": LABEL[pollutant], "n": m["n"], "stasiun": m["n_stations"],
                              "MAE": m["mae"], "RMSE": m["rmse"], "R²": m["r2"], "bias": m["bias"]})
        if vrows:
            lines += [_md_table(pd.DataFrame(vrows), list(vrows[0].keys()), ".3f"), ""]

    if ctx.get("tuned"):
        lines += ["## Analisis sensitivitas (split validasi)", "",
                  "Konfigurasi terbaik pada validasi, lalu dievaluasi pada split uji sebagai pembanding. "
                  "Hasil utama tetap konfigurasi Tabel 3.8 agar sama dengan fallback.", ""]
        trows = []
        for pollutant, t in ctx["tuned"].items():
            trows.append({"polutan": LABEL[pollutant], "power": t["power"],
                          "neighbors": "semua" if t["neighbors"] == 0 else t["neighbors"],
                          "RMSE val": t["val_rmse"], "RMSE uji": t["test"]["rmse"], "MAE uji": t["test"]["mae"],
                          "R² uji": t["test"]["r2"]})
        lines += [_md_table(pd.DataFrame(trows), list(trows[0].keys()), ".3f"), "",
                  f"![sweep](figures/{ctx['figures']['sweep']})", ""]

    lines += ["## Temuan: area dan keadaan dengan galat besar atau data terbatas", ""]
    lines += [f"- {f}" for f in ctx["findings"]]
    lines += ["", "## Galat per stasiun (10 terbesar per polutan)", ""]
    st = ctx["stations"]
    for pollutant in sorted(st["pollutant"].unique()):
        s = st[st["pollutant"] == pollutant].head(10)
        lines += [f"### {LABEL[pollutant]}", "",
                  _md_table(s, ["kode", "name", "type", "kota", "n", "rmse", "mae", "bias", "r2",
                                "nearest_station_km", "high_error", "limited_data"]), ""]
    lines += ["## Galat menurut wilayah dan waktu", ""]
    for name, col in (("by_kota", "kota"), ("by_station_type", "type"),
                      ("by_suspected_low_bias", "suspected_low_bias"), ("by_day_type", "day_type"),
                      ("by_nearest_source_km", "nearest_source_km_bin"),
                      ("by_sources_available", "sources_available_bin"),
                      ("by_reference_fraction_used", "reference_fraction_bin")):
        lines += [f"### {name}", "",
                  _md_table(ctx["tables"][name], ["pollutant", col, "n", "rmse", "mae", "bias", "r2",
                                                  "rmse_ratio", "high_error", "limited_data"]), ""]
    lines += ["## Visualisasi", ""]
    for key, fname in ctx["figures"].items():
        if key != "sweep":
            lines.append(f"![{key}](figures/{fname})")
    lines += ["", "## Berkas", "",
              "- `run_manifest.json`: konfigurasi, seed, versi dataset/kode/pustaka, perangkat keras, durasi",
              "- `config.toml`: salinan konfigurasi yang dipakai",
              "- `metrics.json`: metrik agregat, per stasiun, dan hasil sensitivitas",
              "- `tables/*.csv`: seluruh tabel analisis galat",
              "- `predictions.parquet`: estimasi LOSO per stasiun-jam (tidak di-commit)", ""]
    path.write_text("\n".join(lines), encoding="utf-8")
