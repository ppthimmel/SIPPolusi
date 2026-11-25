"""Check the notebook's ported code against the repository on ds-v0.4.0.

Usage: python check_notebook_parity.py NOTEBOOK SNAPSHOT_DIR REPO_DATA_WORKER
Compares: tabular spatial features + design table; ST-GNN inputs, labels, IDW baseline, fit nodes, scaled inputs;
an ST-GNN forward pass with identical weights.
"""
import json
import sys
from pathlib import Path

import numpy as np
import pandas as pd

nb_path, snapshot, repo = Path(sys.argv[1]), Path(sys.argv[2]), Path(sys.argv[3])
nb = json.loads(nb_path.read_text(encoding="utf-8"))
ns = {"display": print, "SNAPSHOT": snapshot, "SEED": 42, "STGNN_BATCH_SIZE": 8, "DEVICE": "cpu"}
defs = []
for c in nb["cells"]:
    src = "".join(c["source"])
    if c["cell_type"] != "code":
        continue
    if src.startswith("TEMPORAL_VALUES") or src.startswith("from pyproj") or src.startswith("from dataclasses") \
            or src.startswith("EVAL = {}") or src.startswith("import numpy as np"):
        defs.append(src)
exec("import os, json, hashlib, random\nfrom pathlib import Path", ns)
for src in defs:
    if src.startswith("import numpy as np"):
        src = src.split("n_files = verify_snapshot")[0]
    src = src.replace('p = panel("pm25")\nprint(', 'pass\nif False: print(')
    exec(src, ns)
ns["loso_folds"] = pd.read_parquet(snapshot / "splits/loso_folds.parquet")

sys.path.insert(0, str(repo))
from spatial_model.data import features as repo_features  # noqa: E402
from spatial_model.models.lightgbm.data import design as repo_design, feature_groups as repo_groups  # noqa: E402
from spatial_model.models.stgnn.data import prepare as repo_prepare, make_batch as repo_batch  # noqa: E402
from spatial_model.models.stgnn.preprocessing import Preprocessor as RepoPrep  # noqa: E402
from spatial_model.models.stgnn.model import STGNN as RepoSTGNN, ModelConfig as RepoCfg  # noqa: E402
import torch  # noqa: E402

ok = True
def check(name, cond):
    global ok
    ok &= bool(cond)
    print(("OK   " if cond else "FAIL ") + name)

# --- tabular
for target in ("pm25", "no2"):
    rp, npnl = repo_features.Panel(target, snapshot), ns["Panel"](target)
    allowed = rp.group != rp.group[0]
    RF, NF = repo_features.spatial_features(rp, allowed), ns["spatial_features"](npnl, allowed)
    rows = (rp.period == "test")[:, None] & np.isfinite(rp.Y)
    a, b = repo_design(rp, RF, rows), ns["design"](npnl, NF, rows)
    cols = [c for c in b.columns]
    same = all(np.allclose(a[c].to_numpy(float), b[c].to_numpy(float), equal_nan=True) for c in cols
               if c not in ("station", "group"))
    check(f"{target} design table ({len(b)} rows, {len(cols)} cols)", same and repo_groups(target) == ns["feature_groups"](target))

# --- ST-GNN
for target, protocol, fold in (("pm25", "fixed_split", None), ("no2", "loso", 0)):
    r = repo_prepare(snapshot, protocol, fold, target=target)
    n = ns["prepare_stgnn"](target, protocol, fold)
    check(f"{target} {protocol} temporal inputs", np.allclose(r.temporal_features, n["temporal"], equal_nan=True))
    check(f"{target} {protocol} local inputs", np.allclose(r.local_features, n["local"], equal_nan=True))
    check(f"{target} {protocol} fit nodes", np.array_equal(r.training_nodes, n["fit_nodes"]))
    for s in ("train", "val", "test"):
        a, b = r.split_labels[s], n["split_labels"][s]
        check(f"{target} {protocol} {s} labels + IDW baseline ({len(b)})",
              len(a) == len(b) and np.allclose(a.idw_prediction, b.idw_prediction, equal_nan=True)
              and np.allclose(a.value_ugm3, b.value_ugm3))
    train = r.split_labels["train"]
    first = r.hour_index[train.time_utc.min()] - r.window + 1
    last = r.hour_index[train.time_utc.max()] + 1
    rprep = RepoPrep().fit(r.temporal_features[first:last, r.training_nodes], r.local_features[first:last, r.training_nodes],
                           train.value_ugm3.to_numpy())
    nprep = ns["Preprocessor"]().fit(n["temporal"][first:last, n["fit_nodes"]], n["local"][first:last, n["fit_nodes"]],
                                     train.value_ugm3.to_numpy())
    hours = r.split_hours["val"][:4]
    rb = repo_batch(r, rprep, hours, "val")
    ns["scale_inputs"](n, nprep, "cpu")
    nb_ = ns["make_batch"](n, hours, "val", "cpu")
    check(f"{target} {protocol} batch labels", rb["labels"].reset_index(drop=True)[["station_uuid", "node_index"]]
          .equals(nb_["labels"][["station_uuid", "node_index"]]) and
          torch.equal(rb["label_batch"], nb_["label_batch"]) and torch.allclose(rb["idw_values"], nb_["idw"]))
    check(f"{target} {protocol} scaled batch", torch.allclose(rb["temporal"], nb_["temporal"], atol=1e-5)
          and torch.allclose(rb["local"], nb_["local"], atol=1e-5) and abs(rprep.target_scale - nprep.target_scale) < 1e-9)
    torch.manual_seed(0)
    rm = RepoSTGNN(RepoCfg(**json.loads((repo / "spatial_model/configs/stgnn.json").read_text())["model"])).eval()
    nm = ns["STGNN"]().eval()
    nm.load_state_dict(rm.state_dict())
    with torch.no_grad():
        check(f"{target} {protocol} ST-GNN forward pass", torch.allclose(
            rm(rb["temporal"], rb["local"], rb["edge_index"]), nm(nb_["temporal"], nb_["local"], nb_["edge_index"]),
            atol=1e-5))
print("PARITY OK" if ok else "PARITY FAILED")
sys.exit(0 if ok else 1)
