"""Every Colab XGBoost + IDW fold model, predicted locally on the local design rows, vs Colab's saved predictions.

Usage: python .../colab_models_check.py <controlled output dir> <Colab runs/ copy> <out dir>
A match to float32 rounding means the Colab run scored the same station-hours with the same feature values.
"""
import json, sys
from pathlib import Path
import numpy as np, pandas as pd, xgboost as xgb
sys.path.insert(0, str(Path(__file__).resolve().parents[4] / "src/data_worker"))
from spatial_model.models.lightgbm.data import CORE, feature_groups
COLS = CORE + [c for g in feature_groups("no2").values() for c in g]
ctrl, colab, out_dir = Path(sys.argv[1]), Path(sys.argv[2]), Path(sys.argv[3])
ev = pd.read_parquet(ctrl / "notebook_cpu_eval.parquet")
out = []
for d in sorted((colab / "xgboost_idwcorr").glob("fold-*"), key=lambda p: int(p.name[5:])):
    fold = int(d.name[5:])
    m = json.loads((d / "model.json").read_text())
    b = xgb.Booster(); b.load_model(str(d / "model.json"))
    saved = pd.read_parquet(d / "predictions.parquet")
    rows = ev[ev.fold == fold].merge(saved, on=["split", "hour", "station"], validate="one_to_one")
    raw = b.predict(xgb.DMatrix(rows[COLS].astype("float64"), feature_names=COLS))
    diff = np.abs(np.maximum(rows.idw_p2_k8.to_numpy() + raw, 0) - rows.predicted.to_numpy())
    out.append(dict(fold=fold, rows=len(rows), saved_rows=len(saved), version=".".join(map(str, m["version"])),
                    max_abs_diff=float(diff.max())))
t = pd.DataFrame(out); t.to_csv(out_dir / "colab_models_check.csv", index=False)
print(t.to_string(index=False)); print("overall max", t.max_abs_diff.max())
