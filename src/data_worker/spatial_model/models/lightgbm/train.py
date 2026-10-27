"""NO2 full-feature fitting and native export; evaluation lives in evaluation/."""
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


def train(snapshot, output, *, seed=DEFAULT_SEED, fold=None, evaluate_test=True):
    if Path(output).exists():
        raise FileExistsError("Use a new output directory")
    if seed != DEFAULT_SEED:
        raise ValueError("The validation-selected configuration uses seed 42")
    p = Panel("no2", snapshot)
    config = load_model_config("lightgbm")
    columns = CORE + [c for group in feature_groups("no2").values() for c in group]
    allowed = np.ones(len(p.group), dtype=bool)
    held = None
    if fold is not None:
        folds = pd.read_parquet(Path(snapshot) / "splits/loso_folds.parquet")
        match = folds[(folds.target == "no2") & (folds.fold == fold)]
        if match.empty or match.group_id.nunique() != 1:
            raise ValueError("Unknown NO2 LOSO fold")
        held = match.group_id.iloc[0]
        allowed = p.group != held
    F = spatial_features(p, allowed)
    rows = (p.period == "train")[:, None] & np.isfinite(p.Y) & allowed[None, :]
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
    metadata = dict(configuration=config, training_config=dict(config["hp"], threads=model.n_jobs), target="no2", dataset="ds-v0.3.0", unit="ug/m3", seed=seed,
                    columns=columns, target_transform=config["target"], hyperparameters=config["hp"],
                    snapshot_hashes=verify_snapshot(Path(snapshot)),
                    sensor_stations=p.stations.loc[allowed, "uuid"].tolist(), fold=fold,
                    selection=config["selection"], hyperparameter_origin=config["hyperparameter_origin"],
                    skipped_training_labels=int((~valid).sum()), training_labels=int(valid.sum()))
    (output / "metadata.json").write_text(json.dumps(metadata, indent=2))
    if held is not None:
        from spatial_model.evaluation.lightgbm.evaluate import evaluate
        evaluate(snapshot, output, output, splits=("val", "test") if evaluate_test else ("val",), _panel=p)
    else:
        (output / "metrics.json").write_text(json.dumps({"status": "not_evaluated", "reason": "Serving fit; evaluate separate LOSO checkpoints"}, indent=2))
    return export_bundle(output, "no2", snapshot, metadata)
