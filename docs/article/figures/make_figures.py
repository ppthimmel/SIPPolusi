"""Regenerate the article's data figures from the results committed in this repository.

Run from any directory:  python docs/article/figures/make_figures.py
Needs numpy and matplotlib. Writes spatial_forest.pdf and route_tradeoff.pdf next to this file.
"""
from __future__ import annotations

import csv
import gzip
import json
import re
from collections import defaultdict
from pathlib import Path

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt  # noqa: E402
import numpy as np  # noqa: E402

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[2]
COMPARISON = ROOT / "docs/experiments/model-comparison-ds-v0.3.0"
SCENARIOS = ROOT / "docs/experiments/TI-AI-11/morl-comparison/scenarios.jsonl.gz"

INK = "#0b0b0b"
MUTED = "#52514e"
GRID = "#d9d8d3"
DEPLOYED = "#2a78d6"
OTHER = "#77766f"
ORANGE = "#eb6834"
AQUA = "#1baf7a"

plt.rcParams.update({
    "font.family": "STIXGeneral",
    "mathtext.fontset": "stix",
    "font.size": 8,
    "axes.labelsize": 8,
    "axes.titlesize": 8.5,
    "xtick.labelsize": 7.5,
    "ytick.labelsize": 7.5,
    "legend.fontsize": 7.5,
    "pdf.fonttype": 42,
    "axes.edgecolor": MUTED,
    "axes.labelcolor": INK,
    "xtick.color": MUTED,
    "ytick.color": INK,
    "axes.linewidth": 0.6,
})

# (label in the figure, model name in results/summary_loso_<target>.csv)
SPATIAL_ROWS = {
    "pm25": [
        ("IDW, p=1, all stations", "IDW p=1, all stations"),
        ("Ordinary kriging", "Ordinary kriging"),
        ("Ensemble of six learners", "Ensemble, all 6 learned"),
        ("Ridge", "Ridge"),
        ("LightGBM, all features", "LightGBM direct, all features"),
        ("XGBoost", "XGBoost"),
        ("Random forest", "Random forest"),
    ],
    "no2": [
        ("LightGBM, all features", "LightGBM direct, all features"),
        ("Extra trees, all features", "Extra trees, all features, tuned"),
        ("Ridge", "Ridge"),
        ("IDW, p=2, all stations", "IDW p=2, all stations"),
        ("IDW, p=1, all stations", "IDW p=1, all stations"),
        ("Random forest", "Random forest"),
        ("Ordinary kriging", "Ordinary kriging"),
    ],
}
STGNN_REPORT = {
    "pm25": "ds-v0.3.0_loso_pm25_gpu_seed42_20261014_a7eabc1b",
    "no2": "ds-v0.3.0_loso_no2_gpu_seed42_20261015_0e6b77e4",
}
DEPLOYED_LABEL = {"pm25": "IDW + ST-GNN", "no2": "LightGBM, all features"}


def spatial_rows(target):
    """(label, difference, low, high) of RMSE against IDW p=2, k=8, best first."""
    with open(COMPARISON / f"results/summary_loso_{target}.csv", encoding="utf-8") as handle:
        table = {row["model"]: row["vs IDW p2k8 [95% CI]"] for row in csv.DictReader(handle)}
    rows = []
    for label, model in SPATIAL_ROWS[target]:
        diff, low, high = re.fullmatch(r"([-+\d.]+) \[([-+\d.]+), ([-+\d.]+)\]", table[model]).groups()
        rows.append((label, float(diff), float(low), float(high)))
    report = (COMPARISON / "stgnn_idw_residual" / STGNN_REPORT[target] / "report.md").read_text(encoding="utf-8")
    diff, low, high = re.search(
        r"Versus IDW p2 k8: ([-+\d.]+); 95% interval \[([-+\d.]+), ([-+\d.]+)\]", report).groups()
    rows.append(("IDW + ST-GNN", float(diff), float(low), float(high)))
    return sorted(rows, key=lambda row: row[1])


def spatial_forest():
    fig, axes = plt.subplots(1, 2, figsize=(7.16, 2.3))
    titles = {"pm25": "(a) PM$_{2.5}$: 97 stations, 4,358 station-hours",
              "no2": "(b) NO$_2$: 13 stations, 604 station-hours"}
    for ax, target in zip(axes, ("pm25", "no2")):
        rows = spatial_rows(target)
        ys = np.arange(len(rows))[::-1]
        ax.axvline(0, color=INK, lw=0.8, zorder=1)
        for y, (label, diff, low, high) in zip(ys, rows):
            chosen = label == DEPLOYED_LABEL[target]
            color = DEPLOYED if chosen else OTHER
            ax.plot([low, high], [y, y], color=color, lw=1.6 if chosen else 1.0, solid_capstyle="round", zorder=2)
            ax.plot(diff, y, marker="D" if chosen else "o", ms=4.6 if chosen else 3.6, color=color,
                    mec="white", mew=0.5, zorder=3)
        ax.set_yticks(ys, [row[0] for row in rows])
        for tick, row in zip(ax.get_yticklabels(), rows):
            if row[0] == DEPLOYED_LABEL[target]:
                tick.set_fontweight("bold")
        ax.set_title(titles[target], loc="left", color=INK)
        ax.set_xlabel("RMSE difference from IDW ($p$=2, $k$=8) ($\\mu$g/m$^3$)")
        ax.grid(axis="x", color=GRID, lw=0.5)
        ax.set_axisbelow(True)
        ax.tick_params(axis="y", length=0)
        ax.tick_params(axis="x", length=2, width=0.5)
        for side in ("top", "right", "left"):
            ax.spines[side].set_visible(False)
    fig.tight_layout(w_pad=1.5, pad=0.3)
    fig.savefig(HERE / "spatial_forest.pdf")
    plt.close(fig)


def route_curves(rows, method):
    """Mean paired % change of (time, exposure) against the fastest route, per preference."""
    groups = defaultdict(list)
    for row in rows:
        route = row["optimal"] if method == "optimal" else row["methods"][method]["route"]
        if route and route.get("reached"):
            fastest = row["fastest"]
            groups[round(row["alpha"], 1)].append((100 * (route["time_s"] / fastest["time_s"] - 1),
                                                   100 * (route["exposure"] / fastest["exposure"] - 1)))
    alphas = sorted(groups)
    return alphas, np.array([np.mean(groups[a], axis=0) for a in alphas])


def route_tradeoff():
    with gzip.open(SCENARIOS, "rt", encoding="utf-8") as handle:
        records = [json.loads(line) for line in handle]
    series = [
        ("optimal", "Exact weighted optimum", INK, "s", "--"),
        ("regret_prior", "Regret prior (untrained)", AQUA, "^", "-"),
        ("scalarized", "Preference-conditioned policy", DEPLOYED, "o", "-"),
        ("fixed_preference_set", "Fixed-preference set", ORANGE, "D", "-"),
    ]
    fig, axes = plt.subplots(1, 2, figsize=(7.16, 2.55))
    for ax, fields, title in zip(axes, ("real", "perturbed"),
                                 ("(a) IDW exposure fields", "(b) Synthetically perturbed fields (stress test)")):
        rows = [r for r in records if r["stratum"] == "trained-range" and r["fields"] == fields]
        ax.axhline(0, color=GRID, lw=0.7, zorder=1)
        ax.axvline(0, color=GRID, lw=0.7, zorder=1)
        for method, label, color, marker, style in series:
            alphas, curve = route_curves(rows, method)
            ax.plot(curve[:, 0], curve[:, 1], color=color, ls=style, lw=1.3, marker=marker, ms=3.4,
                    mec="white", mew=0.4, label=label, zorder=3)
            if method == "fixed_preference_set":
                for alpha, offset in ((0.0, (5, 5)), (1.0, (5, 5))):
                    point = curve[alphas.index(alpha)]
                    ax.annotate(f"$\\alpha$={alpha:g}", point, xytext=offset, textcoords="offset points",
                                fontsize=7, color=MUTED)
        ax.margins(y=0.14)
        ax.set_title(title, loc="left", color=INK)
        ax.set_xlabel("Mean added travel time vs. fastest route (%)")
        ax.set_ylabel("Mean exposure change vs. fastest (%)")
        ax.grid(color=GRID, lw=0.4)
        ax.set_axisbelow(True)
        ax.tick_params(length=2, width=0.5, colors=MUTED)
        for side in ("top", "right"):
            ax.spines[side].set_visible(False)
    handles, labels = axes[0].get_legend_handles_labels()
    fig.legend(handles, labels, loc="lower center", ncol=4, frameon=False, handlelength=2.4, columnspacing=1.4)
    fig.tight_layout(rect=(0, 0.075, 1, 1), w_pad=1.5, pad=0.3)
    fig.savefig(HERE / "route_tradeoff.pdf")
    plt.close(fig)


if __name__ == "__main__":
    spatial_forest()
    route_tradeoff()
