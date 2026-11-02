"""Full-feature pollutant fitting and native export; evaluation lives in evaluation/."""
from __future__ import annotations

import json
from pathlib import Path

import numpy as np
import pandas as pd

from spatial_model.data.features import Panel, spatial_features
from spatial_model.artifacts.bundles import export_bundle
from spatial_model.data.validation import verify_snapshot
from spatial_model.configs import load_model_config, DEFAULT_SEED
from .data import CORE, feature_groups, design, TARGETS
from .model import make_model


def train(snapshot, output, *, seed=DEFAULT_SEED, fold=None, evaluate_test=True, target="no2", protocol=None):
    protocol = protocol or ("loso" if fold is not None else "serving")
    if target not in ("pm25", "no2"):
        raise ValueError("Choose pm25 or no2")
    if protocol not in ("fixed_split", "loso", "serving"):
        raise ValueError("Choose fixed_split, loso, or serving")
    if protocol == "fixed_split" and (target != "pm25" or fold is not None):
        raise ValueError("Fixed split requires PM2.5 and no fold")
    if (protocol == "loso") != (fold is not None):
        raise ValueError("LOSO requires a fold; other protocols require no fold")
    if Path(output).exists():
        raise FileExistsError("Use a new output directory")
    if seed != DEFAULT_SEED:
        raise ValueError("The validation-selected configuration uses seed 42")
    p = Panel(target, snapshot)
    config = load_model_config("lightgbm")
    columns = CORE + [c for group in feature_groups(target).values() for c in group]
    allowed = np.ones(len(p.group), dtype=bool)
    held = None
    if fold is not None:
        folds = pd.read_parquet(Path(snapshot) / "splits/loso_folds.parquet")
        match = folds[(folds.target == target) & (folds.fold == fold)]
        if match.empty or match.group_id.nunique() != 1:
            raise ValueError("Unknown LOSO fold")
        held = match.group_id.iloc[0]
        allowed = p.group != held
    if protocol == "fixed_split":
        allowed = p.station_split == "train"
    F = spatial_features(p, allowed)
    rows = (p.period == "train")[:, None] & np.isfinite(p.Y) & allowed[None, :]
    if protocol == "fixed_split":
        rows = (p.split == "train") & np.isfinite(p.Y)
    d = design(p, F, rows)
    encoded = TARGETS[config["target"]][0](d)
    valid = np.isfinite(encoded)
    if not valid.any():
        raise ValueError("No eligible training labels")
    model = make_model(config["hp"], seed=seed)
    # Own contiguous arrays avoid views into nullable/read-only Parquet buffers.
    training = d.loc[valid, columns].astype("float64").copy()
    target_values = np.array(encoded[valid], dtype="float32", order="C", copy=True)
    model.fit(training, target_values)
    output = Path(output)
    output.mkdir(parents=True, exist_ok=False)
    model.booster_.save_model(str(output / "model.txt"))
    metadata = dict(model_family="lightgbm", protocol=protocol, configuration=config, training_config=dict(config["hp"], threads=model.n_jobs), target=target, dataset="ds-v0.3.0", unit="ug/m3", seed=seed,
                    columns=columns, target_transform=config["target"], hyperparameters=config["hp"],
                    snapshot_hashes=verify_snapshot(Path(snapshot)),
                    sensor_stations=p.stations.loc[allowed, "uuid"].tolist(), fold=fold,
                    selection=config["selection"] if target == "no2" else "Reused configuration; not newly tuned for PM2.5", hyperparameter_origin=config["hyperparameter_origin"],
                    skipped_training_labels=int((~valid).sum()), training_labels=int(valid.sum()))
    (output / "metadata.json").write_text(json.dumps(metadata, indent=2))
    if protocol != "serving":
        from spatial_model.evaluation.lightgbm.evaluate import evaluate
        evaluate(snapshot, output, output, splits=("val", "test") if evaluate_test else ("val",), _panel=p)
    else:
        (output / "metrics.json").write_text(json.dumps({"status": "not_evaluated", "reason": "Serving fit; evaluate separate LOSO checkpoints"}, indent=2))
    return export_bundle(output, target, snapshot, metadata)
