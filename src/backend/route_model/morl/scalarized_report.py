"""The readable summary of a scalarized run: training_curves.png and summary.md, from its files alone."""
from __future__ import annotations

import json
from pathlib import Path

# Categorical slots 1 and 2 of the project's chart palette: policy and exact optimum.
POLICY, OPTIMUM = "#2a78d6", "#eb6834"
SURFACE, TEXT, MUTED, GRID = "#fcfcfb", "#0b0b0b", "#52514e", "#e6e5e1"


def read_json(path):
    return json.loads(Path(path).read_text(encoding="utf-8")) if Path(path).exists() else None


def read_metrics(run_dir):
    """(train rows, val rows, warm-start rows), by update; after a resume the later record of an update wins."""
    records = [json.loads(line) for line in (Path(run_dir) / "metrics.jsonl").read_text(encoding="utf-8").splitlines()]
    latest = lambda phase: sorted({r["update"]: r for r in records if r.get("phase") == phase}.values(),
                                  key=lambda r: r["update"])
    return latest("train"), latest("val"), [r for r in records if r.get("phase") == "warm_start"]


def _ratio_rows(by_alpha):
    rows = ["| α | β | Reached | Mean return | T/T_ref | E/E_ref | Optimal T/T_ref | Optimal E/E_ref |",
            "|---:|---:|---:|---:|---:|---:|---:|---:|"]
    number = lambda value, digits=3: "–" if value is None else f"{value:.{digits}f}"
    for name, row in sorted(by_alpha.items(), key=lambda item: float(item[0])):
        rows.append(f"| {name} | {1 - float(name):g} | {row['success_rate']:.1%} | {number(row['mean_return'])} "
                    f"| {number(row['time_ratio'])} | {number(row['exposure_ratio'])} "
                    f"| {number(row.get('oracle_time_ratio'))} | {number(row.get('oracle_exposure_ratio'))} |")
    return rows


def _prior_rows(policy, prior):
    rows = ["| α | Policy reached | Prior reached | Policy return | Prior return | Policy T/T_ref, E/E_ref | Prior T/T_ref, E/E_ref |",
            "|---:|---:|---:|---:|---:|---:|---:|"]
    pair = lambda row: "–" if row["time_ratio"] is None else f"{row['time_ratio']:.3f}, {row['exposure_ratio']:.3f}"
    for name, base in sorted(prior["by_alpha"].items(), key=lambda item: float(item[0])):
        row = policy["by_alpha"][name]
        rows.append(f"| {name} | {row['success_rate']:.1%} | {base['success_rate']:.1%} | {row['mean_return']:.3f} "
                    f"| {base['mean_return']:.3f} | {pair(row)} | {pair(base)} |")
    return rows


def write_summary(run_dir):
    run_dir = Path(run_dir)
    manifest = read_json(run_dir / "manifest.json")
    evaluation = read_json(run_dir / "evaluation.json")
    verification = read_json(run_dir / "verification.json")
    best, progress = manifest["best"], manifest["progress"]
    lines = [f"# {manifest['run_id']}", "",
             f"- **Status:** `{manifest['status']}`: {manifest['stop_reason']}",
             f"- **Algorithm:** `{manifest['algorithm']}`; seed {manifest['seed']}; "
             f"{manifest.get('parameters', 0):,} parameters; config SHA-256 `{manifest['config_sha256'][:12]}`",
             f"- **Code:** commit `{manifest['code']['commit']}`, uncommitted changes: {manifest['code']['dirty']}",
             f"- **Graph:** `{manifest['data'].get('graph_version')}`, {manifest['data'].get('mode')}; "
             f"fields manifest SHA-256 `{manifest['data'].get('fields', {}).get('manifest_sha256', '')[:12]}`; "
             f"OD set SHA-256 `{manifest['data'].get('od_pairs_sha256', '')[:12]}`",
             f"- **Progress:** {progress['updates']} updates, {progress['env_steps']:,} environment steps, "
             f"{progress['episodes']:,} episodes"]
    if best:
        selection = manifest["checkpoint_selection"]
        lines += [f"- **Selection:** {selection['metric']}, split `{selection['split']}`, "
                  f"α ∈ {selection.get('alphas')}; test split loaded: {selection['test_split_loaded']}",
                  f"- **Selected checkpoint:** update {best['update']}, validation return "
                  f"{best['val_mean_return']:.4f}, reached {best['val_success_rate']:.1%}, "
                  f"SHA-256 `{best['sha256'][:12]}`"]
        if best["update"] == 0:
            lines.append("- **Note:** the selected checkpoint is the warm start. "
                         "PPO on the scalarized reward did not improve validation return.")
    if verification:
        reload = verification["reload"]
        lines.append(f"- **Reload check:** checksum matches: {verification['checkpoint_matches_manifest']}; "
                     f"{reload['routes']} routes identical across two loads: {reload['identical']}; "
                     f"median {verification['route_seconds_cpu']['median']} s per route on CPU")
    if evaluation:
        summary = evaluation["summary"]
        lines += ["", "## Validation, real fields", "",
                  f"{summary['episodes']} episodes; reached {summary['success_rate']:.1%}; "
                  f"optimality gap {summary.get('mean_optimality_gap', 0):.2%}. These episodes also selected the "
                  "checkpoint, so the numbers are slightly optimistic; TI-AI-10 reports on test.", "",
                  *_ratio_rows(summary["by_alpha"])]
        perturbed = evaluation.get("perturbed_fields")
        if perturbed:
            lines += ["", "## Validation, synthetically perturbed fields (not used for selection)", "",
                      *_ratio_rows(perturbed["summary"]["by_alpha"])]
    prior = verification.get("regret_prior") if verification else None
    if evaluation and prior:
        lines += ["", "## Compared with the regret prior", "",
                  "The prior is greedy on alpha * time regret + beta * exposure regret, with no learned part: it uses "
                  "the two cost-to-go searches alone. The difference is what training contributed.", "",
                  "Real fields:", "", *_prior_rows(evaluation["summary"], prior["real_fields"])]
        if prior.get("perturbed_fields") and evaluation.get("perturbed_fields"):
            lines += ["", "Perturbed fields:", "",
                      *_prior_rows(evaluation["perturbed_fields"]["summary"], prior["perturbed_fields"])]
    path = run_dir / "summary.md"
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return path


def _trade_off(axis, by_alpha, title):
    rows = sorted(by_alpha.items(), key=lambda item: float(item[0]))
    reached = [(name, row) for name, row in rows if row.get("time_ratio") is not None]
    axis.plot([r.get("oracle_time_ratio") for _, r in rows], [r.get("oracle_exposure_ratio") for _, r in rows],
              marker="o", markersize=5, linewidth=2, color=OPTIMUM, label="Exact optimum")
    axis.plot([r["time_ratio"] for _, r in reached], [r["exposure_ratio"] for _, r in reached],
              marker="o", markersize=5, linewidth=2, color=POLICY, label="Policy")
    for name, row in reached:
        if float(name) in (0.0, 0.5, 1.0):
            axis.annotate(f"α={name}", (row["time_ratio"], row["exposure_ratio"]), textcoords="offset points",
                          xytext=(5, 5), fontsize=8, color=MUTED)
    axis.set(title=title, xlabel="Time relative to the fastest route", ylabel="Exposure relative to the fastest route")
    axis.legend(frameon=False, fontsize=8)


def plot_curves(run_dir):
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt

    run_dir = Path(run_dir)
    train, val, _ = read_metrics(run_dir)
    manifest, evaluation = read_json(run_dir / "manifest.json"), read_json(run_dir / "evaluation.json")
    figure, axes = plt.subplots(2, 4, figsize=(17, 7.5), facecolor=SURFACE)
    series = [("Validation return (fastest route = −1)", val, "mean_return"),
              ("Validation share of routes reached", val, "success_rate"),
              ("Training return, perturbed and real fields", train, "train_mean_return"),
              ("Entropy", train, "entropy"), ("Policy loss", train, "policy_loss"), ("Value loss", train, "value_loss")]
    for axis, (title, rows, key) in zip(axes.flat, series):
        rows = [r for r in rows if r.get(key) is not None]
        axis.plot([r["update"] for r in rows], [r[key] for r in rows], color=POLICY, linewidth=2,
                  marker="o" if rows is val or len(rows) < 30 else None, markersize=4)
        axis.set(title=title, xlabel="PPO update")
    if manifest["best"]:
        for axis in axes.flat[:2]:
            axis.axvline(manifest["best"]["update"], color=MUTED, linewidth=1, linestyle="--")
            axis.annotate("selected", (manifest["best"]["update"], 1), xycoords=("data", "axes fraction"),
                          textcoords="offset points", xytext=(4, -12), fontsize=8, color=MUTED)
    if evaluation:
        _trade_off(axes.flat[6], evaluation["summary"]["by_alpha"], "Selected checkpoint, real validation fields")
        if evaluation.get("perturbed_fields"):
            _trade_off(axes.flat[7], evaluation["perturbed_fields"]["summary"]["by_alpha"],
                       "Selected checkpoint, perturbed validation fields")
    for axis in axes.flat:
        axis.set_facecolor(SURFACE)
        axis.grid(color=GRID, linewidth=0.8)
        axis.set_axisbelow(True)
        axis.title.set(fontsize=10, color=TEXT)
        axis.xaxis.label.set(fontsize=8, color=MUTED)
        axis.yaxis.label.set(fontsize=8, color=MUTED)
        axis.tick_params(colors=MUTED, labelsize=8, length=0)
        for side, spine in axis.spines.items():
            spine.set_visible(side in ("left", "bottom"))
            spine.set_color(GRID)
    figure.suptitle(f"{manifest['run_id']} ({manifest['status']})", fontsize=11, color=TEXT)
    figure.tight_layout()
    path = run_dir / "training_curves.png"
    figure.savefig(path, dpi=130, facecolor=SURFACE)
    plt.close(figure)
    return path
