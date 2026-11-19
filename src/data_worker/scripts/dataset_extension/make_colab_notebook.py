"""Generate the self-contained Colab training notebook (python make_notebook.py OUT.ipynb).

The notebook needs only the data bundle: every model is defined in the notebook itself, ported from
src/data_worker/spatial_model (Panel/spatial features, LightGBM settings, ST-GNN data, preprocessing,
architecture, training and evaluation).
"""
import json
import sys

cells = []


def md(text):
    cells.append({"cell_type": "markdown", "metadata": {}, "source": text.strip("\n").splitlines(keepends=True)})


def code(text):
    cells.append({"cell_type": "code", "metadata": {}, "execution_count": None, "outputs": [],
                  "source": text.strip("\n").splitlines(keepends=True)})


# ============================================================================================ intro
md("""
# SIPPolusi spatial downscaling models — training on ds-v0.4.0

This notebook trains and compares the models that estimate **PM2.5** and **NO₂** at places without a sensor,
on the **ds-v0.4.0** dataset (13 Sep – 19 Nov 2026, about 3.6× the labels of ds-v0.3.0).

The question it answers: **does any learned model beat plain IDW interpolation?**

It needs only one upload, the data bundle `sippolusi_ds-v0.4.0_bundle.zip`. All model code is in this notebook,
ported from the repository (`src/data_worker/spatial_model`).

| Section | What happens |
|---|---|
| 1. Setup | Settings, packages, the data bundle |
| 2. Data loading | Snapshot check, period and blocks, labels, stations, LOSO folds |
| 3. Preprocessing | What the snapshot already did; station-hour features (IDW, LightGBM, XGBoost); ST-GNN inputs |
| 4. Models | IDW, LightGBM, XGBoost, ST-GNN — definitions and runners |
| 5. PM2.5 | 5.1 Fixed split, 5.2 LOSO — each model in its own cell, then a comparison |
| 6. NO₂ | 6.1 Fixed (temporal-only) split, 6.2 LOSO |
| 7. Summary | Every test result, and whether each model beats IDW |

**Runtime:** choose a GPU (*Runtime → Change runtime type → T4 GPU*) for ST-GNN. Rough times on Colab: IDW
seconds; LightGBM/XGBoost about 0.5–1 min per LOSO fold (PM2.5 has 109 folds, NO₂ 15); ST-GNN several minutes
per split or fold. Start with the default few folds, then widen them.
""")

# ============================================================================================ 1 setup
md("""
## 1. Setup
### 1.1 Settings
Edit these, then run the notebook from top to bottom. Nothing below needs editing.
""")
code('''
# Data bundle: a path on Google Drive, or None to upload it from your computer when 1.3 runs.
BUNDLE_ZIP = "/content/drive/MyDrive/SIPPolusi/sippolusi_ds-v0.4.0_bundle.zip"

# Outputs (on Drive, so a disconnect does not lose finished work). Finished ST-GNN units are skipped on rerun.
RUN_ROOT = "/content/drive/MyDrive/SIPPolusi/runs"

# What to run
RUN_PM25_FIXED = True
RUN_PM25_LOSO = True
RUN_NO2_FIXED = True
RUN_NO2_LOSO = True
RUN_STGNN = True                          # False runs only IDW, LightGBM and XGBoost

# LOSO folds: "all" or a list of fold numbers
TABULAR_LOSO_FOLDS = [0, 1, 2, 3, 4]      # IDW, LightGBM, XGBoost; "all" = 109 PM2.5 / 15 NO2 folds
STGNN_LOSO_FOLDS = [0, 1, 2, 3, 4]        # ST-GNN is the slow one

# ST-GNN training
STGNN_EPOCHS = 100                        # early stopping on validation; 1 epoch is only a smoke test
STGNN_BATCH_SIZE = 8

# XGBoost (not tuned on this dataset yet)
XGB_PARAMS = dict(n_estimators=600, learning_rate=0.03, max_depth=4, min_child_weight=20,
                  subsample=0.8, colsample_bytree=0.5, reg_lambda=5.0, tree_method="hist")
SEED = 42
''')

md("""
### 1.2 Packages
Colab already has PyTorch, pandas, scikit-learn, XGBoost and matplotlib. LightGBM is pinned to 4.6.0, the
version the repository uses.
""")
code('''
import subprocess, sys
subprocess.run([sys.executable, "-m", "pip", "install", "-q", "lightgbm==4.6.0", "torch-geometric>=2.5,<3",
                "pyproj>=3.6", "xgboost"], check=True)
''')

md("""
### 1.3 Load the data bundle
The bundle holds one frozen dataset snapshot (`ds-v0.4.0/`) and nothing else.
""")
code('''
import os
os.environ.setdefault("CUBLAS_WORKSPACE_CONFIG", ":4096:8")   # deterministic GPU matrix products
import hashlib, json, random, shutil, time, zipfile
from pathlib import Path

if (BUNDLE_ZIP or "").startswith("/content/drive") or RUN_ROOT.startswith("/content/drive"):
    from google.colab import drive
    drive.mount("/content/drive")
if not BUNDLE_ZIP:
    from google.colab import files
    BUNDLE_ZIP = "/content/" + next(iter(files.upload()))

DATA = Path("/content/data")              # unpacked snapshot (local disk)
CACHE = Path("/content/cache")            # design tables of section 3.3 (local disk)
if not (DATA / "ds-v0.4.0" / "manifest.json").exists():
    with zipfile.ZipFile(BUNDLE_ZIP) as z:
        z.extractall(DATA)
SNAPSHOT = DATA / "ds-v0.4.0"
RUN_ROOT = Path(RUN_ROOT)
RUN_ROOT.mkdir(parents=True, exist_ok=True)
print("snapshot:", SNAPSHOT)
print("outputs: ", RUN_ROOT)
''')

# ============================================================================================ 2 data
md("""
## 2. Data loading
### 2.1 Check the snapshot
Every file's SHA-256 is compared with `CHECKSUMS.sha256`, so a damaged or edited bundle stops here.
""")
code('''
import numpy as np
import pandas as pd
import torch
import matplotlib.pyplot as plt

def verify_snapshot(root):
    root, bad = Path(root), []
    lines = (root / "CHECKSUMS.sha256").read_text(encoding="utf-8").splitlines()
    for line in lines:
        digest, name = line.split(maxsplit=1)
        if hashlib.sha256((root / name).read_bytes()).hexdigest() != digest:
            bad.append(name)
    if bad:
        raise ValueError(f"Snapshot files changed or damaged: {bad}")
    return len(lines)

n_files = verify_snapshot(SNAPSHOT)
manifest = json.loads((SNAPSHOT / "manifest.json").read_text())
split_spec = json.loads((SNAPSHOT / "split_spec.json").read_text())
print("dataset:", manifest["version"], "| files verified:", n_files)
print("GPU:", torch.cuda.get_device_name(0) if torch.cuda.is_available() else "none (CPU)")
pd.DataFrame(split_spec["blocks"]).T
''')

md("""
### 2.2 Labels
One label is the mean of the valid readings of one station in one hour (µg/m³). `period` is the time block.
PM2.5 also has a fixed split (station split × time block); NO₂ uses the time block only.

No readings exist from 11 Nov 04:00 to 15 Nov 06:30 (between the Railway and GCP collectors); labels up to
16 Nov 07:00 are left out so every kept label has a full 24-hour history.
""")
code('''
labels = pd.read_parquet(SNAPSHOT / "labels.parquet")
stations = pd.read_parquet(SNAPSHOT / "stations.parquet")
counts = pd.DataFrame({
    "PM2.5 labels": labels[labels.pm25_ugm3.notna()].groupby("period").size(),
    "PM2.5 stations": labels[labels.pm25_ugm3.notna()].groupby("period").station_uuid.nunique(),
    "NO2 labels": labels[labels.no2_ugm3.notna()].groupby("period").size(),
    "NO2 stations": labels[labels.no2_ugm3.notna()].groupby("period").station_uuid.nunique(),
}).fillna(0).astype(int)
print(f"{len(stations)} stations in {stations.group_id.nunique()} groups;",
      f"labels from {labels.time_window_start.min()} to {labels.time_window_start.max()}")
counts
''')
code('''
fig, axes = plt.subplots(1, 2, figsize=(14, 3.5))
for ax, col, name in zip(axes, ["pm25_ugm3", "no2_ugm3"], ["PM2.5", "NO2"]):
    daily = labels.dropna(subset=[col]).set_index("time_window_start")[col].resample("D").mean()
    daily.plot(ax=ax, title=f"{name}: daily mean over stations (µg/m³); shaded = train, val, test")
    for b in split_spec["blocks"].values():
        ax.axvspan(pd.Timestamp(b["first_window"]), pd.Timestamp(b["end_window_exclusive"]), alpha=0.08)
plt.tight_layout()
''')

md("""
### 2.3 Stations and LOSO folds
A station group is a set of stations closer than 300 m (or duplicates from one provider). Groups are the unit of
the station split and of leave-one-station-out (LOSO): each fold holds out one group.
""")
code('''
loso_folds = pd.read_parquet(SNAPSHOT / "splits/loso_folds.parquet")
print(loso_folds.groupby("target").fold.nunique().rename("LOSO folds").to_string())
colour = stations.station_split.map({"train": "tab:blue", "val": "tab:orange", "test": "tab:red"})
ax = stations.plot.scatter("lng", "lat", c=colour, figsize=(6, 6))
ax.set_title("Stations by fixed-split role (blue train, orange val, red test)")
''')

# ============================================================================================ 3 preprocessing
md("""
## 3. Preprocessing
### 3.1 Already done in the snapshot
These steps are frozen in the bundle and only shown here:

- **Label cleaning:** QC flags, zeros, sentinels (≥ 999.99), stuck series (≥ 12 identical readings), stations
  that copy another station's signal, and incomplete hours are removed (`exclusion_rules.md`).
- **Grid features** per 100 m cell and hour (`stgnn/features.parquet`): GEOS-CF analysis and forecast, Open-Meteo
  weather, Sentinel-5P NO₂, VIIRS night lights, Landsat NDVI/NDBI, calendar. Each is the newest value available
  at prediction time (no look-ahead), with its age in `*_age_hours`.
- **Leakage checks**, all passed (`leakage_report.md`).

VIIRS was not refreshed after 20 Sep (it needs an Earthdata token), so in Oct–Nov the models see the September
value with a growing `ntl_age_hours`. Cloudy weather also makes Landsat NDVI/NDBI older in Oct–Nov.
""")
code('''
print((SNAPSHOT / "leakage_report.md").read_text()[:1200])
''')

md("""
### 3.2 Station-hour panel (for IDW, LightGBM and XGBoost)
These three models work on a table with one row per (station, hour).

- `Panel` holds every label of one pollutant as an (hours × stations) array, plus the grid features of each
  station's cell.
- `spatial_features` builds, for every station-hour, values from the *other* stations that are allowed as
  sensors — never from the station's own group: IDW variants, Gaussian kernels, neighbour statistics, 1–2 hour
  lags, and GEOS-CF fields corrected by the sensors.
- `design` turns selected (hour, station) cells into a table.

Lags are positional over the label hours, so the first hours of a block (and after the 11–16 Nov gap) take their
lag from the previous labelled hour. (Ported from `spatial_model/data/features.py` and `models/lightgbm/data.py`.)
""")
code('''
from pyproj import Transformer

TO_UTM = Transformer.from_crs("EPSG:4326", "EPSG:32748", always_xy=True)
PERIODS = ("train", "val", "test")
GRID_COLUMNS = ["easting", "northing", "geoscf_pm25_ugm3", "geoscf_pm25_age_hours", "geoscf_no2_ugm3",
                "geoscf_no2_age_hours", "geoscf_fcst_pm25_ugm3", "geoscf_fcst_pm25_lead_hours", "geoscf_fcst_no2_ugm3",
                "geoscf_fcst_no2_lead_hours", "temperature_2m", "relative_humidity_2m", "wind_speed_10m",
                "wind_direction_10m", "boundary_layer_height", "precipitation", "surface_pressure",
                "weather_age_hours", "no2_mol_m2", "no2_mol_m2_age_hours", "ntl", "ntl_age_hours", "ndvi",
                "ndvi_age_hours", "ndbi", "ndbi_age_hours", "hour_sin", "hour_cos", "weekday_sin", "weekday_cos"]


class Panel:
    """Dense (hour x station) arrays for one pollutant."""

    def __init__(self, target):
        self.target = target
        st = pd.read_parquet(SNAPSHOT / "stations.parquet").sort_values("kode").reset_index(drop=True)
        st["x"], st["y"] = TO_UTM.transform(st["lng"].to_numpy(), st["lat"].to_numpy())
        self.stations = st
        lab = pd.read_parquet(SNAPSHOT / "labels.parquet")
        lab = lab[lab["period"].isin(PERIODS)]
        self.hours = pd.DatetimeIndex(sorted(lab["time_utc"].unique()))
        hi = pd.Series(np.arange(len(self.hours)), index=self.hours)
        si = pd.Series(st.index.to_numpy(), index=st["uuid"].to_numpy())
        H, N = len(self.hours), len(st)
        ok = lab[f"{target}_ugm3"].notna()
        self.Y = np.full((H, N), np.nan)
        self.Y[hi[lab[ok]["time_utc"]].to_numpy(), si[lab[ok]["station_uuid"].astype(str)].to_numpy()] = \\
            lab[ok][f"{target}_ugm3"]
        self.period = lab.drop_duplicates("time_utc").set_index("time_utc")["period"].reindex(self.hours) \\
            .astype(str).to_numpy()
        # fixed-split role of each labelled station-hour ('' outside the split)
        self.split = np.full((H, N), "", dtype=object)
        sc = f"{target}_split"
        r2 = lab[ok & lab[sc].notna() & (lab[sc].astype(str) != "")]
        self.split[hi[r2["time_utc"]].to_numpy(), si[r2["station_uuid"].astype(str)].to_numpy()] = r2[sc].astype(str)
        self.group = st["group_id"].to_numpy()
        gcode = pd.factorize(self.group)[0]
        self.station_split = st["station_split"].to_numpy()
        self.is_ref = (st["station_type"] == "Reference").to_numpy()
        xy = st[["x", "y"]].to_numpy()
        self.D = np.sqrt(((xy[:, None, :] - xy[None, :, :]) ** 2).sum(-1))
        self.same_group = gcode[:, None] == gcode[None, :]
        self.G = self._grid_features()

    def _grid_features(self):
        f = pd.read_parquet(SNAPSHOT / "stgnn/features.parquet", columns=["time_utc", "node_index", *GRID_COLUMNS])
        nodes = self.stations["node_index"].to_numpy()
        f = f[f["node_index"].isin(nodes) & f["time_utc"].isin(self.hours)]
        hi = pd.Series(np.arange(len(self.hours)), index=self.hours)[f["time_utc"]].to_numpy()
        out = {}
        for c in GRID_COLUMNS:
            per_node = np.full((len(self.hours), int(nodes.max()) + 1), np.nan)
            per_node[hi, f["node_index"].to_numpy()] = f[c].to_numpy(dtype=float)
            out[c] = per_node[:, nodes]
        th = np.deg2rad(out.pop("wind_direction_10m"))
        out["wind_u"] = -out["wind_speed_10m"] * np.sin(th)
        out["wind_v"] = -out["wind_speed_10m"] * np.cos(th)
        return out


def _idw(dist, val, power, k=None):
    """dist/val sorted ascending along the last axis; inf/NaN for unavailable sources."""
    if k is not None:
        dist, val = dist[..., :k], val[..., :k]
    w = np.where(np.isfinite(dist), 1.0 / np.maximum(dist, 1.0) ** power, 0.0)
    s = w.sum(-1)
    return np.where(s > 0, (w * np.nan_to_num(val)).sum(-1) / np.where(s > 0, s, 1), np.nan)


def spatial_features(p, allowed):
    """Sensor-derived features for every (hour, station) from allowed stations outside the own group."""
    H, N = p.Y.shape
    src = np.isfinite(p.Y) & allowed[None, :]
    mask = src[:, None, :] & ~p.same_group[None, :, :]                  # (hour, target, source)
    Dm = np.where(mask, p.D[None], np.inf)
    order = np.argsort(Dm, axis=2, kind="stable")
    ds = np.take_along_axis(Dm, order, 2)
    vs = np.where(np.isfinite(ds), np.take_along_axis(np.broadcast_to(p.Y[:, None, :], (H, N, N)), order, 2), np.nan)
    g = p.G[f"geoscf_fcst_{p.target}_ugm3"]                             # GEOS-CF forecast at each station cell
    gs = np.take_along_axis(np.broadcast_to(g[:, None, :], (H, N, N)), order, 2)
    refs = np.take_along_axis(np.broadcast_to(p.is_ref[None, None, :].astype(float), (H, N, N)), order, 2)
    with np.errstate(all="ignore"):
        F = {
            "n_src": np.isfinite(ds).sum(-1).astype(float),
            "src_mean": np.nanmean(vs, -1), "src_median": np.nanmedian(vs, -1), "src_std": np.nanstd(vs, -1),
            "src_p25": np.nanpercentile(vs, 25, axis=-1), "src_p75": np.nanpercentile(vs, 75, axis=-1),
            "nn_val": vs[..., 0], "nn_dist": np.where(np.isfinite(ds[..., 0]), ds[..., 0], np.nan),
            "mean_dist_k8": np.nanmean(np.where(np.isfinite(ds[..., :8]), ds[..., :8], np.nan), -1),
            "idw_p2_k8": _idw(ds, vs, 2, 8), "idw_p1_k8": _idw(ds, vs, 1, 8), "idw_p1_all": _idw(ds, vs, 1),
            "idw_p2_all": _idw(ds, vs, 2), "idw_p3_k4": _idw(ds, vs, 3, 4),
            "ref_frac_k8": np.nanmean(np.where(np.isfinite(ds[..., :8]), refs[..., :8], np.nan), -1),
        }
        for bw in (2000, 5000, 10000):
            w = np.where(np.isfinite(ds), np.exp(-0.5 * (ds / bw) ** 2), 0.0)
            F[f"gauss_{bw // 1000}km"] = np.where(w.sum(-1) > 1e-6, (w * np.nan_to_num(vs)).sum(-1)
                                                  / np.maximum(w.sum(-1), 1e-12), np.nan)
            F[f"gauss_wsum_{bw // 1000}km"] = w.sum(-1)
        F["ctm_add_idw"] = g + _idw(ds, vs - gs, 2, 8)                  # GEOS-CF + IDW of its error
        F["ctm_mul_idw"] = g * _idw(ds, vs / gs, 2, 8)                  # GEOS-CF x IDW of its ratio
        F["ctm_grad"] = g - _idw(ds, gs, 2, 8)
        F["idw_minus_median"] = F["idw_p2_k8"] - F["src_median"]
    for name in ("idw_p2_k8", "src_median"):
        a = F[name]
        lag1 = np.vstack([np.full((1, N), np.nan), a[:-1]])
        lag2 = np.vstack([np.full((2, N), np.nan), a[:-2]])
        F[f"{name}_lag1"], F[f"{name}_lag2"], F[f"{name}_d1"] = lag1, lag2, a - lag1
        F[f"{name}_roll3"] = pd.DataFrame(a).rolling(3, min_periods=1).mean().to_numpy()
    return F


def feature_groups(target):
    o = "no2" if target == "pm25" else "pm25"
    return {
        "interp": ["src_mean", "src_median", "src_std", "src_p25", "src_p75", "nn_val", "idw_p1_k8", "idw_p1_all",
                   "idw_p2_all", "idw_p3_k4", "gauss_2km", "gauss_5km", "gauss_10km", "idw_minus_median"],
        "geom": ["n_src", "nn_dist", "mean_dist_k8", "gauss_wsum_2km", "gauss_wsum_5km", "gauss_wsum_10km",
                 "ref_frac_k8"],
        "lag": ["idw_p2_k8_lag1", "idw_p2_k8_lag2", "idw_p2_k8_d1", "idw_p2_k8_roll3", "src_median_lag1",
                "src_median_lag2", "src_median_d1", "src_median_roll3"],
        "ctm_ana": [f"geoscf_{target}_ugm3", f"geoscf_{target}_age_hours", f"geoscf_{o}_ugm3"],
        "ctm_fcst": [f"geoscf_fcst_{target}_ugm3", f"geoscf_fcst_{target}_lead_hours", f"geoscf_fcst_{o}_ugm3",
                     "ctm_add_idw", "ctm_mul_idw", "ctm_grad"],
        "met": ["temperature_2m", "relative_humidity_2m", "wind_speed_10m", "wind_u", "wind_v",
                "boundary_layer_height", "precipitation", "surface_pressure", "weather_age_hours"],
        "sat": ["no2_mol_m2", "no2_mol_m2_age_hours", "ntl", "ntl_age_hours"],
        "land": ["ndvi", "ndbi", "ndvi_age_hours", "ndbi_age_hours"],
        "cal": ["hour_sin", "hour_cos", "weekday_sin", "weekday_cos"],
        "coord": ["easting", "northing"],
    }


def feature_columns(target):
    return ["idw_p2_k8"] + [c for group in feature_groups(target).values() for c in group]


def design(p, F, rows):
    """One row per True cell of rows (hours x stations): keys, label y, and every feature."""
    h, i = np.nonzero(rows)
    cols = {"hour": h, "station": p.stations["kode"].to_numpy()[i], "group": p.group[i], "y": p.Y[h, i]}
    cols.update({k: a[h, i] for k, a in F.items()})
    cols.update({k: a[h, i] for k, a in p.G.items()})
    return pd.DataFrame(cols)


PANELS = {}

def panel(target):
    if target not in PANELS:
        PANELS[target] = Panel(target)
    return PANELS[target]

p = panel("pm25")
print("PM2.5 panel:", p.Y.shape, "(hours x stations),", int(np.isfinite(p.Y).sum()), "labels,",
      len(feature_columns("pm25")), "feature columns")
''')

md("""
### 3.3 Design tables per protocol
For each protocol (and each LOSO fold) the training table and the val/test table are built once, so IDW,
LightGBM and XGBoost see exactly the same rows and columns. Training tables are cached on local disk; val/test
tables stay in memory in `EVAL[(target, protocol)]`.

| Protocol | Sensors used as inputs | Training rows | Scored rows |
|---|---|---|---|
| `fixed_split` (PM2.5) | training stations | training stations, training block | val stations in val block, test stations in test block |
| `temporal_split` (NO₂) | all stations (own group excluded) | all stations, training block | all stations in val / test block |
| `loso` | every group but the held-out one | those groups, training block | the held-out group in val / test block |
""")
code('''
EVAL = {}

def selected_folds(target, fold_list):
    f = loso_folds[loso_folds.target == target].drop_duplicates("fold").sort_values("fold")
    return f if fold_list == "all" else f[f.fold.isin(fold_list)]

def prepare(target, protocol, fold_list=None):
    """Build the training table(s) (cached to disk) and the val/test table of one protocol."""
    p = panel(target)
    units = ([(int(f.fold), f.group_id) for f in selected_folds(target, fold_list).itertuples()]
             if protocol == "loso" else [(None, None)])
    evals = []
    for fold, held in units:
        if protocol == "fixed_split":
            allowed = p.station_split == "train"
            train_rows = (p.split == "train") & np.isfinite(p.Y)
            eval_rows = {s: (p.split == s) & np.isfinite(p.Y) for s in ("val", "test")}
        elif protocol == "temporal_split":
            allowed = np.ones(len(p.group), dtype=bool)
            train_rows = (p.period == "train")[:, None] & np.isfinite(p.Y)
            eval_rows = {s: (p.period == s)[:, None] & np.isfinite(p.Y) for s in ("val", "test")}
        else:
            allowed = p.group != held
            train_rows = (p.period == "train")[:, None] & np.isfinite(p.Y) & allowed[None, :]
            eval_rows = {s: (p.period == s)[:, None] & np.isfinite(p.Y) & (p.group == held)[None, :]
                         for s in ("val", "test")}
        F = spatial_features(p, allowed)
        path = CACHE / target / protocol / ("fixed" if fold is None else f"fold-{fold}") / "train.parquet"
        path.parent.mkdir(parents=True, exist_ok=True)
        design(p, F, train_rows).to_parquet(path, index=False)
        for split, rows in eval_rows.items():
            if rows.any():
                evals.append(design(p, F, rows).assign(split=split, fold=fold))
        if fold is not None:
            print(f"fold {fold} ({held})", end="; ")
    table = pd.concat(evals, ignore_index=True).rename(columns={"y": "observed"})
    EVAL[(target, protocol)] = table
    print(f"\\n{target} {protocol}: {len(units)} unit(s); scored rows:", table.groupby("split").size().to_dict())
    return table

def train_table(target, protocol, fold):
    name = "fixed" if fold is None or pd.isna(fold) else f"fold-{int(fold)}"
    return pd.read_parquet(CACHE / target / protocol / name / "train.parquet")
''')

md("""
### 3.4 ST-GNN inputs
ST-GNN works on a graph of 100 m grid cells (every labelled cell plus one ring of neighbours) and reads the last
24 hours of every grid feature. Its inputs, ported from `models/stgnn/data.py`, `sensors.py` and
`preprocessing.py`:

- **Sensor channels:** for every cell and hour, IDW (p=2, k=8) of the sensors allowed in this protocol, plus its
  spread, nearest distance and source count. Sensors of the target's own group, and any within two cells of a
  node, are excluded so the graph cannot see the answer.
- **Label baseline:** the same IDW at each labelled station, from other groups only. ST-GNN learns a correction on
  top of it: `prediction = max(0, IDW + scale × correction)`.
- **Scaling:** ages and distances are `log1p`-transformed, wind direction becomes sin/cos, and every channel is
  standardised with statistics fitted on training hours and training nodes only. Missing values become 0 plus a
  0/1 "available" channel.
""")
code('''
TEMPORAL_VALUES = ["no2_mol_m2", "ntl", "temperature_2m", "relative_humidity_2m", "wind_speed_10m",
                   "wind_direction_10m", "boundary_layer_height", "precipitation", "surface_pressure",
                   "geoscf_pm25_ugm3", "geoscf_no2_ugm3", "geoscf_fcst_pm25_ugm3", "geoscf_fcst_no2_ugm3",
                   "hour_sin", "hour_cos", "weekday_sin", "weekday_cos"]
TEMPORAL_AGES = ["no2_mol_m2_age_hours", "ntl_age_hours", "geoscf_pm25_age_hours", "geoscf_no2_age_hours",
                 "weather_age_hours", "geoscf_fcst_pm25_lead_hours", "geoscf_fcst_no2_lead_hours"]
LOCAL_COLUMNS = ["ndvi", "ndbi", "ndvi_age_hours", "ndbi_age_hours"]
BASE_TEMPORAL_COLUMNS = TEMPORAL_VALUES + TEMPORAL_AGES
IDW_COLUMNS = ["idw_concentration", "idw_spread", "idw_nearest_m", "idw_source_count"]
TEMPORAL_COLUMNS = BASE_TEMPORAL_COLUMNS + IDW_COLUMNS
IDW_POWER, IDW_NEIGHBORS = 2.0, 8


def idw_features(distances, readings):
    """(targets x sources) distances and one hour of source readings -> IDW mean, spread, nearest, count."""
    d_avail = np.where(np.isfinite(readings)[None, :], distances, np.inf)
    nearest = np.argsort(d_avail, axis=1, kind="stable")[:, :IDW_NEIGHBORS]
    distance = np.take_along_axis(d_avail, nearest, axis=1)
    values = np.where(np.isfinite(readings[nearest]), readings[nearest], 0)
    available, coincident = np.isfinite(distance), distance < 1e-6
    weights = np.zeros_like(distance)
    np.divide(1.0, np.maximum(distance, 1e-6) ** IDW_POWER, out=weights, where=available)
    hits = coincident.any(axis=1)
    weights[hits] = coincident[hits].astype(float)
    wsum = weights.sum(axis=1)
    weights = np.divide(weights, wsum[:, None], out=np.zeros_like(weights), where=wsum[:, None] > 0)
    mean = (weights * values).sum(axis=1)
    spread = np.sqrt((weights * (values - mean[:, None]) ** 2).sum(axis=1))
    result = np.column_stack([mean, spread, distance.min(axis=1), available.sum(axis=1)])
    result[available.sum(axis=1) == 0] = np.nan
    return result.astype("float32")


def transform_values(values, columns):
    values, out = np.asarray(values, dtype=np.float64), []
    for i, name in enumerate(columns):
        x = np.where(np.isfinite(values[..., i]), values[..., i], np.nan)
        if name.endswith("_age_hours") or name == "dist_nearest_sensor_m":
            x = np.log1p(x)
        if name == "wind_direction_10m":
            out.extend([np.sin(np.deg2rad(x)), np.cos(np.deg2rad(x))])
        else:
            out.append(x)
    return np.stack(out, axis=-1)


class FeatureScaler:
    """Standardise with train-fitted statistics; missing -> 0 plus an availability channel."""

    def __init__(self, columns):
        self.columns = list(columns)

    def fit(self, values):
        x = transform_values(values, self.columns).reshape(-1, len(self._names()))
        mask = np.isfinite(x)
        n = np.maximum(mask.sum(axis=0), 1)
        self.mean = np.where(mask, x, 0).sum(axis=0) / n
        self.scale = np.sqrt(np.where(mask, (x - self.mean) ** 2, 0).sum(axis=0) / n)
        self.scale[self.scale < 1e-8] = 1.0
        return self

    def _names(self):
        return [n for c in self.columns for n in (["sin", "cos"] if c == "wind_direction_10m" else [c])]

    def transform(self, values):
        x = transform_values(values, self.columns)
        mask = np.isfinite(x)
        scaled = np.where(mask, (x - self.mean) / self.scale, 0)
        return np.concatenate([scaled, mask.astype(np.float64)], axis=-1).astype("float32")


class Preprocessor:
    def fit(self, temporal, local, targets):
        self.temporal = FeatureScaler(TEMPORAL_COLUMNS).fit(temporal)
        self.local = FeatureScaler(LOCAL_COLUMNS).fit(local)
        y = np.asarray(targets, dtype=float)
        self.target_mean, self.target_scale = float(y.mean()), max(float(y.std()), 1e-8)
        return self

    def transform(self, temporal, local):
        return self.temporal.transform(temporal), self.local.transform(local)


def grid_rc(grid_ids):
    return pd.Series(grid_ids).str.extract(r"^r(\\d+)_c(\\d+)$").astype(int).to_numpy()


def prepare_stgnn(target, protocol, fold=None):
    """Graph, 24-hour inputs, sensor channels and per-split labels with their IDW baseline."""
    st_manifest = json.loads((SNAPSHOT / "stgnn/manifest.json").read_text())
    window = int(st_manifest["window"])
    nodes = pd.read_parquet(SNAPSHOT / "stgnn/nodes.parquet").sort_values("node_index")
    edges = pd.read_parquet(SNAPSHOT / "stgnn/edges.parquet")
    edge_index = torch.tensor(edges[["source_node", "target_node"]].to_numpy().T, dtype=torch.long)
    st = pd.read_parquet(SNAPSHOT / "stations.parquet")
    lab = pd.read_parquet(SNAPSHOT / "labels.parquet")
    tcol = f"{target}_ugm3"
    split_labels = {}
    if protocol == "fixed_split":
        train_st = st[st.station_split == "train"]
        for split in PERIODS:
            t = pd.read_parquet(SNAPSHOT / "splits" / f"{split}.parquet")
            split_labels[split] = t[(t.target == target) & (t.protocol == "fixed_split")].copy()
    else:
        held = str(selected_folds(target, [fold]).group_id.iloc[0])
        train_st = st[st.group_id != held]
        for split in PERIODS:
            eligible = lab.group_id == held if split == "test" else lab.group_id != held
            t = lab[eligible & (lab.period == split) & lab[tcol].notna()].copy()
            split_labels[split] = t.rename(columns={tcol: "value_ugm3"})
    for split, t in split_labels.items():
        t["time_utc"] = pd.to_datetime(t.time_utc, utc=True)
        split_labels[split] = t.sort_values(["time_utc", "station_uuid"]).reset_index(drop=True)

    # grid features as (hours, nodes, channels)
    cols = ["time_utc", "node_index", *BASE_TEMPORAL_COLUMNS, *LOCAL_COLUMNS]
    feats = pd.read_parquet(SNAPSHOT / "stgnn/features.parquet", columns=cols)
    hours = pd.date_range(st_manifest["start_utc"], st_manifest["end_utc_exclusive"], freq="h", inclusive="left")
    index = pd.MultiIndex.from_product([hours, nodes.node_index], names=["time_utc", "node_index"])
    feats = feats.set_index(["time_utc", "node_index"]).reindex(index)
    shape = (len(hours), len(nodes), -1)
    temporal = feats[BASE_TEMPORAL_COLUMNS].to_numpy("float32").reshape(shape)
    local = feats[LOCAL_COLUMNS].to_numpy("float32").reshape(shape)
    hour_index = {t: i for i, t in enumerate(hours)}

    # nodes used to fit the scaler: around training stations, never around excluded ones
    rc = grid_rc(nodes.grid_id)
    def near(grid_id, cells):
        return np.abs(rc - grid_rc([grid_id])[0]).max(axis=1) <= cells
    fit_mask = np.zeros(len(nodes), dtype=bool)
    for g in train_st.grid_id.unique():
        fit_mask |= near(g, 1)
    for g in st[~st.uuid.isin(train_st.uuid)].grid_id.unique():
        fit_mask &= ~near(g, 1)

    # sensor channels and label baseline
    src = train_st.sort_values("uuid").reset_index(drop=True)
    readings = lab[lab.station_uuid.isin(src.uuid)].pivot(index="time_utc", columns="station_uuid", values=tcol)
    readings = readings.reindex(index=hours, columns=src.uuid).to_numpy(dtype=float)
    src_xy = np.column_stack(TO_UTM.transform(src.lng.to_numpy(), src.lat.to_numpy()))
    st_xy = np.column_stack(TO_UTM.transform(st.lng.to_numpy(), st.lat.to_numpy()))
    node_d = np.linalg.norm(nodes[["easting", "northing"]].to_numpy()[:, None] - src_xy[None], axis=-1)
    st_d = np.linalg.norm(st_xy[:, None] - src_xy[None], axis=-1)
    st_d[st.group_id.to_numpy()[:, None] == src.group_id.to_numpy()[None, :]] = np.inf
    src_rc = grid_rc(src.grid_id)
    for g in src.group_id.unique():                       # one GAT hop: hide each group within two cells
        in_g = src.group_id.to_numpy() == g
        sep = np.abs(rc[:, None, :] - src_rc[in_g][None, :, :]).max(axis=-1)
        node_d[np.ix_((sep <= 2).any(axis=1), in_g)] = np.inf
    sensor = np.full((len(hours), len(nodes), len(IDW_COLUMNS)), np.nan, dtype="float32")
    baseline = np.full((len(hours), len(st)), np.nan, dtype="float32")
    for h, r in enumerate(readings):
        sensor[h] = idw_features(node_d, r)
        baseline[h] = idw_features(st_d, r)[:, 0]
    pos = {u: i for i, u in enumerate(st.uuid)}
    for split, t in split_labels.items():
        t["idw_prediction"] = baseline[t.time_utc.map(hour_index).to_numpy("int64"),
                                       t.station_uuid.map(pos).to_numpy("int64")]
        if split == "train":
            split_labels[split] = t[np.isfinite(t.idw_prediction)].reset_index(drop=True)
    return dict(split_labels=split_labels, edge_index=edge_index, hours=hours, hour_index=hour_index,
                window=window, temporal=np.concatenate([temporal, sensor], axis=-1), local=local,
                fit_nodes=np.flatnonzero(fit_mask),
                split_hours={s: t.time_utc.drop_duplicates().tolist() for s, t in split_labels.items()})


def make_batch(data, prep, hours, split, device):
    """Batch of hours: 24-hour inputs of every node, plus the labels of those hours."""
    w, temporal, local, labs = data["window"], [], [], []
    table = data["split_labels"][split]
    for b, hour in enumerate(hours):
        i = data["hour_index"][pd.Timestamp(hour)]
        temporal.append(data["temporal"][i - w + 1:i + 1].transpose(1, 0, 2))
        local.append(data["local"][i])
        labs.append(table[table.time_utc == hour].assign(batch_index=b))
    t, s = prep.transform(np.stack(temporal), np.stack(local))
    lab = pd.concat(labs, ignore_index=True)
    as_t = lambda a, dtype: torch.tensor(a, dtype=dtype, device=device)  # noqa: E731
    return dict(temporal=torch.from_numpy(t).to(device), local=torch.from_numpy(s).to(device),
                edge_index=data["edge_index"].to(device), labels=lab,
                label_batch=as_t(lab.batch_index.to_numpy(), torch.long),
                label_node=as_t(lab.node_index.to_numpy("int64"), torch.long),
                idw=as_t(lab.idw_prediction.to_numpy(), torch.float32),
                y=as_t(lab.value_ugm3.to_numpy(), torch.float32))
''')

# ============================================================================================ 4 models
md("""
## 4. Models
### 4.0 Metrics and results
Metrics (µg/m³ except R²): **RMSE**, **MAE**, **bias** (positive = overestimate), **R²**. Within one protocol,
IDW, LightGBM and XGBoost are scored on the same station-hours. ST-GNN is scored on its own label set and compared
with the IDW it is paired with on those labels.
""")
code('''
RESULTS = []          # one row per (target, protocol, model, split)

def compute_metrics(observed, predicted):
    y, yhat = np.asarray(observed, float), np.asarray(predicted, float)
    ok = np.isfinite(y) & np.isfinite(yhat)
    y, yhat = y[ok], yhat[ok]
    if not len(y):
        return dict(n=0, mae=np.nan, rmse=np.nan, r2=np.nan, bias=np.nan)
    err, ss = yhat - y, float(np.sum((y - y.mean()) ** 2))
    return dict(n=int(len(y)), mae=float(np.mean(np.abs(err))), rmse=float(np.sqrt(np.mean(err ** 2))),
                r2=float(1 - np.sum(err ** 2) / ss) if ss > 0 else np.nan, bias=float(err.mean()))

def record(target, protocol, model, table, column):
    for split, part in table.groupby("split"):
        RESULTS.append(dict(target=target, protocol=protocol, model=model, split=split,
                            **compute_metrics(part.observed, part[column])))

def show(target, protocol):
    r = pd.DataFrame(RESULTS)
    r = r[(r.target == target) & (r.protocol == protocol)]
    return r.pivot_table(index="model", columns="split", values=["rmse", "mae", "r2", "n"]).round(3)
''')

md("""
### 4.1 IDW
No training: the estimate at a station is the inverse-distance-weighted mean of the allowed sensors in the same
hour.
- **IDW p=2, k=8** — the production fallback.
- **IDW p=1, all stations** — the best interpolator in the ds-v0.3.0 comparison report.
""")
code('''
def run_idw(target, protocol):
    table = EVAL[(target, protocol)]
    for name, column in (("IDW p=2 k=8", "idw_p2_k8"), ("IDW p=1 all", "idw_p1_all")):
        table[name] = table[column]
        record(target, protocol, name, table, name)
''')

md("""
### 4.2 LightGBM
Gradient-boosted trees on every feature group of 3.2, predicting the concentration directly. Hyperparameters per
target are the repository's (`configs/lightgbm.json`), chosen on ds-v0.3.0. One model per fixed split or fold.
""")
code('''
import lightgbm as lgb

LGBM_BASE = dict(n_estimators=400, learning_rate=0.05, num_leaves=15, min_child_samples=50, subsample=0.8,
                 subsample_freq=1, colsample_bytree=0.8, reg_lambda=1.0)
LGBM_HP = {
    "pm25": dict(n_estimators=150, learning_rate=0.02, num_leaves=7, min_child_samples=400, subsample=1.0,
                 colsample_bytree=0.4, reg_lambda=1, min_split_gain=1.0),
    "no2": dict(n_estimators=150, learning_rate=0.02, num_leaves=63, min_child_samples=50, subsample=0.6,
                colsample_bytree=0.4, reg_lambda=50, min_split_gain=0),
}

def fit_predict(target, protocol, name, make):
    """Fit one model per fold on its training table and predict that fold's val/test rows."""
    table, cols = EVAL[(target, protocol)], feature_columns(target)
    table[name] = np.nan
    for fold in table.fold.drop_duplicates():
        train = train_table(target, protocol, fold)
        model = make().fit(train[cols].astype("float64"), train["y"].to_numpy("float32"))
        rows = table.fold.isna() if pd.isna(fold) else table.fold == fold
        table.loc[rows, name] = np.maximum(model.predict(table.loc[rows, cols].astype("float64")), 0)
    record(target, protocol, name, table, name)

def run_lightgbm(target, protocol):
    fit_predict(target, protocol, "LightGBM", lambda: lgb.LGBMRegressor(
        **(LGBM_BASE | LGBM_HP[target]), random_state=SEED, n_jobs=os.cpu_count(),
        deterministic=True, force_row_wise=True, verbose=-1))
''')

md("""
### 4.3 XGBoost
Same features, rows and target as LightGBM, with `XGB_PARAMS` from 1.1.
""")
code('''
import xgboost as xgb

def run_xgboost(target, protocol):
    fit_predict(target, protocol, "XGBoost", lambda: xgb.XGBRegressor(
        **XGB_PARAMS, random_state=SEED, n_jobs=os.cpu_count()))
''')

md("""
### 4.4 ST-GNN
**Architecture** (ported from `models/stgnn/model.py`, settings from `configs/stgnn.json`): each node's 24-hour
history goes through a causal temporal convolution (dilations 1, 2, 4); Landsat land cover goes through a small
MLP; both are fused, passed through one graph-attention layer (GAT, 2 heads) over neighbouring cells, and a final
layer outputs the correction. The last layer starts at zero, so an untrained model equals IDW.
""")
code('''
from dataclasses import dataclass
from torch import nn
from torch.nn import functional as Fnn
from torch_geometric.nn import GATConv

@dataclass(frozen=True)
class ModelConfig:
    temporal_channels: int = 58     # 29 transformed inputs + 29 availability channels
    local_channels: int = 8
    hidden_channels: int = 16
    local_hidden_channels: int = 8
    heads: int = 2
    kernel_size: int = 3
    dilations: tuple = (1, 2, 4)
    dropout: float = 0.2


class CausalConv1d(nn.Conv1d):
    def forward(self, x):
        return super().forward(Fnn.pad(x, (self.dilation[0] * (self.kernel_size[0] - 1), 0)))


class TemporalBlock(nn.Module):
    def __init__(self, width, kernel, dilation, dropout):
        super().__init__()
        self.layers = nn.Sequential(CausalConv1d(width, width, kernel, dilation=dilation), nn.ReLU(), nn.Dropout(dropout),
                                    CausalConv1d(width, width, kernel, dilation=dilation), nn.ReLU(), nn.Dropout(dropout))

    def forward(self, x):
        return Fnn.relu(x + self.layers(x))


class TemporalEncoder(nn.Module):
    """[batch, nodes, hours, channels] -> same shape with hidden channels; convolution over time only."""

    def __init__(self, c):
        super().__init__()
        self.projection = nn.Linear(c.temporal_channels, c.hidden_channels)
        self.blocks = nn.Sequential(*[TemporalBlock(c.hidden_channels, c.kernel_size, d, c.dropout) for d in c.dilations])

    def forward(self, x):
        b, n, hours, _ = x.shape
        h = self.projection(x).reshape(b * n, hours, -1).transpose(1, 2)
        return self.blocks(h).transpose(1, 2).reshape(b, n, hours, -1)


def batch_edges(edge_index, nodes, batches):
    """Disjoint copies of one graph, with one self loop per node in each copy."""
    edges = edge_index[:, edge_index[0] != edge_index[1]]
    loops = torch.arange(nodes, device=edges.device).repeat(2, 1)
    edges = torch.unique(torch.cat([edges, loops], dim=1), dim=1)
    offsets = torch.arange(batches, device=edges.device) * nodes
    return (edges[:, None, :] + offsets[None, :, None]).reshape(2, -1)


class STGNN(nn.Module):
    """Returns the standardised IDW correction [batch, nodes, 1]."""

    def __init__(self, c=ModelConfig()):
        super().__init__()
        self.c = c
        h, s = c.hidden_channels, c.local_hidden_channels
        self.temporal = TemporalEncoder(c)
        self.local = nn.Sequential(nn.Linear(c.local_channels, h), nn.ReLU(), nn.Dropout(c.dropout),
                                   nn.Linear(h, s), nn.ReLU())
        self.fusion = nn.Sequential(nn.Linear(h + s, h), nn.ReLU())
        self.graph = GATConv(h, h // c.heads, heads=c.heads, concat=True, dropout=c.dropout, add_self_loops=False)
        self.norm = nn.LayerNorm(h)
        self.regression = nn.Sequential(nn.Linear(h, 16), nn.ReLU(), nn.Linear(16, 1))

    def forward(self, temporal, local, edge_index):
        b, n = temporal.shape[:2]
        fused = self.fusion(torch.cat([self.temporal(temporal)[:, :, -1], self.local(local)], dim=-1))
        spatial = self.graph(fused.reshape(b * n, -1), batch_edges(edge_index, n, b)).reshape(b, n, -1)
        return self.regression(self.norm(fused + Fnn.elu(spatial)))
''')

md("""
**Training** (ported from `models/stgnn/train.py`): AdamW (lr 3e-4, weight decay 1e-3), learning rate halved
after 3 epochs without improvement, gradient clipping at 1.0, early stopping after 12 epochs without improvement.
The loss is the squared error of the standardised correction `(label − IDW) / scale`. The epoch with the lowest
validation RMSE is kept; epoch 0 (plain IDW) can win.

**Evaluation:** ST-GNN and its paired IDW are scored on the same labels. In LOSO, ST-GNN validates on the groups it
is *not* holding out, so only its **test** score (the held-out group) is an out-of-sample LOSO estimate; its
validation rows are labelled `val (non-held)`.
""")
code('''
DEVICE = "cuda" if torch.cuda.is_available() else "cpu"
OPT = dict(lr=3e-4, weight_decay=1e-3, patience=12, clip=1.0)

def seed_everything(seed):
    random.seed(seed); np.random.seed(seed); torch.manual_seed(seed)
    if torch.cuda.is_available():
        torch.cuda.manual_seed_all(seed)
    torch.use_deterministic_algorithms(True, warn_only=True)

@torch.no_grad()
def stgnn_predict(model, data, prep, split):
    model.eval()
    frames = []
    hours = data["split_hours"][split]
    for start in range(0, len(hours), STGNN_BATCH_SIZE):
        b = make_batch(data, prep, hours[start:start + STGNN_BATCH_SIZE], split, DEVICE)
        z = model(b["temporal"], b["local"], b["edge_index"])[b["label_batch"], b["label_node"], 0]
        f = b["labels"][["station_uuid", "time_utc", "value_ugm3", "idw_prediction"]].copy()
        f["predicted"] = np.maximum(f.idw_prediction.to_numpy() + z.cpu().numpy() * prep.target_scale, 0)
        frames.append(f)
    return pd.concat(frames, ignore_index=True).rename(columns={"value_ugm3": "observed"})

def train_stgnn(target, protocol, fold=None):
    """Train one ST-GNN; return val/test predictions with the paired IDW. Finished units are read back."""
    out = RUN_ROOT / "stgnn" / target / protocol / ("fixed-split" if fold is None else f"fold-{fold}")
    if (out / "predictions.parquet").exists():
        return pd.read_parquet(out / "predictions.parquet")
    seed_everything(SEED)
    data = prepare_stgnn(target, protocol, fold)
    train = data["split_labels"]["train"]
    first = data["hour_index"][train.time_utc.min()] - data["window"] + 1
    last = data["hour_index"][train.time_utc.max()] + 1
    fit = data["fit_nodes"]
    prep = Preprocessor().fit(data["temporal"][first:last, fit], data["local"][first:last, fit],
                              train.value_ugm3.to_numpy())
    model = STGNN().to(DEVICE)
    nn.init.zeros_(model.regression[-1].weight); nn.init.zeros_(model.regression[-1].bias)
    optimizer = torch.optim.AdamW(model.parameters(), lr=OPT["lr"], weight_decay=OPT["weight_decay"])
    scheduler = torch.optim.lr_scheduler.ReduceLROnPlateau(optimizer, factor=0.5, patience=3, min_lr=1e-6)
    rng = np.random.default_rng(SEED)

    def val_rmse():
        p = stgnn_predict(model, data, prep, "val")
        return float(np.sqrt(np.mean((p.predicted - p.observed) ** 2)))

    best_rmse, best_epoch, stale = val_rmse(), 0, 0
    best = {k: v.detach().cpu().clone() for k, v in model.state_dict().items()}
    history = [dict(epoch=0, val_rmse=best_rmse)]
    print(f"{target} {protocol} {'' if fold is None else f'fold {fold} '}| plain IDW val RMSE {best_rmse:.3f}")
    for epoch in range(1, STGNN_EPOCHS + 1):
        if stale >= OPT["patience"]:
            break
        model.train()
        hours = rng.permutation(data["split_hours"]["train"]).tolist()
        for start in range(0, len(hours), STGNN_BATCH_SIZE):
            b = make_batch(data, prep, hours[start:start + STGNN_BATCH_SIZE], "train", DEVICE)
            optimizer.zero_grad(set_to_none=True)
            z = model(b["temporal"], b["local"], b["edge_index"])[b["label_batch"], b["label_node"], 0]
            loss = ((z - (b["y"] - b["idw"]) / prep.target_scale) ** 2).mean()
            loss.backward()
            nn.utils.clip_grad_norm_(model.parameters(), OPT["clip"])
            optimizer.step()
        rmse = val_rmse()
        scheduler.step(rmse)
        history.append(dict(epoch=epoch, val_rmse=rmse))
        print(f"  epoch {epoch:03d} val RMSE {rmse:.3f}")
        if rmse < best_rmse:
            best_rmse, best_epoch, stale = rmse, epoch, 0
            best = {k: v.detach().cpu().clone() for k, v in model.state_dict().items()}
        else:
            stale += 1
    model.load_state_dict(best)
    print(f"  best epoch {best_epoch} (0 = plain IDW), val RMSE {best_rmse:.3f}")
    preds = pd.concat([stgnn_predict(model, data, prep, s).assign(split=s)
                       for s in ("val", "test") if data["split_hours"][s]], ignore_index=True)
    out.mkdir(parents=True, exist_ok=True)
    torch.save(dict(state_dict=best, best_epoch=best_epoch, target_scale=prep.target_scale), out / "best.pt")
    pd.DataFrame(history).to_csv(out / "history.csv", index=False)
    preds.assign(fold=fold).to_parquet(out / "predictions.parquet", index=False)
    return preds.assign(fold=fold)

def run_stgnn(target, protocol, fold_list=None):
    folds = [None] if protocol != "loso" else [int(f) for f in selected_folds(target, fold_list).fold]
    table = pd.concat([train_stgnn(target, protocol, f) for f in folds], ignore_index=True)
    table = table.rename(columns={"predicted": "ST-GNN", "idw_prediction": "IDW paired"})
    if protocol == "loso":
        table["split"] = table.split.replace({"val": "val (non-held)"})
    suffix = f" ({len(folds)} folds)" if protocol == "loso" else ""
    record(target, protocol, "ST-GNN" + suffix, table, "ST-GNN")
    record(target, protocol, "IDW paired with ST-GNN" + suffix, table, "IDW paired")
    return table
''')

# ============================================================================================ 5 PM2.5
md("""
## 5. PM2.5
### 5.1 Fixed split
Stations **and** time are held out together: train = training stations in the training block, validation =
validation stations in the validation block, test = test stations in the test block. Only training stations are
used as sensor inputs.
""")
code('''
if RUN_PM25_FIXED:
    prepare("pm25", "fixed_split")
''')
md("#### 5.1.1 IDW")
code('''
if RUN_PM25_FIXED:
    run_idw("pm25", "fixed_split")
    display(show("pm25", "fixed_split"))
''')
md("#### 5.1.2 ST-GNN")
code('''
if RUN_PM25_FIXED and RUN_STGNN:
    run_stgnn("pm25", "fixed_split")
    display(show("pm25", "fixed_split"))
''')
md("#### 5.1.3 LightGBM")
code('''
if RUN_PM25_FIXED:
    run_lightgbm("pm25", "fixed_split")
    display(show("pm25", "fixed_split"))
''')
md("#### 5.1.4 XGBoost")
code('''
if RUN_PM25_FIXED:
    run_xgboost("pm25", "fixed_split")
    display(show("pm25", "fixed_split"))
''')
md("""
#### 5.1.5 Comparison
Lower RMSE is better. A model beats IDW when its **test** RMSE is below the better IDW row (ST-GNN: below its
paired IDW row).
""")
code('''
if RUN_PM25_FIXED:
    display(show("pm25", "fixed_split"))
''')

md("""
### 5.2 LOSO (leave one station group out)
Each fold holds out one station group, trains on the training block with every other group as sensors, and scores
the held-out group in the validation and test blocks — the closest test of the estimate where there is no sensor.
Scores are pooled over all station-hours of the selected folds.
""")
code('''
if RUN_PM25_LOSO:
    prepare("pm25", "loso", TABULAR_LOSO_FOLDS)
''')
md("#### 5.2.1 IDW")
code('''
if RUN_PM25_LOSO:
    run_idw("pm25", "loso")
    display(show("pm25", "loso"))
''')
md("""
#### 5.2.2 ST-GNN
Uses `STGNN_LOSO_FOLDS`, which can differ from the folds above; compare it with its own paired IDW row.
""")
code('''
if RUN_PM25_LOSO and RUN_STGNN:
    run_stgnn("pm25", "loso", STGNN_LOSO_FOLDS)
    display(show("pm25", "loso"))
''')
md("#### 5.2.3 LightGBM")
code('''
if RUN_PM25_LOSO:
    run_lightgbm("pm25", "loso")
    display(show("pm25", "loso"))
''')
md("#### 5.2.4 XGBoost")
code('''
if RUN_PM25_LOSO:
    run_xgboost("pm25", "loso")
    display(show("pm25", "loso"))
''')
md("#### 5.2.5 Comparison")
code('''
if RUN_PM25_LOSO:
    display(show("pm25", "loso"))
''')

# ============================================================================================ 6 NO2
md("""
## 6. NO₂
Only about 15 station groups measure NO₂, too few to set aside separate validation and test *stations*.
""")
md("""
### 6.1 Fixed split (temporal only)
Train on the training block and score the validation and test blocks, with **all NO₂ stations in every block**.
Each station's inputs still come only from other station groups. This measures how well a model carries over to a
later period (November, rainy season), not to a new place; LOSO (6.2) measures that.
""")
code('''
if RUN_NO2_FIXED:
    prepare("no2", "temporal_split")
''')
md("#### 6.1.1 IDW")
code('''
if RUN_NO2_FIXED:
    run_idw("no2", "temporal_split")
    display(show("no2", "temporal_split"))
''')
md("""
#### 6.1.2 ST-GNN
Not run here: ST-GNN's fixed split needs held-out stations, which NO₂ does not have.
""")
md("#### 6.1.3 LightGBM")
code('''
if RUN_NO2_FIXED:
    run_lightgbm("no2", "temporal_split")
    display(show("no2", "temporal_split"))
''')
md("#### 6.1.4 XGBoost")
code('''
if RUN_NO2_FIXED:
    run_xgboost("no2", "temporal_split")
    display(show("no2", "temporal_split"))
''')
md("#### 6.1.5 Comparison")
code('''
if RUN_NO2_FIXED:
    display(show("no2", "temporal_split"))
''')

md("### 6.2 LOSO")
code('''
if RUN_NO2_LOSO:
    prepare("no2", "loso", TABULAR_LOSO_FOLDS)
''')
md("#### 6.2.1 IDW")
code('''
if RUN_NO2_LOSO:
    run_idw("no2", "loso")
    display(show("no2", "loso"))
''')
md("#### 6.2.2 ST-GNN")
code('''
if RUN_NO2_LOSO and RUN_STGNN:
    run_stgnn("no2", "loso", STGNN_LOSO_FOLDS)
    display(show("no2", "loso"))
''')
md("#### 6.2.3 LightGBM")
code('''
if RUN_NO2_LOSO:
    run_lightgbm("no2", "loso")
    display(show("no2", "loso"))
''')
md("#### 6.2.4 XGBoost")
code('''
if RUN_NO2_LOSO:
    run_xgboost("no2", "loso")
    display(show("no2", "loso"))
''')
md("#### 6.2.5 Comparison")
code('''
if RUN_NO2_LOSO:
    display(show("no2", "loso"))
''')

# ============================================================================================ 7 summary
md("""
## 7. Summary
Test RMSE of every model and its difference from the IDW it has to beat (negative = better than IDW): the better
of the two IDW rows for LightGBM and XGBoost, the paired IDW for ST-GNN.
""")
code('''
results = pd.DataFrame(RESULTS)
results.to_csv(RUN_ROOT / "results.csv", index=False)
for (target, protocol), table in EVAL.items():
    keep = ["split", "fold", "hour", "station", "group", "observed"] + [
        c for c in ("IDW p=2 k=8", "IDW p=1 all", "LightGBM", "XGBoost") if c in table]
    table[keep].to_parquet(RUN_ROOT / f"predictions_{target}_{protocol}.parquet", index=False)

rows = []
for (target, protocol), g in results[results.split == "test"].groupby(["target", "protocol"]):
    best_idw = g[g.model.isin(["IDW p=2 k=8", "IDW p=1 all"])].rmse.min()
    for r in g.itertuples():
        if r.model.startswith("IDW"):
            reference = np.nan                                  # baselines get no verdict
        elif r.model.startswith("ST-GNN"):
            reference = g[g.model == r.model.replace("ST-GNN", "IDW paired with ST-GNN")].rmse.iloc[0]
        else:
            reference = best_idw
        rows.append(dict(target=target, protocol=protocol, model=r.model, n=r.n, test_rmse=round(r.rmse, 3),
                         idw_reference_rmse=round(reference, 3) if pd.notna(reference) else np.nan,
                         vs_idw=round(r.rmse - reference, 3) if pd.notna(reference) else np.nan,
                         beats_idw=bool(r.rmse < reference) if pd.notna(reference) else None))
summary = pd.DataFrame(rows).sort_values(["target", "protocol", "test_rmse"])
summary.to_csv(RUN_ROOT / "summary.csv", index=False)
print("Saved", RUN_ROOT / "results.csv", "and", RUN_ROOT / "summary.csv")
summary
''')
md("""
### Reading the results
- Not comparable with ds-v0.3.0 reports: the blocks differ (train 13 Sep – 28 Oct, val 29 Oct – 5 Nov, test
  6 – 19 Nov) and the station split moved after four DKJ stations were reclassified as Reference.
- LOSO rows cover only the selected folds; set `TABULAR_LOSO_FOLDS` / `STGNN_LOSO_FOLDS` to `"all"` for the full
  estimate.
- The test block is November (rainy season) and includes the days collected on GCP.
- Per-station errors: `EVAL[(target, protocol)]` holds every scored station-hour; group by `station` to see where
  a model helps or hurts.
""")

nb = {"cells": cells, "metadata": {"accelerator": "GPU", "colab": {"provenance": [], "gpuType": "T4"},
                                   "kernelspec": {"display_name": "Python 3", "name": "python3"},
                                   "language_info": {"name": "python"}},
      "nbformat": 4, "nbformat_minor": 0}
with open(sys.argv[1], "w", encoding="utf-8") as f:
    json.dump(nb, f, indent=1, ensure_ascii=False)
print("cells:", len(cells))
