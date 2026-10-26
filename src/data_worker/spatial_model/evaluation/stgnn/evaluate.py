"""Evaluate a selected correction checkpoint without fitting or training."""
import json
import hashlib
from pathlib import Path
import torch
import numpy as np
import pandas as pd
from spatial_model.models.stgnn.model import ModelConfig, STGNN
from spatial_model.models.stgnn.preprocessing import Preprocessor
from spatial_model.models.stgnn.data import prepare, make_batch as build_batch
from spatial_model.evaluation.metrics import compute_metrics

def evaluate(snapshot, checkpoint, output, *, splits=("val", "test"), device="cpu", batch_size=8, threads=2, _data=None):
    if not splits or set(splits) - {"val", "test"}:
        raise ValueError("Evaluation permits only val/test splits")
    if batch_size < 1 or threads < 1:
        raise ValueError("batch_size and threads must be positive")
    torch.set_num_threads(threads)
    checkpoint, OUTPUT_DIR, DEVICE, BATCH_SIZE = Path(checkpoint), Path(output), device, batch_size
    artifact = torch.load(checkpoint, map_location="cpu", weights_only=True)
    if artifact["artifact_version"] != "idw_residual_v1":
        raise ValueError("Unsupported correction checkpoint")
    metadata = artifact["metadata"]
    data = _data if _data is not None else prepare(snapshot, metadata["protocol"], metadata["fold"])
    if data.hashes != metadata["snapshot_hashes"] or data.source_ids != metadata["sensor_stations"]:
        raise ValueError("Checkpoint dataset or sensor policy mismatch")
    model = STGNN(ModelConfig(**artifact["model_config"])).to(DEVICE)
    split_hours = data.split_hours
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    def make_batch(hours, split):
        return build_batch(data, preprocessing, hours, split, DEVICE)
    model.load_state_dict(artifact["state_dict"])
    preprocessing = Preprocessor.from_state(artifact["preprocessing"])
    model.eval()
    prediction_tables = {}

    # Evaluate test only after validation has selected the best model.
    for split in splits:
        split_frames = []
        hours_to_predict = split_hours[split]

        with torch.no_grad():
            for start in range(0, len(hours_to_predict), BATCH_SIZE):
                batch_hours = hours_to_predict[start:start + BATCH_SIZE]
                batch = make_batch(batch_hours, split)
                node_predictions = model(batch["temporal"], batch["local"], batch["edge_index"])
                station_predictions = node_predictions[batch["label_batch"], batch["label_node"], 0]
                correction = station_predictions.cpu().numpy() * preprocessing.target_scale
                concentrations = batch["idw_values"].cpu().numpy() + correction

                if not np.isfinite(concentrations).all():
                    raise ValueError("Nonfinite evaluation predictions")

                columns = ["station_uuid", "time_utc", "node_index", "value_ugm3", "idw_prediction"]
                frame = batch["labels"][columns].rename(columns={"value_ugm3": "observed"}).copy()
                frame["predicted"] = concentrations.clip(min=0)
                frame["clipped"] = concentrations < 0
                frame["estimation_source"] = artifact["metadata"]["estimation_source"]
                split_frames.append(frame)

        prediction_tables[split] = pd.concat(split_frames, ignore_index=True)
    def calculate_metrics(frame):
        return compute_metrics(frame.observed, frame.predicted) | {"clipped_fraction": float(frame.clipped.mean())}
    evaluation_tables = {}
    for split, predictions in prediction_tables.items():
        idw = predictions.copy()
        idw["predicted"] = idw["idw_prediction"]
        idw["clipped"] = False
        idw["estimation_source"] = "idw"

        evaluation_tables[(split, "IDW")] = idw
        evaluation_tables[(split, "IDW-ST-GNN")] = predictions.copy()

    print("IDW-ST-GNN checkpoint source:", artifact["metadata"]["estimation_source"])
    print("If the selected epoch is 0, its correction is zero and both methods are identical.")
    overall_rows = []
    for (split, method), predictions in evaluation_tables.items():
        row = {"split": split, "method": method, "station_uuid": "pooled"}
        row["estimation_source"] = predictions["estimation_source"].iloc[0]
        row.update(calculate_metrics(predictions))
        overall_rows.append(row)

    overall_metrics = pd.DataFrame(overall_rows)
    print(overall_metrics)
    station_rows = []
    for (split, method), predictions in evaluation_tables.items():
        for station_uuid, station_predictions in predictions.groupby("station_uuid", sort=True):
            row = {"split": split, "method": method, "station_uuid": station_uuid}
            row["estimation_source"] = station_predictions["estimation_source"].iloc[0]
            row.update(calculate_metrics(station_predictions))
            station_rows.append(row)

    station_metrics = pd.DataFrame(station_rows)
    station_comparison = station_metrics.pivot(
        index=["split", "station_uuid"],
        columns="method",
        values=["n", "mae", "rmse", "bias", "r2"],
    )
    print(station_comparison)
    for split, predictions in prediction_tables.items():
        predictions.to_parquet(OUTPUT_DIR / f"{split}_predictions.parquet", index=False)
        idw = evaluation_tables[(split, "IDW")]
        idw.to_parquet(OUTPUT_DIR / f"{split}_idw_predictions.parquet", index=False)

    metrics = pd.concat([overall_metrics, station_metrics], ignore_index=True)
    metrics.to_csv(OUTPUT_DIR / "evaluation_metrics.csv", index=False)
    overall_metrics.to_csv(OUTPUT_DIR / "idw_comparison.csv", index=False)
    print("Saved:", OUTPUT_DIR.resolve())
    provenance = dict(model="stgnn", checkpoint_sha256=hashlib.sha256(checkpoint.read_bytes()).hexdigest(),
        snapshot_hashes=data.hashes, protocol=metadata["protocol"], fold=metadata["fold"], splits=list(splits), unit="ug/m3")
    (OUTPUT_DIR / "evaluation.json").write_text(json.dumps(provenance, indent=2), encoding="utf-8")
    return metrics
