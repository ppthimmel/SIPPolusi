"""Evaluate a native NO2 model on its held-out sensor group without fitting."""
import hashlib
import json
from pathlib import Path

import lightgbm as lgb
import numpy as np
import pandas as pd

from spatial_model.data.features import Panel, spatial_features
from spatial_model.data.validation import verify_snapshot
from spatial_model.models.lightgbm.data import design
from spatial_model.models.lightgbm.model import predict
from spatial_model.evaluation.metrics import compute_metrics


def evaluate(snapshot, checkpoint, output, *, splits=("val", "test"), _panel=None):
    if not splits or set(splits) - {"val", "test"}:
        raise ValueError("Evaluation permits only val/test splits")
    checkpoint, output = Path(checkpoint), Path(output)
    metadata = json.loads((checkpoint / "metadata.json").read_text(encoding="utf-8"))
    if metadata.get("target") != "no2" or metadata.get("dataset") != "ds-v0.3.0":
        raise ValueError("Require a ds-v0.3.0 NO2 checkpoint")
    fold = metadata.get("fold")
    if fold is None:
        raise ValueError("LOSO evaluation requires a held-out fold checkpoint; serving fit is ineligible")
    hashes = verify_snapshot(Path(snapshot))
    expected = metadata.get("snapshot_hashes")
    # Older exported runs recorded hashes in the bundle rather than metadata.json.
    manifest_path = checkpoint / "bundle.json"
    if not manifest_path.exists():
        manifest_path = checkpoint / "bundle/bundle.json"
    if expected is None and manifest_path.exists():
        expected = json.loads(manifest_path.read_text(encoding="utf-8"))["snapshot_hashes"]
    if expected != hashes:
        raise ValueError("Checkpoint dataset hash mismatch or missing provenance")
    p = _panel if _panel is not None else Panel("no2", snapshot)
    folds = pd.read_parquet(Path(snapshot) / "splits/loso_folds.parquet")
    match = folds[(folds.target == "no2") & (folds.fold == fold)]
    if match.empty or match.group_id.nunique() != 1:
        raise ValueError("Unknown NO2 LOSO fold")
    held = match.group_id.iloc[0]
    allowed = p.group != held
    if metadata["sensor_stations"] != p.stations.loc[allowed, "uuid"].tolist():
        raise ValueError("Checkpoint sensor policy differs from LOSO fold")
    model_path = checkpoint / "model.txt"
    model = lgb.Booster(model_file=str(model_path))
    if model.feature_name() != metadata["columns"]:
        raise ValueError("Checkpoint feature order mismatch")
    features = spatial_features(p, allowed)
    output.mkdir(parents=True, exist_ok=True)
    results, reports = {}, []
    for split in splits:
        rows = (p.period == split)[:, None] & np.isfinite(p.Y) & (p.group == held)[None, :]
        if not rows.any():
            results[split] = {"status": "unevaluable", "reason": "no held-out labels"}
            continue
        frame = design(p, features, rows)
        prediction = predict(model, metadata, frame)
        table = frame[["hour", "station", "group", "y"]].rename(columns={"y": "observed"})
        table = table.assign(predicted=prediction)
        # Keep the historical y column for consumers of existing prediction tables.
        table["y"] = table.observed
        table.to_parquet(output / f"{split}_predictions.parquet", index=False)
        results[split] = compute_metrics(table.observed, prediction)
        reports.append(dict(split=split, method="LightGBM", station_uuid="pooled", **results[split]))
        for station, part in table.groupby("station"):
            station_uuid = p.stations.loc[p.stations.kode == station, "uuid"].iloc[0]
            reports.append(dict(split=split, method="LightGBM", station_uuid=station_uuid,
                                **compute_metrics(part.observed, part.predicted)))
    (output / "metrics.json").write_text(json.dumps(results, indent=2), encoding="utf-8")
    pd.DataFrame(reports).to_csv(output / "evaluation_metrics.csv", index=False)
    provenance = dict(model="lightgbm", checkpoint_sha256=hashlib.sha256(model_path.read_bytes()).hexdigest(),
                      metadata_sha256=hashlib.sha256((checkpoint / "metadata.json").read_bytes()).hexdigest(),
                      snapshot_hashes=hashes, protocol="loso", fold=fold, held_out_group=str(held),
                      splits=list(splits), unit="ug/m3")
    (output / "evaluation.json").write_text(json.dumps(provenance, indent=2), encoding="utf-8")
    return results
