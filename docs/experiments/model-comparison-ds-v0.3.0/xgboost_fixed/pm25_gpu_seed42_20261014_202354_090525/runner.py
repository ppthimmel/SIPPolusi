"""GPU PM2.5 fixed split; six preset candidates selected only on validation."""
from __future__ import annotations

import hashlib
import json
import os
from datetime import datetime
from pathlib import Path
import time

os.environ.setdefault("MKL_THREADING_LAYER", "SEQUENTIAL")

import numpy as np
import pandas as pd
import xgboost as xgb

import common
from models import CORE, TARGETS, design, feature_groups


def verify_snapshot_windows(root=common.SNAP):
    """Allow Git CRLF conversion for text only; binary hashes remain exact."""
    hashes = {}
    for line in (root / "CHECKSUMS.sha256").read_text().splitlines():
        expected, name = line.split(maxsplit=1)
        name = name.lstrip("*")
        raw = (root / name).read_bytes()
        actual = hashlib.sha256(raw).hexdigest()
        if actual != expected:
            assert Path(name).suffix in {".json", ".md", ".csv", ".txt", ".toml"}, name
            assert hashlib.sha256(raw.replace(b"\r\n", b"\n")).hexdigest() == expected, name
        hashes[name] = expected
    return hashes


def main():
    started = time.perf_counter()
    hashes = verify_snapshot_windows()
    common.verify_snapshot = verify_snapshot_windows
    p = common.Panel("pm25")
    allowed = p.station_split == "train"
    for split in ("val", "test"):
        assert not set(p.group[allowed]) & set(p.group[p.station_split == split])
    fields = common.spatial_features(p, allowed)
    data = {s: design(p, fields, p.split == s).reset_index(drop=True)
            for s in ("train", "val", "test")}
    groups = feature_groups("pm25")
    # Exclude row-shift lags: panel hours have gaps between time blocks.
    columns = CORE + [c for g, cs in groups.items() if g != "lag" for c in cs]
    for frame in data.values():
        assert len(frame) and np.isfinite(frame.y).all()
        assert np.isfinite(frame.idw_p2_k8).all()
        assert not np.isinf(frame[columns].to_numpy()).any()
    out = common.OUT / "xgboost_fixed" / datetime.now().strftime("pm25_gpu_seed42_%Y%m%d_%H%M%S_%f")
    out.mkdir(parents=True, exist_ok=False)
    (out / "runner.py").write_bytes(Path(__file__).read_bytes())
    for name in ("common.py", "models.py"):
        (out / name).write_bytes((common.OUT / name).read_bytes())
    print("Output:", out, "rows:", {s: len(d) for s, d in data.items()},
          "features:", len(columns), flush=True)
    candidates, best_score, selected = [], float("inf"), None
    for target in ("direct", "resid_idw"):
        encode, decode = TARGETS[target]
        for depth in (2, 4, 6):
            model = xgb.XGBRegressor(
                device="cuda", tree_method="hist", objective="reg:squarederror",
                n_estimators=1500, learning_rate=0.03, max_depth=depth,
                min_child_weight=20, reg_lambda=10, subsample=0.8,
                colsample_bytree=0.8, random_state=42, n_jobs=8,
                early_stopping_rounds=50, eval_metric="rmse")
            model.fit(data["train"][columns], encode(data["train"]),
                      eval_set=[(data["val"][columns], encode(data["val"]))], verbose=False)
            device = json.loads(model.get_booster().save_config())["learner"]["generic_param"]["device"]
            assert device.startswith("cuda"), f"GPU training required; got {device}"
            z = model.get_booster().predict(xgb.DMatrix(data["val"][columns]),
                                            iteration_range=(0, model.best_iteration + 1))
            prediction = np.maximum(decode(data["val"], z), 0)
            assert np.isfinite(prediction).all()
            score = common.metrics(data["val"].y, prediction)["rmse"]
            row = dict(target=target, max_depth=depth, best_iteration=model.best_iteration,
                       validation_rmse=score, device=device)
            candidates.append(row)
            pd.DataFrame(candidates).to_csv(out / "validation_candidates.csv", index=False)
            print(row, flush=True)
            if score < best_score:
                best_score, selected = score, row.copy()
                model.get_booster().save_model(out / "best.ubj")
    # Reload the validation-selected model before any test prediction.
    model = xgb.Booster()
    model.load_model(out / "best.ubj")
    decode = TARGETS[selected["target"]][1]
    evaluation = []
    for split in ("val", "test"):
        frame = data[split]
        z = model.predict(xgb.DMatrix(frame[columns]),
                          iteration_range=(0, selected["best_iteration"] + 1))
        predicted = np.maximum(decode(frame, z), 0)
        assert np.isfinite(predicted).all()
        predictions = frame[["hour", "station_idx", "station", "group", "y"]].copy()
        predictions["station_uuid"] = p.stations.uuid.to_numpy()[frame.station_idx]
        predictions["time_utc"] = p.hours[frame.hour].to_numpy()
        predictions["xgboost"] = predicted
        for name in ("idw_p2_k8", "idw_p1_all"):
            predictions[name] = frame[name].to_numpy()
        predictions.to_parquet(out / f"{split}_predictions.parquet", index=False)
        if split == "test":
            comparison = []
            for baseline in ("idw_p2_k8", "idw_p1_all"):
                lo, hi, better, total = common.station_bootstrap(predictions, "xgboost", base=baseline)
                comparison.append(dict(baseline=baseline, ci_low=lo, ci_high=hi,
                                       stations_better=better, stations_total=total))
            pd.DataFrame(comparison).to_csv(out / "paired_station_bootstrap.csv", index=False)
        for name in ("idw_p2_k8", "idw_p1_all", "xgboost"):
            for station, rows in [("pooled", predictions), *predictions.groupby("station", sort=True)]:
                evaluation.append(dict(split=split, method=name, station=station,
                                       **common.metrics(rows.y, rows[name])))
    table = pd.DataFrame(evaluation)
    table.to_csv(out / "evaluation_metrics.csv", index=False)
    manifest = dict(target="pm25", protocol="fixed_split", seed=42, selected=selected,
                    features=columns, candidate_grid=candidates,
                    rows={s: len(d) for s, d in data.items()}, snapshot_hashes=hashes,
                    source_rule="Train stations only, excluding target's entire group; current hour readings allowed",
                    selection="Six preset candidates and early stopping on validation; test evaluated after selection",
                    limitation="Exploratory single-seed experiment on a previously inspected test dataset",
                    versions=dict(xgboost=xgb.__version__, numpy=np.__version__, pandas=pd.__version__),
                    source_hashes={name: hashlib.sha256((common.OUT / name).read_bytes()).hexdigest()
                                   for name in ("common.py", "models.py", Path(__file__).name)},
                    elapsed_seconds=time.perf_counter() - started)
    (out / "run_manifest.json").write_text(json.dumps(manifest, indent=2), encoding="utf-8")
    pooled = table[table.station == "pooled"]
    print(pooled.to_string(index=False), flush=True)
    (out / "report.md").write_text(
        "# XGBoost PM2.5 fixed split\n\n" + pooled.to_markdown(index=False) +
        "\n\nSix preset direct/residual configurations, selected on validation with early stopping. "
        "Train-station readings only, excluding each target group. No lag features. "
        "Single seed; exploratory results on a previously inspected test dataset.\n", encoding="utf-8")
    print("Saved:", out, flush=True)


if __name__ == "__main__":
    main()
