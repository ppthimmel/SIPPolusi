"""Reusable ds-v0.3.0 correction training. No notebook execution at runtime."""
import os
os.environ.setdefault("CUBLAS_WORKSPACE_CONFIG", ":4096:8")
import json
import random
from pathlib import Path
from dataclasses import asdict
import torch
import numpy as np
import pandas as pd
from torch import nn
from spatial_model.configs import DEFAULT_SEED, load_model_config
from .model import ModelConfig, STGNN
from .preprocessing import Preprocessor
from .sensors import IDW_POWER, IDW_NEIGHBORS

def train(snapshot, output, *, protocol="fixed_split", fold=None, seed=DEFAULT_SEED,
          epochs=100, device="cpu", batch_size=8, threads=2, resume=False, evaluate_test=True):
    DATA_DIR, OUTPUT_DIR = Path(snapshot), Path(output)
    if epochs < 1 or batch_size < 1 or threads < 1:
        raise ValueError("epochs, batch_size and threads must be positive")
    if OUTPUT_DIR.exists() and not resume:
        raise FileExistsError("Use a new output directory, or --resume for an interrupted run")
    TARGET, PROTOCOL, FOLD, SEED = "pm25", protocol, fold, seed
    DEVICE, EPOCHS, BATCH_SIZE = device, epochs, batch_size
    config = load_model_config("stgnn")
    optimizer_config = config["optimizer"]
    LEARNING_RATE = optimizer_config["learning_rate"]
    WEIGHT_DECAY = optimizer_config["weight_decay"]
    PATIENCE = optimizer_config["patience"]
    GRADIENT_CLIP = optimizer_config["gradient_clip"]
    random.seed(seed)
    np.random.seed(seed)
    torch.manual_seed(seed)
    torch.set_num_threads(threads)
    torch.use_deterministic_algorithms(True)
    if torch.cuda.is_available():
        torch.cuda.manual_seed_all(seed)
        torch.backends.cudnn.benchmark = False
    from .data import prepare, make_batch as build_batch
    data = prepare(snapshot, protocol, fold)
    hashes = data.hashes
    split_labels = data.split_labels
    split_hours = data.split_hours
    training_nodes = data.training_nodes
    hours = data.hours
    hour_index = data.hour_index
    window = data.window
    temporal_features = data.temporal_features
    local_features = data.local_features
    skipped_training_labels = data.skipped_training_labels
    source_ids = data.source_ids
    model_config = ModelConfig(**config["model"])
    model = STGNN(model_config).to(DEVICE)
    # Zero correction starts from exactly IDW.
    nn.init.zeros_(model.regression[-1].weight)
    nn.init.zeros_(model.regression[-1].bias)
    print("Correction model parameters:", sum(p.numel() for p in model.parameters()))
    train_labels = split_labels["train"]
    first = hour_index[train_labels.time_utc.min()] - window + 1
    last = hour_index[train_labels.time_utc.max()] + 1
    fit_times = [str(hours[first]), str(hours[last - 1])]
    preprocessing = Preprocessor().fit(
        temporal_features[first:last, training_nodes],
        local_features[first:last, training_nodes],
        train_labels.value_ugm3.to_numpy())
    print("Feature fit hours:", fit_times)
    print("Target mean/std:", preprocessing.target_mean, preprocessing.target_scale)
    def make_batch(hours, split):
        return build_batch(data, preprocessing, hours, split, DEVICE)
    @torch.no_grad()
    def validation_rmse():
        model.eval()
        squared_error, count = 0.0, 0
        for start in range(0, len(split_hours["val"]), BATCH_SIZE):
            batch = make_batch(split_hours["val"][start:start + BATCH_SIZE], "val")
            output = model(batch["temporal"], batch["local"], batch["edge_index"])
            z = output[batch["label_batch"], batch["label_node"], 0].cpu().numpy()
            predicted = np.maximum(batch["idw_values"].cpu().numpy() + z * preprocessing.target_scale, 0)
            observed = batch["labels"].value_ugm3.to_numpy()
            if not np.isfinite(predicted).all():
                raise ValueError("Nonfinite validation predictions")
            squared_error += float(np.sum((predicted - observed) ** 2))
            count += len(observed)
        return (squared_error / count) ** 0.5
    OUTPUT_DIR.mkdir(parents=True, exist_ok=resume)
    optimizer = torch.optim.AdamW(model.parameters(), lr=LEARNING_RATE, weight_decay=WEIGHT_DECAY)
    scheduler = torch.optim.lr_scheduler.ReduceLROnPlateau(optimizer, **config["scheduler"])
    rng = np.random.default_rng(SEED)
    history = []
    best_rmse = validation_rmse()
    best_epoch, stale = 0, 0
    best_weights = {k: v.detach().cpu().clone() for k, v in model.state_dict().items()}
    print(f"Plain IDW validation RMSE: {best_rmse:.4f}")
    def save_progress():
        pd.DataFrame(history).to_csv(OUTPUT_DIR / "history.csv", index=False)
        torch.save(dict(artifact_version="idw_residual_v1", snapshot_hashes=hashes, configuration=config,
            protocol=PROTOCOL, fold=FOLD, seed=SEED, epoch=epoch,
            model_state=model.state_dict(), best_weights=best_weights,
            optimizer=optimizer.state_dict(), scheduler=scheduler.state_dict(),
            rng=json.dumps(rng.bit_generator.state), torch_rng=torch.get_rng_state(),
            cuda_rng=torch.cuda.get_rng_state_all() if torch.cuda.is_available() else [],
            history=history, best_rmse=best_rmse, best_epoch=best_epoch, stale=stale),
            OUTPUT_DIR / "progress.pt")

    start_epoch = 0
    if resume:
        state = torch.load(OUTPUT_DIR / "progress.pt", map_location="cpu", weights_only=True)
        if (state["snapshot_hashes"], state["protocol"], state["fold"], state["seed"]) != (hashes, PROTOCOL, FOLD, SEED):
            raise ValueError("Resume data/configuration mismatch")
        if state.get("configuration", config) != config:
            raise ValueError("Resume model/optimizer configuration mismatch")
        model.load_state_dict(state["model_state"])
        best_weights, best_rmse, best_epoch, stale = (state[k] for k in ("best_weights", "best_rmse", "best_epoch", "stale"))
        optimizer.load_state_dict(state["optimizer"])
        scheduler.load_state_dict(state["scheduler"])
        rng.bit_generator.state = json.loads(state["rng"])
        torch.set_rng_state(state["torch_rng"])
        if state["cuda_rng"] and torch.cuda.is_available():
            torch.cuda.set_rng_state_all(state["cuda_rng"])
        history, start_epoch = state["history"], state["epoch"]

    epoch = start_epoch
    save_progress()
    for epoch in range(start_epoch + 1, EPOCHS + 1):
        if stale >= PATIENCE:
            break
        model.train()
        train_hours = rng.permutation(split_hours["train"]).tolist()
        loss_sum, count = 0.0, 0

        for start in range(0, len(train_hours), BATCH_SIZE):
            batch = make_batch(train_hours[start:start + BATCH_SIZE], "train")
            optimizer.zero_grad(set_to_none=True)
            output = model(batch["temporal"], batch["local"], batch["edge_index"])
            prediction = output[batch["label_batch"], batch["label_node"], 0]
            target = (batch["label_values"] - batch["idw_values"]) / preprocessing.target_scale
            loss = ((prediction - target) ** 2).mean()
            if not torch.isfinite(loss):
                raise ValueError("Nonfinite training loss")
            loss.backward()
            nn.utils.clip_grad_norm_(model.parameters(), GRADIENT_CLIP, error_if_nonfinite=True)
            optimizer.step()
            loss_sum += loss.item() * len(target)
            count += len(target)

        rmse = validation_rmse()
        scheduler.step(rmse)
        history.append(dict(epoch=epoch, learning_rate=optimizer.param_groups[0]["lr"], train_mse_standardized=loss_sum / count, validation_rmse=rmse))
        print(f"Epoch {epoch:03d} | train MSE {loss_sum / count:.4f} | val RMSE {rmse:.4f}", flush=True)

        if rmse < best_rmse:
            best_rmse, best_epoch, stale = rmse, epoch, 0
            best_weights = {k: v.detach().cpu().clone() for k, v in model.state_dict().items()}
        else:
            stale += 1
        save_progress()
        if stale >= PATIENCE:
            print("Early stopping")
            break
    metadata = dict(configuration=config, temporal_columns=list(preprocessing.temporal.columns), local_columns=list(preprocessing.local.columns), dataset="ds-v0.3.0", features="forecast and calendar included",
        training_config=dict(epochs=EPOCHS, patience=PATIENCE, batch_size=BATCH_SIZE,
            learning_rate=LEARNING_RATE, weight_decay=WEIGHT_DECAY, gradient_clip=GRADIENT_CLIP),
        fit_times=fit_times, fit_nodes=training_nodes.tolist(),
        estimation_source="idw" if best_epoch == 0 else "idw_stgnn_residual",
        idw_power=IDW_POWER, idw_neighbors=IDW_NEIGHBORS, skipped_training_labels=len(skipped_training_labels),
        sensor_stations=source_ids, sensor_policy="eligible stations; target group excluded from baseline and one-hop context",
        target=TARGET, protocol=PROTOCOL, fold=FOLD,
        snapshot_hashes=hashes, unit="ug/m3", seed=SEED,
        best_epoch=best_epoch, validation_rmse=best_rmse)
    torch.save(dict(artifact_version="idw_residual_v1", model_config=asdict(model_config),
        state_dict=best_weights, preprocessing=preprocessing.state(), metadata=metadata),
        OUTPUT_DIR / "best.pt")
    pd.DataFrame(history).to_csv(OUTPUT_DIR / "history.csv", index=False)
    (OUTPUT_DIR / "preprocessing.json").write_text(json.dumps(preprocessing.state(), indent=2), encoding="utf-8")
    print("Best epoch:", best_epoch)
    print("Saved:", OUTPUT_DIR.resolve())
    skipped_training_labels.to_parquet(OUTPUT_DIR / "skipped_training_labels.parquet", index=False)
    print("Selected source:", metadata["estimation_source"])
    from spatial_model.evaluation.stgnn.evaluate import evaluate
    evaluate(DATA_DIR, OUTPUT_DIR / "best.pt", OUTPUT_DIR,
             splits=("val", "test") if evaluate_test else ("val",),
             device=DEVICE, batch_size=BATCH_SIZE, threads=threads, _data=data)
    from spatial_model.artifacts.bundles import export_bundle
    return export_bundle(OUTPUT_DIR, "pm25", DATA_DIR, metadata)

