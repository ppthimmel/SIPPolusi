"""Execute the standalone ST-GNN notebook and retain logs, provenance and artifacts.

Run from any directory: python path/to/run_stgnn.py
Training uses notebook settings; validation selects the checkpoint, followed by one test evaluation.
"""

import hashlib
import argparse
import ast
import json
import os
import platform
import subprocess
import sys
import time
from pathlib import Path

os.environ.setdefault("MKL_THREADING_LAYER", "SEQUENTIAL")
os.environ.setdefault("MPLBACKEND", "Agg")
os.environ.setdefault("CUBLAS_WORKSPACE_CONFIG", ":4096:8")

HERE = Path(__file__).resolve().parent
REPO = HERE.parents[2]
NOTEBOOK = HERE / "train_stgnn_ds_v0.3.0.ipynb"


def code_commit():
    """Git HEAD, or the COMMIT file shipped with the Colab package when git is unavailable."""
    try:
        return subprocess.check_output(["git", "-c", f"safe.directory={REPO.as_posix()}", "rev-parse", "HEAD"],
                                       cwd=REPO, text=True, stderr=subprocess.DEVNULL).strip()
    except (OSError, subprocess.CalledProcessError):
        marker = REPO / "COMMIT"
        return marker.read_text(encoding="utf-8").strip() + " (package)" if marker.is_file() else "unknown"


class RunLog:
    def __init__(self, terminal, path):
        self.terminal = terminal
        self.file = path.open("a", encoding="utf-8")

    def write(self, text):
        self.terminal.write(text)
        self.file.write(text)
        self.file.flush()

    def flush(self):
        self.terminal.flush()
        self.file.flush()


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--protocol", choices=("fixed_split", "loso"))
    parser.add_argument("--fold", type=int)
    parser.add_argument("--target", choices=("pm25", "no2"))
    parser.add_argument("--output", type=Path)
    parser.add_argument("--seed", type=int)
    parser.add_argument("--epochs", type=int)
    parser.add_argument("--data", type=Path, help="Snapshot folder (overrides the notebook DATA_DIR)")
    parser.add_argument("--device", help="cuda or cpu (overrides the notebook DEVICE)")
    parser.add_argument("--cache-inputs", action="store_true", help="Cache transformed hourly inputs on the GPU")
    args = parser.parse_args(argv)
    notebook = json.loads(NOTEBOOK.read_bytes())
    overrides = {name: value for name, value in {
        "PROTOCOL": args.protocol, "FOLD": args.fold, "TARGET": args.target,
        "SEED": args.seed, "EPOCHS": args.epochs, "DEVICE": args.device,
        "DATA_DIR": args.data.resolve() if args.data is not None else None,
    }.items() if value is not None}
    if args.output is not None:
        overrides["OUTPUT_DIR"] = args.output.resolve()
    # Save the exact notebook settings that are executed for this fold.
    for cell in notebook["cells"]:
        source = "".join(cell["source"])
        if cell["cell_type"] != "code" or "DATA_DIR = Path(" not in source:
            continue
        lines = source.splitlines()
        for node in reversed(ast.parse(source).body):
            if isinstance(node, ast.Assign) and isinstance(node.targets[0], ast.Name):
                name = node.targets[0].id
                if name in overrides:
                    value = overrides[name]
                    expression = f"Path({str(value)!r})" if isinstance(value, Path) else repr(value)
                    lines[node.lineno - 1:node.end_lineno] = [f"{name} = {expression}"]
        cell["source"] = "\n".join(lines).splitlines(keepends=True)
    if args.cache_inputs:
        for cell in notebook["cells"]:
            source = "".join(cell["source"])
            if cell["cell_type"] != "code" or not source.startswith("def make_batch"):
                continue
            cell["source"] = '''cached_temporal, cached_local = preprocessing.transform(temporal_features, local_features)
temporal_cache = torch.from_numpy(cached_temporal).to(DEVICE)
local_cache = torch.from_numpy(cached_local).to(DEVICE)
edge_cache = edge_index.to(DEVICE)
window_offsets = torch.arange(window - 1, -1, -1, device=DEVICE)

def make_batch(hours, split):
    positions = torch.tensor([hour_index[pd.Timestamp(hour)] for hour in hours], device=DEVICE)
    temporal = temporal_cache[positions[:, None] - window_offsets].permute(0, 2, 1, 3)
    local = local_cache[positions]
    labels = [split_labels[split][split_labels[split].time_utc == pd.Timestamp(hour)].assign(batch_index=i)
              for i, hour in enumerate(hours)]
    lab = pd.concat(labels, ignore_index=True)
    return dict(temporal=temporal, local=local, edge_index=edge_cache,
        label_batch=torch.tensor(lab.batch_index.to_numpy(), dtype=torch.long, device=DEVICE),
        label_node=torch.tensor(lab.node_index.to_numpy(dtype="int64"), dtype=torch.long, device=DEVICE),
        idw_values=torch.tensor(lab.idw_prediction.to_numpy(), dtype=torch.float32, device=DEVICE),
        label_values=torch.tensor(lab.value_ugm3.to_numpy(), dtype=torch.float32, device=DEVICE), labels=lab)

# Verify values and ordering against the original window preprocessing, without fitting.
check_hours = split_hours["train"][:2]
check_positions = [hour_index[pd.Timestamp(hour)] for hour in check_hours]
raw_temporal = np.stack([temporal_features[i-window+1:i+1].transpose(1, 0, 2) for i in check_positions])
raw_local = np.stack([local_features[i] for i in check_positions])
expected_temporal, expected_local = preprocessing.transform(raw_temporal, raw_local)
cached = make_batch(check_hours, "train")
np.testing.assert_array_equal(cached["temporal"].cpu().numpy(), expected_temporal)
np.testing.assert_array_equal(cached["local"].cpu().numpy(), expected_local)
del cached_temporal, cached_local, cached, expected_temporal, expected_local, raw_temporal, raw_local
print("Cached preprocessing matches original windows exactly.")
'''.splitlines(keepends=True)
    notebook_bytes = (json.dumps(notebook, indent=1, ensure_ascii=False) + "\n").encode("utf-8")
    scope = {"display": lambda value: print(value.to_string(), flush=True)}
    started = time.perf_counter()
    output = None
    provenance = {}
    terminal = sys.stdout
    log = None
    try:
        for index, cell in enumerate(notebook["cells"]):
            source = "".join(cell["source"])
            if cell["cell_type"] == "markdown":
                print(source.strip(), flush=True)
                continue
            if source.startswith("for epoch in range"):
                output = scope["OUTPUT_DIR"]
                # Reuse the exact code and settings, not a different CLI implementation.
                (output / "training_notebook.ipynb").write_bytes(notebook_bytes)
                (output / "training_runner.py").write_bytes(Path(__file__).read_bytes())
                log = RunLog(terminal, output / "training.log")
                sys.stdout = log
                provenance = {
                    "status": "training", "client_date": "2026-10-14",
                    "code_commit": code_commit(),
                    "notebook_sha256": hashlib.sha256(notebook_bytes).hexdigest(),
                    "runner_sha256": hashlib.sha256(Path(__file__).read_bytes()).hexdigest(),
                    "dataset": "ds-v0.3.0", "snapshot_hashes": scope["hashes"],
                    "settings": {name: scope[name] for name in (
                        "TARGET", "PROTOCOL", "FOLD", "DEVICE", "SEED", "EPOCHS", "BATCH_SIZE",
                        "LEARNING_RATE", "WEIGHT_DECAY", "PATIENCE", "GRADIENT_CLIP", "THREADS",
                        "USE_GRAPH", "USE_LOCAL", "IDW_POWER", "IDW_NEIGHBORS")},
                    "model_config": scope["asdict"](scope["model_config"]),
                    "gpu": scope["torch"].cuda.get_device_name(0) if scope["DEVICE"].startswith("cuda") else None,
                    "cublas_workspace_config": os.environ.get("CUBLAS_WORKSPACE_CONFIG"),
                    "cached_inputs": args.cache_inputs,
                    "versions": {"python": platform.python_version(),
                        "torch": str(scope["torch"].__version__),
                        "torch_geometric": scope["torch_geometric"].__version__,
                        "numpy": scope["np"].__version__, "pandas": scope["pd"].__version__},
                    "temporal_columns": scope["TEMPORAL_COLUMNS"],
                    "local_columns": scope["LOCAL_COLUMNS"],
                    "fit_times": scope["fit_times"], "training_nodes": scope["training_nodes"].tolist(),
                    "source_stations": scope["source_ids"],
                    "label_counts": {split: len(table) for split, table in scope["split_labels"].items()},
                    "skipped_training_labels": len(scope["skipped_training_labels"]),
                    "test_policy": "One evaluation after validation checkpoint selection; previously inspected dataset, exploratory run",
                }
                # Check group isolation without fitting a model.
                sources = scope["source_stations"]
                if scope["PROTOCOL"] == "loso":
                    held_group = scope["held_out_group"]
                    if (sources.group_id == held_group).any():
                        raise ValueError("Held-out group leaked into sensor sources")
                    for split in ("train", "val"):
                        if (scope["split_labels"][split].group_id == held_group).any():
                            raise ValueError(f"Held-out group leaked into {split} labels")
                    if not (scope["split_labels"]["test"].group_id == held_group).all():
                        raise ValueError("LOSO test contains the wrong group")
                    provenance["held_out_group"] = held_group
                edges = scope["edge_index"].numpy()
                np = scope["np"]
                for label in scope["split_labels"]["train"].drop_duplicates("station_uuid").itertuples():
                    group = sources.group_id.to_numpy() == label.group_id
                    context = np.unique(np.r_[label.node_index, edges[0, edges[1] == label.node_index]])
                    if scope["source_allowed"][np.ix_(context, group)].any():
                        raise ValueError("Target group leaked into GAT sensor context")
                (output / "run_manifest.json").write_text(json.dumps(provenance, indent=2), encoding="utf-8")
                print("Leakage preflight passed. Output:", output, flush=True)
            exec(compile(source, f"{NOTEBOOK}:cell-{index}", "exec"), scope)
        provenance.update(status="completed", best_epoch=scope["best_epoch"],
                          epochs_run=len(scope["history"]), validation_rmse=scope["best_rmse"])
        if scope["DEVICE"].startswith("cuda"):
            provenance["gpu_peak_allocated_bytes"] = scope["torch"].cuda.max_memory_allocated()
    except BaseException as error:
        provenance.update(status="interrupted" if isinstance(error, KeyboardInterrupt) else "failed",
                          error=repr(error))
        raise
    finally:
        if output is not None:
            provenance["elapsed_seconds"] = time.perf_counter() - started
            (output / "run_manifest.json").write_text(json.dumps(provenance, indent=2), encoding="utf-8")
        if log is not None:
            sys.stdout = terminal
            log.file.close()
    print("Completed run:", output)


if __name__ == "__main__":
    main()
