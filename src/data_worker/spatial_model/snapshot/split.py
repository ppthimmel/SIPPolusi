"""Grup stasiun, split stasiun berstrata, blok temporal, dan fold leave-one-station-out.

- **Grup stasiun:** stasiun pada sel 100 m yang sama, berjarak < ``max_distance_m``
  (transitif), atau duplikat penyedia (nilai identik pada sebagian besar cap waktu
  bersama) masuk satu grup. Grup adalah satuan split dan satuan fold, sehingga
  stasiun yang berimpit tidak pernah berada di sisi berbeda.
- **Split stasiun:** alokasi sistematis berstrata. Grup diurutkan per strata (tipe
  stasiun, kota administrasi), diacak di dalam strata dengan seed, lalu posisi ke-i
  diberi label menurut pola pecahan (``systematic_labels``), sehingga setiap strata
  terbagi mendekati proporsi train/val/test.
- **Blok temporal:** interval awal time window tanpa tumpang tindih; jam di luar blok
  adalah jeda (``gap``).
"""

from __future__ import annotations

import math

import numpy as np
import pandas as pd

from feature_matrix.grid import nearest_sensor_distance, to_grid_xy

SPLITS = ("train", "val", "test")


# Grup ---------------------------------------------------------------------------------
def station_groups(stations: pd.DataFrame, readings: pd.DataFrame, max_distance_m: float,
                   duplicate_identical_fraction: float, min_shared: int = 48) -> pd.DataFrame:
    """``stations`` (uuid, kode, lat, lng, grid_id, ...) → kolom ``group_id`` dan ``group_reason``.

    ``readings``: pembacaan mentah (station_uuid, metric, ts_utc, value) untuk deteksi
    duplikat penyedia. ``group_id`` = ``G`` + kode terkecil anggota, deterministik.
    """
    st = stations.sort_values("kode").reset_index(drop=True)
    n = len(st)
    parent = list(range(n))
    reasons: dict[tuple[int, int], str] = {}

    def find(i):
        while parent[i] != i:
            parent[i] = parent[parent[i]]
            i = parent[i]
        return i

    def union(i, j, why):
        reasons[(i, j)] = why
        a, b = find(i), find(j)
        if a != b:
            parent[max(a, b)] = min(a, b)

    x, y = to_grid_xy(st.lng, st.lat)
    d = np.hypot(x[:, None] - x[None, :], y[:, None] - y[None, :])
    for i in range(n):
        for j in range(i + 1, n):
            if st.grid_id[i] == st.grid_id[j]:
                union(i, j, "same_cell")
            elif d[i, j] < max_distance_m:
                union(i, j, f"distance<{max_distance_m:g}m")

    uuids = set(st.uuid)
    clean = readings[readings.station_uuid.isin(uuids)]
    for _, frame in clean.groupby("metric"):
        wide = frame.pivot_table(index="ts_utc", columns="station_uuid", values="value", aggfunc="first")
        cols = list(wide.columns)
        values = wide.to_numpy()
        index = {u: k for k, u in enumerate(st.uuid)}
        for a in range(len(cols)):
            for b in range(a + 1, len(cols)):
                shared = ~np.isnan(values[:, a]) & ~np.isnan(values[:, b])
                if shared.sum() >= min_shared and (values[shared, a] == values[shared, b]).mean() >= \
                        duplicate_identical_fraction:
                    i, j = sorted((index[cols[a]], index[cols[b]]))
                    union(i, j, "duplicate_provider")

    roots = [find(i) for i in range(n)]
    st["group_id"] = ["G_" + st.kode[r] for r in roots]
    why = {}
    for (i, j), r in reasons.items():
        why.setdefault(find(i), set()).add(r)
    st["group_reason"] = [",".join(sorted(why.get(r, {"single"}))) if roots.count(r) > 1 else "single"
                          for r in roots]
    return st


# Split stasiun ------------------------------------------------------------------------
def systematic_labels(n: int, fractions: dict[str, float]) -> list[str]:
    """Label posisi 0..n−1: ``test`` dan ``val`` disebar merata, sisanya ``train``.

    Posisi i berlabel ``test`` bila ⌊(i+1)·f_test⌋ > ⌊i·f_test⌋, dan ``val`` bila
    ⌊(i+1)·f_val + ½⌋ > ⌊i·f_val + ½⌋; pergeseran ½ mencegah keduanya jatuh pada
    posisi yang sama untuk f ≤ 0,25.
    """
    ft, fv = fractions["test"], fractions["val"]
    if max(ft, fv) > 0.25:
        raise ValueError("systematic_labels mengasumsikan pecahan val/test ≤ 0,25")
    out = []
    for i in range(n):
        if math.floor((i + 1) * ft) > math.floor(i * ft):
            out.append("test")
        elif math.floor((i + 1) * fv + 0.5) > math.floor(i * fv + 0.5):
            out.append("val")
        else:
            out.append("train")
    return out


def split_groups(stations: pd.DataFrame, fractions: dict[str, float], strata: list[str],
                 seed: int) -> pd.DataFrame:
    """Satu baris per grup: ``group_id``, strata, ``order``, ``station_split``."""
    groups = (stations.sort_values("kode").groupby("group_id", sort=True)
              .agg(station_type=("station_type", lambda s: "Reference" if (s == "Reference").any() else "Sensor"),
                   kota=("kota", "first"), n_stations=("uuid", "size"), kode=("kode", lambda s: ",".join(s)))
              .reset_index())
    rng = np.random.default_rng(seed)
    ordered = []
    for _, g in groups.sort_values([*strata, "group_id"]).groupby(strata, sort=True):
        g = g.sort_values("group_id")
        ordered.append(g.iloc[rng.permutation(len(g))])
    groups = pd.concat(ordered, ignore_index=True)
    groups["order"] = np.arange(len(groups))
    groups["station_split"] = systematic_labels(len(groups), fractions)
    return groups


# Blok temporal ------------------------------------------------------------------------
def period_of(window_start: pd.Series, blocks: dict[str, tuple]) -> pd.Series:
    out = pd.Series("gap", index=window_start.index, dtype="object")
    for name, (start, end) in blocks.items():
        out[(window_start >= pd.Timestamp(start)) & (window_start < pd.Timestamp(end))] = name
    return out


def block_gaps_hours(blocks: dict[str, tuple]) -> dict[str, float]:
    """Jarak (jam) antara time window terakhir satu blok dan time window pertama blok berikutnya."""
    items = sorted(blocks.items(), key=lambda kv: pd.Timestamp(kv[1][0]))
    gaps = {}
    for (a, (_, end_a)), (b, (start_b, _)) in zip(items, items[1:]):
        last_a = pd.Timestamp(end_a) - pd.Timedelta(hours=1)
        gaps[f"{a}->{b}"] = (pd.Timestamp(start_b) - last_a) / pd.Timedelta(hours=1)
    return gaps


# Fold leave-one-station-out -----------------------------------------------------------
def loso_folds(labels: pd.DataFrame, stations: pd.DataFrame, targets: list[str]) -> pd.DataFrame:
    """Satu fold per grup dan target; baris per stasiun yang ditahan."""
    rows = []
    for target in targets:
        valid = labels[labels[f"{target}_ugm3"].notna()]
        counts = valid.groupby("station_uuid").size()
        held = stations[stations.uuid.isin(counts.index)].sort_values(["group_id", "kode"])
        for fold, (group, members) in enumerate(held.groupby("group_id", sort=True)):
            for m in members.itertuples():
                rows.append(dict(target=target, fold=fold, group_id=group, station_uuid=m.uuid, kode=m.kode,
                                 n_labels=int(counts[m.uuid])))
    return pd.DataFrame(rows)


def loso_sensor_distance(nodes: pd.DataFrame, stations: pd.DataFrame) -> pd.DataFrame:
    """``dist_nearest_sensor_m`` per node untuk setiap grup yang ditahan (tanpa stasiun grup itu)."""
    frames = []
    for group in sorted(stations.group_id.unique()):
        keep = stations[stations.group_id != group]
        dist = nearest_sensor_distance(nodes.easting, nodes.northing, keep.lng.to_numpy(), keep.lat.to_numpy())
        frames.append(pd.DataFrame({"held_out_group": group, "node_index": nodes.node_index.to_numpy(),
                                    "dist_nearest_sensor_m": dist.astype("float32")}))
    return pd.concat(frames, ignore_index=True)
