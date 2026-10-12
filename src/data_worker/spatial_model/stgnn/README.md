# ST-GNN implementation

TCN dynamic encoder + local MLP + concatenation + one GAT layer + scalar regression.
See [ARCHITECTURE.md](ARCHITECTURE.md) for feature contracts, tensor shapes and limitations.
One checkpoint estimates one pollutant's completed-hour concentration in µg/m³.

## Run

From `src/data_worker`, install the project dependencies in the chosen ML environment.
This package needs PyTorch, PyTorch Geometric, NumPy, pandas, PyArrow, PyProj and Shapely;
their declarations are in `requirements.txt`. The current `base2` on this Windows machine
has conflicting Intel OpenMP runtimes; setting `$env:MKL_THREADING_LAYER='SEQUENTIAL'`
before starting Python allows verification without enabling duplicate OpenMP runtimes.
This is an environment setting, not a requirement embedded in the model.

```powershell
# PM2.5 fixed station/time split. Defaults: 200 epochs maximum, early stopping on validation.
D:/conda_envs/base2/python.exe -m spatial_model.stgnn train `
  --snapshot ../../docs/experiments/ds-v0.1.0 `
  --target pm25 --protocol fixed_split --out ../../graphify-out/stgnn-pm25-seed42

# Separate NO2 LOSO fold; repeat for each eligible fold and seed in the evaluation protocol.
D:/conda_envs/base2/python.exe -m spatial_model.stgnn train `
  --snapshot ../../docs/experiments/ds-v0.1.0 --target no2 --protocol loso --fold 0 `
  --out ../../graphify-out/stgnn-no2-fold0-seed42

# Test evaluation is explicit and never selects a training checkpoint.
D:/conda_envs/base2/python.exe -m spatial_model.stgnn evaluate `
  --snapshot ../../docs/experiments/ds-v0.1.0 `
  --checkpoint ../../graphify-out/stgnn-pm25-seed42/best.pt `
  --split test --out ../../graphify-out/stgnn-pm25-seed42-test
```

Use `--epochs 1` for an integration smoke run; this does not constitute an evaluated model.
Use `--seed 43`/`44`, `--no-graph` or `--no-local` for the specified experiments. CPU is the
default; `--threads 4` controls CPU thread count. `--device cuda` requires an appropriate
PyTorch build/device. Deterministic algorithms are enabled strictly; unsupported deterministic
GPU operations raise rather than silently producing a reproducibility claim.

`configs/default.toml` holds candidate model/training settings. The CLI accepts `--config`.
Use a fresh output directory per run. Snapshot checksums are verified before reading data;
binary files must match exactly, while text files can match after Git's CRLF→LF conversion.
No source dataset or frozen normalization file is overwritten.

## Data and evaluation

`SnapshotData` caches selected raw feature columns as `[hours, nodes, channels]`, then gathers
causal 24-hour windows for each minibatch. It filters target/protocol keys explicitly. PM2.5
fixed splits use `splits/{train,val,test}.parquet`. LOSO constructs train/validation keys from
the respective temporal blocks without the held-out group; test keys contain that group only.
An unknown fold or a fold with no eligible labels in a required split raises a clear error;
record it as unevaluable rather than creating labels or silently changing the protocol.

The adapter recalculates sensor distances for the chosen eligible sensor set. Preprocessing
is fitted on training hours and eligible station-context nodes only, excluding held-out patches.
Target scaling uses eligible training labels. Raw statistics in the snapshot cannot normalize
the transformed log-age/distance and circular wind channels directly.

The network produces standardized predictions. Training uses unclipped standardized MSE;
validation/test/serving invert target scaling and clamp at zero. Metrics include pooled and
per-station MAE, RMSE, bias and R², station medians and clipping rate. Constant-target R² is
undefined and written as JSON `null`.

Training writes `best.pt`, `preprocessing.json`, `history.json`, validation predictions/metrics
and `run_manifest.json`. The checkpoint contains weights, model config, fitted transforms,
column contracts, target/unit, snapshot hashes, fold, sensor set, feature-fit nodes, seed,
dependency versions, code revision, source hashes and validation selection details. Checkpoints
load with `torch.load(..., weights_only=True)`.

## Inference boundary

### Standalone sensor-conditioned experiment

`train_idw_residual.ipynb` is a separate experimental variant. Upload the same complete
`ds-v0.1.0` snapshot and change `DATA_DIR` and `OUTPUT_DIR` in its first cell. It keeps
short sequential cells and includes all code; no repository imports are needed.

Its prediction is **IDW + target-standard-deviation × ST-GNN correction**, clamped at
zero. The smaller network has 6,697 parameters, 16 hidden channels, two attention heads,
dropout 0.2, AdamW learning rate 0.0003 and weight decay 0.001. Validation controls early
stopping and [ReduceLROnPlateau](https://docs.pytorch.org/docs/2.14/generated/torch.optim.lr_scheduler.ReduceLROnPlateau.html).
These are candidate settings, not demonstrated optimal values.

- IDW uses power 2 and eight neighbours, matching the existing baseline. Label baselines
  use actual station coordinates; grid sensor features use node centres.
- Sensor readings come only from eligible training stations, using the same completed-hour
  convention as the existing IDW experiment. Production must supply these readings with
  the required availability; this is not a satellite/weather-only model.
- Training labels' entire station groups are excluded from their IDW baselines. A two-cell
  exclusion around source groups also prevents each target group's readings from entering
  any node in its one-hop GAT receptive field, including historical sensor channels.
- Four additional hourly inputs describe IDW concentration, weighted spread, nearest eligible
  sensor distance and number of used sources. Their masks bring the temporal input to 42
  channels. The local input is NDVI/NDBI and their ages with masks (8 channels); dynamic IDW
  distance replaces the previous self-inclusive sensor distance.
- One fixed-split training label has no other source group. It is explicitly skipped and
  exported to `skipped_training_labels.parquet`. Validation/test rows are never silently
  removed: missing eligible IDW sources make that evaluation fail explicitly.
- The final layer starts at zero correction. Epoch zero is included in checkpoint selection,
  so plain IDW is retained when no epoch improves validation RMSE. This protects validation
  performance only; it does not guarantee improvement on test.
- `idw_comparison.csv` compares both methods on identical observations. Results must identify
  `idw_stgnn_residual` or `idw` according to the selected checkpoint, and should be confirmed
  across seeds and LOSO folds before making general superiority claims. The existing test
  split has already been inspected during development and is an exploratory comparison.

Artifacts use `artifact_version = "idw_residual_v1"`. They require this notebook's residual
inference path and eligible sensor inputs; the original CLI's `load_checkpoint`/`predict_raw`
cannot interpret them. No production inference or registry integration is changed.

Run the leakage and baseline-equivalence checks with
`python -m pytest tests/test_stgnn_idw_notebook.py -q` from `src/data_worker`.

```python
from spatial_model.stgnn.train import load_checkpoint, predict_raw
from spatial_model.stgnn.preprocessing import TEMPORAL_COLUMNS, LOCAL_COLUMNS

model, preprocessing, metadata = load_checkpoint("best.pt")
prediction = predict_raw(
    model, preprocessing, temporal, local, edge_index,
    temporal_columns=TEMPORAL_COLUMNS, local_columns=LOCAL_COLUMNS,
)
```

`temporal` is raw `[B,N,24,16]`; `local` is raw `[B,N,5]` with land values/ages and eligible
sensor distance. `edge_index` is `[2,E]`. Output is `[B,N,1]` in µg/m³. `model.py` has no
data access, registry calls or background-worker side effects.

Register an artifact in MLflow and integrate `spatial_model/inference.py` only after the
required evaluation. Its existing ST-GNN-unavailable/IDW fallback remains in place until a
validated artifact is deployed. Whole-city feature construction, tile scheduling, calibrated
confidence and registry publication are separate integration work. The model accepts variable
node counts and has no learned node-ID embeddings; one-hop halo equivalence is tested.
The current training graph's 115 disconnected components still limit spatial interactions.

## Verification

```powershell
D:/conda_envs/base2/python.exe -m pytest tests/test_stgnn.py -q
```

Checks cover missingness/circular wind, feature-order rejection, temporal causality, graph
batch isolation, exactly one self loop, isolated nodes, gradients, permutation equivariance,
halo equivalence, checkpoint reload and real-snapshot fixed/LOSO exclusions for both targets.
