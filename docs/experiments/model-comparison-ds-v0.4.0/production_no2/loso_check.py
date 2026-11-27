"""Repository-code LOSO check of the NO2 XGBoost + IDW correction on ds-v0.4.0 (CPU).

Usage (with src/data_worker/requirements.txt installed):
    python docs/experiments/model-comparison-ds-v0.4.0/production_no2/loso_check.py <ds-v0.4.0 snapshot> <output dir>

Trains every NO2 LOSO fold with ``train(..., family="xgboost")``, then pools the saved val/test prediction rows
(no averaging of fold metrics) into ``<output dir>/loso_summary.json``. A fold whose held-out group has no labels
in a split is written as unevaluable and contributes no rows.
"""
import json
import sys
from pathlib import Path

import torch  # noqa: F401  (Windows: load before other native runtimes)
import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parents[4] / "src/data_worker"))
from spatial_model.evaluation.metrics import compute_metrics  # noqa: E402
from spatial_model.models.lightgbm.train import train  # noqa: E402

snapshot, output = Path(sys.argv[1]), Path(sys.argv[2])
folds = pd.read_parquet(snapshot / "splits/loso_folds.parquet")
for fold in sorted(folds[folds.target == "no2"].fold.unique()):
    train(snapshot, output / f"fold-{fold}", target="no2", fold=int(fold), family="xgboost")
summary = {}
for split in ("val", "test"):
    parts = [pd.read_parquet(p) for p in sorted(output.glob(f"fold-*/{split}_predictions.parquet"))]
    table = pd.concat(parts, ignore_index=True)
    summary[split] = dict(compute_metrics(table.observed, table.predicted), folds=len(parts))
(output / "loso_summary.json").write_text(json.dumps(summary, indent=2, default=float))
print(json.dumps(summary, indent=2, default=float))
