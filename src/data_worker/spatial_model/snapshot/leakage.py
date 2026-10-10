"""Laporan kebocoran snapshot: delapan pemeriksaan, masing-masing dihitung ulang secara independen."""

from __future__ import annotations

from pathlib import Path

import numpy as np
import pandas as pd

from feature_matrix.grid import nearest_sensor_distance, to_grid_xy

from .split import SPLITS, block_gaps_hours

DIST_TOL_M = 0.01      # dist_nearest_sensor_m disimpan float32


def _check(checks, cid, name, violations, detail):
    checks.append(dict(id=cid, check=name, status="lolos" if violations == 0 else "gagal",
                       violations=int(violations), detail=detail))


def leakage_report(out: Path, cfg: dict, labels: pd.DataFrame, stations: pd.DataFrame,
                   split_frames: dict[str, pd.DataFrame], folds: pd.DataFrame, loso_dist: pd.DataFrame,
                   nodes: pd.DataFrame, node_split: pd.Series, norm: dict, blocks: dict, L: int) -> dict:
    checks: list[dict] = []
    allsplits = pd.concat([f.assign(split=s) for s, f in split_frames.items()], ignore_index=True)

    # 1. Kunci ganda.
    dup_labels = int(labels.duplicated(["station_uuid", "time_window_start"]).sum())
    dup_split = int(allsplits.duplicated(["target", "station_uuid", "time_window_start"]).sum())
    _check(checks, "unique_keys", "Tidak ada kunci (stasiun, time window) ganda", dup_labels + dup_split,
           f"labels.parquet: {dup_labels}; lintas berkas split per target: {dup_split}")

    # 2. Grup stasiun tidak lintas split; fold LOSO menahan grup utuh.
    multi = int((stations.groupby("group_id").station_split.nunique() > 1).sum())
    fixed = allsplits[allsplits.protocol == "fixed_split"]
    cross = int((fixed.groupby(["target", "group_id"]).split.nunique() > 1).sum())
    incomplete = 0
    for (target, group), f in folds.groupby(["target", "group_id"]):
        with_label = set(labels.loc[labels[f"{target}_ugm3"].notna() & (labels.group_id == group), "station_uuid"])
        incomplete += int(set(f.station_uuid) != with_label)
    _check(checks, "groups_disjoint", "Tidak ada grup stasiun lintas split", multi + cross + incomplete,
           f"grup dengan >1 split stasiun: {multi}; grup lintas berkas split (protokol split tetap): {cross}; "
           f"fold LOSO yang tidak menahan seluruh stasiun grupnya: {incomplete}")

    # 3. Jarak antarblok.
    need = max(L, int(cfg["blocks"]["min_gap_hours"]))
    gaps = block_gaps_hours(blocks)
    observed = {}
    order = sorted(blocks, key=lambda b: pd.Timestamp(blocks[b][0]))
    for a, b in zip(order, order[1:]):
        last_a = allsplits.loc[allsplits.split == a, "time_window_start"].max()
        first_b = allsplits.loc[allsplits.split == b, "time_window_start"].min()
        observed[f"{a}->{b}"] = float((first_b - last_a) / pd.Timedelta(hours=1))
    short = sum(v < need for v in [*gaps.values(), *observed.values()])
    _check(checks, "temporal_gap", f"Jarak antarblok temporal ≥ max(L, 24 jam) = {need} jam", short,
           f"batas konfigurasi: {gaps}; teramati pada berkas split: {observed}")

    # 4. Observasi identik lintas split.
    keys_multi = int((allsplits.groupby(["target", "station_uuid", "time_window_start"]).split.nunique() > 1).sum())
    st = stations.sort_values("kode").reset_index(drop=True)
    x, y = to_grid_xy(st.lng, st.lat)
    d = np.hypot(x[:, None] - x[None, :], y[:, None] - y[None, :])
    near = (d < float(cfg["groups"]["max_distance_m"])) | (st.grid_id.to_numpy()[:, None] == st.grid_id.to_numpy())
    diff = st.station_split.to_numpy()[:, None] != st.station_split.to_numpy()
    close_cross = int(np.triu(near & diff, 1).sum())
    dup_cross = 0
    for target in cfg["scope"]["targets"]:
        wide = labels.pivot_table(index="time_window_start", columns="station_uuid", values=f"{target}_ugm3")
        split_of = st.set_index("uuid").station_split
        cols, v = list(wide.columns), wide.to_numpy()
        for i in range(len(cols)):
            for j in range(i + 1, len(cols)):
                if split_of[cols[i]] == split_of[cols[j]]:
                    continue
                shared = ~np.isnan(v[:, i]) & ~np.isnan(v[:, j])
                if shared.sum() >= 48 and (v[shared, i] == v[shared, j]).mean() >= \
                        float(cfg["groups"]["duplicate_identical_fraction"]):
                    dup_cross += 1
    _check(checks, "identical_observations", "Tidak ada observasi identik lintas split",
           keys_multi + close_cross + dup_cross,
           f"kunci (target, stasiun, time window) di >1 split: {keys_multi}; pasangan stasiun berimpit "
           f"(sel sama atau < {cfg['groups']['max_distance_m']:g} m) beda split: {close_cross}; pasangan stasiun "
           f"beda split dengan nilai per jam identik: {dup_cross}")

    # 5. Fitur berbasis sensor tidak memakai stasiun uji/validasi atau stasiun yang ditahan.
    feats = pd.read_parquet(out / "stgnn" / "features.parquet",
                            columns=["node_index", "time_utc", "dist_nearest_sensor_m"])
    stored = feats.groupby("node_index").dist_nearest_sensor_m.agg(["min", "max"]).reindex(nodes.node_index)
    train = st[st.station_split == "train"]
    expect = nearest_sensor_distance(nodes.easting, nodes.northing, train.lng.to_numpy(), train.lat.to_numpy())
    fixed_bad = int(((stored["min"] - expect).abs() > DIST_TOL_M).sum() + ((stored["max"] - expect).abs()
                                                                          > DIST_TOL_M).sum())
    fold_bad = 0
    for group, f in loso_dist.groupby("held_out_group"):
        keep = st[st.group_id != group]
        exp = nearest_sensor_distance(nodes.easting, nodes.northing, keep.lng.to_numpy(), keep.lat.to_numpy())
        got = f.set_index("node_index").dist_nearest_sensor_m.reindex(nodes.node_index).to_numpy()
        fold_bad += int((np.abs(got - exp) > DIST_TOL_M).sum())
    excluded = st[st.station_split != "train"]
    own = nodes.set_index("grid_id").node_index.reindex(excluded.grid_id).to_numpy()
    own_dist = pd.Series(expect, index=nodes.node_index).reindex(own).to_numpy()
    _check(checks, "sensor_features", "Fitur berbasis sensor tidak memakai stasiun uji", fixed_bad + fold_bad,
           f"node dengan dist_nearest_sensor_m ≠ jarak ke stasiun train: {fixed_bad}; nilai fold LOSO ≠ jarak "
           f"tanpa grup yang ditahan: {fold_bad}; jarak minimum dari sel stasiun val/uji ke sensor: "
           f"{np.nanmin(own_dist):.0f} m")

    # 6. Normalisasi dan fitur statis hanya dari train.
    fit = norm["fit_rows"]
    last_train_tau = pd.Timestamp(blocks["train"][1])          # akhir eksklusif awal window = τ terakhir
    late = int(pd.Timestamp(fit["last_time_utc"]) > last_train_tau)
    fit_nodes = set(nodes.node_index[node_split == "train"])
    nontrain_nodes = int(fit["nodes"] > len(fit_nodes))
    target_rows = int(labels.pm25_split.eq("train").fillna(False).sum())
    target_bad = int(norm["targets"]["pm25"]["n"] != target_rows)
    no2_train = labels[labels.no2_split.eq("train").fillna(False)]
    for group, st_ in norm["targets"]["no2"]["per_held_out_group"].items():
        target_bad += int(st_["n"] != int((no2_train.group_id != group).sum()))
    static = pd.read_parquet(out / "stgnn" / "features.parquet",
                             columns=["time_utc", "ndvi", "ndvi_produced_at", "ndbi_produced_at", "ntl",
                                      "ntl_produced_at"])
    future_static = int((static.ndvi_produced_at > static.time_utc).sum() + (static.ndbi_produced_at
                        > static.time_utc).sum() + (static.ntl_produced_at > static.time_utc).sum())
    _check(checks, "train_only_fit", "Scaler dan fitur statis hanya dari data train / data tersedia",
           late + nontrain_nodes + target_bad + future_static,
           f"jam fit terakhir {fit['last_time_utc']} (batas {last_train_tau}); node fit {fit['nodes']} dari "
           f"{len(fit_nodes)} node train; baris target PM2.5 {norm['targets']['pm25']['n']} = split train "
           f"{target_rows}; target NO2 per fold tanpa grup yang ditahan; tidak ada komposit statis yang di-fit (Landsat/VIIRS as-of), nilai dengan "
           f"produced_at > waktu inferensi: {future_static}")

    # 7. GEOS-CF tersedia pada waktu inferensi.
    g = pd.read_parquet(out / "stgnn" / "features.parquet",
                        columns=["time_utc", "geoscf_pm25_ugm3", "geoscf_pm25_available_at_utc",
                                 "geoscf_pm25_time_window_end", "geoscf_no2_ugm3", "geoscf_no2_available_at_utc",
                                 "geoscf_no2_time_window_end"])
    geos_bad = 0
    for var in ("pm25", "no2"):
        ok = g[f"geoscf_{var}_ugm3"].notna()
        geos_bad += int(((g.loc[ok, f"geoscf_{var}_available_at_utc"] > g.loc[ok, "time_utc"])
                         | (g.loc[ok, f"geoscf_{var}_time_window_end"] > g.loc[ok, "time_utc"])
                         | g.loc[ok, f"geoscf_{var}_available_at_utc"].isna()).sum())
    _check(checks, "geoscf_available", "Seluruh fitur GEOS-CF memenuhi available_at_utc <= waktu inferensi",
           geos_bad, f"{int(len(g)):,} baris fitur diperiksa; pelanggaran {geos_bad}".replace(",", "."))

    # 8. Jendela lag tidak melewati batas split.
    lag_bad = 0
    detail = []
    for a, b in zip(order, order[1:]):
        last_tau_a = pd.Timestamp(blocks[a][1])
        rows = allsplits[allsplits.split == b]
        start = rows.time_utc - pd.Timedelta(hours=L - 1)
        n_bad = int((start <= last_tau_a).sum())
        lag_bad += n_bad
        detail.append(f"{b}: jam masukan paling awal {start.min()} > τ terakhir {a} {last_tau_a} "
                      f"({n_bad} sampel melanggar)")
    _check(checks, "lag_window", f"Jendela lag L = {L} time window tidak melewati batas split", lag_bad,
           "; ".join(detail))

    return dict(version=cfg["version"], checks=checks, passed=all(c["status"] == "lolos" for c in checks))


def report_markdown(report: dict) -> str:
    lines = [f"# Laporan kebocoran {report['version']}", "",
             f"Status: **{'lolos' if report['passed'] else 'GAGAL'}** "
             f"({sum(c['status'] == 'lolos' for c in report['checks'])}/{len(report['checks'])} pemeriksaan lolos).",
             "", "| # | Pemeriksaan | Status | Pelanggaran | Rincian |", "|---|---|---|---|---|"]
    for i, c in enumerate(report["checks"], 1):
        lines.append(f"| {i} | {c['check']} | {c['status']} | {c['violations']} | {c['detail']} |")
    return "\n".join(lines) + "\n"


__all__ = ["leakage_report", "report_markdown", "SPLITS"]
