"""Per-row size of run-to-run differences: local CPU seeds against each other, and Colab against local (seed 42).

Usage: python .../per_row.py <controlled output dir> <Colab runs/ copy> <out dir>
"""
import sys
from pathlib import Path

import numpy as np
import pandas as pd

ctrl, colab, out = (Path(a) for a in sys.argv[1:4])
keys = ["fold", "split", "hour", "station"]


def rmse(a, b):
    return float(np.sqrt(np.mean((np.asarray(a) - np.asarray(b)) ** 2)))


rows = []
preds = pd.read_parquet(ctrl / "repo_cpu_predictions.parquet")
for model in ("XGBoost + IDW corr", "LightGBM"):
    wide = preds[preds.model == model].pivot_table(index=keys, columns="seed", values="predicted")
    for seed in (1, 2, 3, 4):
        rows.append(dict(model=model, comparison=f"local CPU seed 42 vs seed {seed}", rmse=rmse(wide[42], wide[seed])))
ev = pd.read_parquet(ctrl / "notebook_cpu_eval.parquet")
for folder, column in (("xgboost_idwcorr", "XGBoost + IDW corr"), ("lightgbm", "LightGBM")):
    parts = [pd.read_parquet(f).assign(fold=int(f.parent.name[5:])) for f in (colab / folder).glob("fold-*/predictions.parquet")]
    joined = pd.concat(parts).merge(ev[keys + [column]], on=keys, validate="one_to_one")
    rows.append(dict(model=column, comparison="Colab (GPU) vs local notebook code CPU seed 42",
                     rmse=rmse(joined.predicted, joined[column]), n=len(joined)))
table = pd.DataFrame(rows)
table.to_csv(out / "per_row.csv", index=False)
print(table.round(3).to_string(index=False))
