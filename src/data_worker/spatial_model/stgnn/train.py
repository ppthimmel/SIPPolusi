"""Offline training and explicit held-out evaluation; training never reads test targets into loss."""

import json
import platform
import random
import subprocess
import time
from dataclasses import asdict, dataclass
from pathlib import Path

import numpy as np
import pandas as pd
import torch
import torch_geometric

from feature_matrix.prepare_stgnn import stgnn_hash
from spatial_model.baseline.evaluate import compute_metrics
from .dataset import SnapshotData
from .model import ModelConfig, STGNN
from .preprocessing import Preprocessor

ARTIFACT_VERSION = 1


def peak_memory(device):
    if platform.system() == "Windows":
        import psutil
        cpu = psutil.Process().memory_info().peak_wset
    else:
        import resource
        cpu = resource.getrusage(resource.RUSAGE_SELF).ru_maxrss
        if platform.system() != "Darwin":
            cpu *= 1024
    gpu = torch.cuda.max_memory_allocated(device) if str(device).startswith("cuda") else None
    return dict(process_peak_rss_bytes=int(cpu), gpu_peak_allocated_bytes=gpu)


@dataclass(frozen=True)
class TrainingConfig:
    seed: int = 42
    epochs: int = 200
    batch_size: int = 4
    learning_rate: float = 1e-3
    weight_decay: float = 1e-4
    patience: int = 20
    gradient_clip: float = 1.0

    def __post_init__(self):
        if min(self.epochs, self.batch_size, self.patience) < 1:
            raise ValueError("Epochs, batch size and patience must be positive")
        if self.learning_rate <= 0 or self.weight_decay < 0 or self.gradient_clip <= 0:
            raise ValueError("Invalid optimization settings")


def write_json(path, value):
    def clean(x):
        if isinstance(x, dict):
            return {k: clean(v) for k, v in x.items()}
        if isinstance(x, (tuple, list)):
            return [clean(v) for v in x]
        if isinstance(x, float) and not np.isfinite(x):
            return None
        return x
    Path(path).write_text(json.dumps(clean(value), indent=2, allow_nan=False) + "\n", encoding="utf-8")


def provenance():
    package = Path(__file__).resolve().parent
    repo = package.parents[3]
    def git(*args):
        return subprocess.run(["git", "-c", f"safe.directory={repo.as_posix()}", *args],
                              cwd=repo, capture_output=True, text=True).stdout.strip()
    return dict(code_revision=git("rev-parse", "HEAD"),
                dirty=bool(git("status", "--porcelain", "--", "src/data_worker")),
                source_hashes={p.relative_to(package).as_posix(): stgnn_hash(p)
                               for p in sorted(package.rglob("*")) if p.suffix in (".py", ".toml")},
                versions=dict(python=platform.python_version(), torch=str(torch.__version__),
                              torch_geometric=str(torch_geometric.__version__), numpy=np.__version__,
                              pandas=pd.__version__))


def save_checkpoint(path, model, preprocessing, metadata):
    torch.save(dict(artifact_version=ARTIFACT_VERSION, model_config=asdict(model.config),
                    state_dict={k: v.detach().cpu() for k, v in model.state_dict().items()},
                    preprocessing=preprocessing.state(), metadata=metadata), path)


def load_checkpoint(path, device="cpu"):
    artifact = torch.load(path, map_location=device, weights_only=True)
    if artifact.get("artifact_version") != ARTIFACT_VERSION:
        raise ValueError("Unsupported ST-GNN artifact version")
    config = dict(artifact["model_config"])
    config["dilations"] = tuple(config["dilations"])
    model = STGNN(ModelConfig(**config)).to(device)
    model.load_state_dict(artifact["state_dict"])
    model.eval()
    return model, Preprocessor.from_state(artifact["preprocessing"]), artifact["metadata"]


@torch.no_grad()
def predict_raw(model, preprocessing, temporal, local, edge_index, *, temporal_columns, local_columns):
    """Raw arrays [B,N,24,16], [B,N,5] -> concentrations [B,N,1] in µg/m³."""
    if model.training:
        raise ValueError("Use eval mode for prediction")
    if temporal.shape[-2] != 24:
        raise ValueError("Require 24 hourly observations")
    t, s = preprocessing.transform(temporal, local, temporal_columns, local_columns)
    device = next(model.parameters()).device
    z = model(torch.from_numpy(t).to(device), torch.from_numpy(s).to(device),
              torch.as_tensor(edge_index, dtype=torch.long, device=device))
    return preprocessing.inverse_target(z.cpu().numpy())


def batches(hours, size):
    for i in range(0, len(hours), size):
        yield hours[i:i + size]


def supervised(model, batch):
    prediction = model(batch["temporal"], batch["local"], batch["edge_index"])
    return prediction[batch["label_batch"], batch["label_node"], 0]


@torch.no_grad()
def evaluate(model, preprocessing, data, split, batch_size, device="cpu"):
    model.eval()
    frames = []
    for hours in batches(data.hours(split), batch_size):
        batch = data.batch(hours, split, preprocessing, device)
        z = supervised(model, batch).cpu().numpy()
        raw = preprocessing.inverse_target(z, clip=False)
        if not np.isfinite(raw).all():
            raise ValueError("Model produced nonfinite predictions")
        frame = batch["labels"][["station_uuid", "time_utc", "node_index", "value_ugm3"]].copy()
        frame = frame.rename(columns={"value_ugm3": "observed"})
        frame["predicted"] = np.maximum(raw, 0)
        frame["clipped"] = raw < 0
        frames.append(frame)
    predictions = pd.concat(frames, ignore_index=True)
    per_station = [{"station_uuid": str(station), **compute_metrics(g.observed, g.predicted)}
                   for station, g in predictions.groupby("station_uuid", sort=True)]
    median = {}
    for key in ("mae", "rmse", "bias", "r2"):
        finite = [m[key] for m in per_station if np.isfinite(m[key])]
        median[key] = float(np.median(finite)) if finite else float("nan")
    metrics = dict(pooled=compute_metrics(predictions.observed, predictions.predicted),
                   per_station=per_station, station_median=median,
                   clipped_fraction=float(predictions.clipped.mean()), split=split)
    return metrics, predictions


def train(snapshot, output, target="pm25", protocol="fixed_split", fold=None,
          model_config=ModelConfig(), training_config=TrainingConfig(), device="cpu"):
    cfg = training_config
    output = Path(output)
    if output.exists():
        raise FileExistsError("Use a new output directory for each training run")
    random.seed(cfg.seed)
    np.random.seed(cfg.seed)
    torch.manual_seed(cfg.seed)
    torch.use_deterministic_algorithms(True)
    if torch.cuda.is_available():
        torch.cuda.manual_seed_all(cfg.seed)
        torch.backends.cudnn.benchmark = False
    started = time.perf_counter()
    if str(device).startswith("cuda"):
        torch.cuda.reset_peak_memory_stats(device)
    data = SnapshotData(snapshot, target, protocol, fold)
    preprocessing = data.fit_preprocessor()
    if model_config.temporal_channels != 34 or model_config.local_channels != 10:
        raise ValueError("Configured channels differ from preprocessing contract")
    model = STGNN(model_config).to(device)
    optimizer = torch.optim.AdamW(model.parameters(), lr=cfg.learning_rate, weight_decay=cfg.weight_decay)
    metadata = dict(**data.identity(), unit="ug/m3", training_config=asdict(cfg),
                    fit_times=data.fit_times, provenance=provenance(),
                    checksum_policy="exact binary bytes; text may match after CRLF-to-LF normalization",
                    deterministic_algorithms=True, device=str(device),
                    graph_policy="one-hop eight-neighbour grid; shared weights; full exogenous graph",
                    label_keys_sha256={s: stgnn_hash(data.root / "splits" / f"{s}.parquet")
                                       for s in ("train", "val", "test")})
    output.mkdir(parents=True, exist_ok=False)
    write_json(output / "preprocessing.json", preprocessing.state())
    history, best, stale = [], float("inf"), 0
    rng = np.random.default_rng(cfg.seed)
    for epoch in range(1, cfg.epochs + 1):
        hours = data.hours("train")
        hours = [hours[i] for i in rng.permutation(len(hours))]
        model.train()
        loss_sum = 0.0
        count = 0
        for sample in batches(hours, cfg.batch_size):
            batch = data.batch(sample, "train", preprocessing, device)
            if not len(batch["label_values"]):
                continue
            optimizer.zero_grad(set_to_none=True)
            z = supervised(model, batch)
            y = preprocessing.target(batch["label_values"])
            loss = torch.mean((z - y) ** 2)
            if not torch.isfinite(loss):
                raise ValueError("Nonfinite training loss")
            loss.backward()
            torch.nn.utils.clip_grad_norm_(model.parameters(), cfg.gradient_clip, error_if_nonfinite=True)
            optimizer.step()
            loss_sum += float(loss.detach()) * len(y)
            count += len(y)
        validation, _ = evaluate(model, preprocessing, data, "val", cfg.batch_size, device)
        rmse = validation["pooled"]["rmse"]
        history.append(dict(epoch=epoch, train_mse_standardized=loss_sum / count, validation_rmse=rmse))
        print(f"epoch={epoch} train_mse={loss_sum/count:.6f} val_rmse={rmse:.6f}", flush=True)
        if rmse < best:
            best, stale = rmse, 0
            metadata.update(best_epoch=epoch, validation=validation)
            save_checkpoint(output / "best.pt", model, preprocessing, metadata)
        else:
            stale += 1
        write_json(output / "history.json", history)
        if stale >= cfg.patience:
            break
    model, preprocessing, metadata = load_checkpoint(output / "best.pt", device)
    metrics, predictions = evaluate(model, preprocessing, data, "val", cfg.batch_size, device)
    predictions.to_parquet(output / "validation_predictions.parquet", index=False)
    write_json(output / "validation_metrics.json", metrics)
    metadata.update(elapsed_seconds=time.perf_counter() - started,
                    parameters=sum(p.numel() for p in model.parameters()), epochs_run=len(history),
                    memory=peak_memory(device))
    save_checkpoint(output / "best.pt", model, preprocessing, metadata)
    write_json(output / "run_manifest.json", metadata)
    return metadata


def evaluate_checkpoint(checkpoint, snapshot, output, split="test", device="cpu", batch_size=4):
    output = Path(output)
    if output.exists():
        raise FileExistsError("Use a new evaluation output directory")
    model, preprocessing, meta = load_checkpoint(checkpoint, device)
    data = SnapshotData(snapshot, meta["target"], meta["protocol"], meta["fold"])
    if data.hashes != meta["snapshot_hashes"]:
        raise ValueError("Evaluation snapshot differs from checkpoint provenance")
    metrics, predictions = evaluate(model, preprocessing, data, split, batch_size, device)
    output.mkdir(parents=True, exist_ok=False)
    predictions.to_parquet(output / "predictions.parquet", index=False)
    write_json(output / "metrics.json", metrics)
    write_json(output / "evaluation_manifest.json", dict(checkpoint_sha256=stgnn_hash(checkpoint),
                                                       split=split, target=meta["target"],
                                                       protocol=meta["protocol"], fold=meta["fold"]))
    return metrics
