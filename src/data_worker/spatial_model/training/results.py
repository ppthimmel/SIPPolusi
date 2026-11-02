"""Pool saved prediction rows without averaging fold-level error metrics."""
import hashlib
import json
from pathlib import Path

import pandas as pd

from spatial_model.evaluation.metrics import compute_metrics


def summarize(run_root, folds, *, splits=("val", "test")):
    """Return metric and coverage tables for explicit fixed/LOSO run directories.

    ``None`` denotes ``fixed-split``; integer folds denote ``fold-N``.
    Validation protocols differ between model families; compare test rows only.
    """
    rows, coverage = [], []
    pooled = {}
    for fold in folds:
        directory = Path(run_root) / ("fixed-split" if fold is None else f"fold-{fold}") / "evaluation"
        for split in splits:
            path = directory / f"{split}_predictions.parquet"
            status = "available" if path.exists() else "missing"
            provenance = directory / "evaluation.json"
            if path.exists():
                status = "unverified"
                if provenance.exists():
                    report = json.loads(provenance.read_text(encoding="utf-8"))
                    checkpoint = directory.parent / ("best.pt" if report.get("model") == "stgnn" else "model.txt")
                    if checkpoint.exists() and split in report.get("splits", []):
                        current = hashlib.sha256(checkpoint.read_bytes()).hexdigest()
                        status = "available" if current == report.get("checkpoint_sha256") else "stale"
                        if report.get("model") == "lightgbm":
                            metadata = directory.parent / "metadata.json"
                            if not metadata.exists() or hashlib.sha256(metadata.read_bytes()).hexdigest() != report.get("metadata_sha256"):
                                status = "stale"
            if not path.exists() and provenance.exists():
                report = json.loads(provenance.read_text(encoding="utf-8"))
                if split in report.get("unavailable_splits", {}):
                    status = "unevaluable"
                metrics_path = directory / "metrics.json"
                if metrics_path.exists():
                    metrics = json.loads(metrics_path.read_text(encoding="utf-8"))
                    if metrics.get(split, {}).get("status") == "unevaluable":
                        status = "unevaluable"
            coverage.append(dict(fold=fold, split=split, status=status))
            if status != "available":
                continue
            frame = pd.read_parquet(path)
            methods = {"model": frame.predicted}
            if "idw_prediction" in frame:
                methods["IDW"] = frame.idw_prediction
            for method, prediction in methods.items():
                rows.append(dict(scope="fold", fold=fold, split=split, method=method,
                                 **compute_metrics(frame.observed, prediction)))
                pooled.setdefault((split, method), []).append(
                    pd.DataFrame({"observed": frame.observed, "predicted": prediction}))
    for (split, method), tables in pooled.items():
        frame = pd.concat(tables, ignore_index=True)
        rows.append(dict(scope="pooled", fold=None, split=split, method=method,
                         **compute_metrics(frame.observed, frame.predicted)))
    return pd.DataFrame(rows), pd.DataFrame(coverage)
