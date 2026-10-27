"""Worker bridge: registered models, independent fallbacks and input identity."""
from __future__ import annotations

import hashlib
import json
import logging
import os

import numpy as np
import pandas as pd

log = logging.getLogger(__name__)


def infer_registered(time_window, ground_truth, spec, settings, inputs=None, resolver=None, versions=None):
    from spatial_model.artifacts import registry
    from .fallback import run_idw_fallback
    from .exposure import exposure_index
    from spatial_model.models.idw import idw_model_version
    resolver = resolver or registry.resolve
    versions = versions or {}
    if set(versions) - {"pm25", "no2"}:
        raise ValueError("Unknown pollutant in model_versions")
    if inputs is not None and hasattr(inputs, "reset"):
        inputs.reset()
    grid = run_idw_fallback(ground_truth, spec, power=settings["power"], neighbors=settings["neighbors"])
    provenance = {}
    for target in ("pm25", "no2"):
        grid[f"{target}_source"] = "idw"
        item = dict(source="idw", model_version=idw_model_version(settings["power"], settings["neighbors"]))
        stage = "load_runtime"
        requested = None
        try:
            from spatial_model.inference.serving import load_model, predict_grid
            stage = "resolve_registry"
            bundle, identity = resolver(target, versions.get(target, os.environ.get(f"SPATIAL_{target.upper()}_VERSION", "production")))
            requested = identity
            stage = "load_bundle"
            loaded = load_model(bundle, target)
            stage = "prepare_inputs"
            if inputs is None:
                raise ValueError("Environmental features and sensor history are unavailable")
            stage = "predict_grid"
            predicted = predict_grid(loaded, inputs, spec, bundle, time_window.end)
            valid = np.isfinite(predicted)
            if not valid.any():
                raise ValueError("Model produced no supported grid predictions")
            grid.loc[valid, f"{target}_ugm3"] = predicted[valid]
            item.update(source="idw_stgnn_residual" if target == "pm25" else "lightgbm",
                        model_version=identity, fallback_cells=int((~valid).sum()))
            if target == "pm25" and loaded.metadata.get("estimation_source") == "idw":
                item["source"] = "idw"
                item["note"] = "Validation selected epoch-zero IDW"
            grid.loc[valid, f"{target}_source"] = item["source"]
        except Exception as exc:
            # A failure in one pollutant must not discard the other's successful model.
            log.warning("%s model fallback (%s)", target, type(exc).__name__)
            # Keep details out of persisted API-facing metadata (URLs may contain credentials).
            item = dict(source="idw", model_version=idw_model_version(settings["power"], settings["neighbors"]),
                        fallback_reason=f"{stage}:{type(exc).__name__}", requested_model_version=requested)
        provenance[target] = item
    grid["exposure_index"] = exposure_index({p: grid[f"{p}_ugm3"].to_numpy() for p in provenance})
    fingerprint = hashlib.sha256(pd.util.hash_pandas_object(ground_truth, index=False).values.tobytes())
    if inputs is not None:
        fingerprint.update(pd.util.hash_pandas_object(inputs.readings, index=False).values.tobytes())
    # Environmental history is hashed by the provider as it is consumed, not by path.
        fingerprint.update(inputs.fingerprint.encode())
    identity = hashlib.sha256(json.dumps(dict(models=provenance, input=fingerprint.hexdigest(),
                                              grid=spec.as_dict(), settings=settings), sort_keys=True, default=str).encode()).hexdigest()
    return grid, provenance, f"spatial-{identity}", fingerprint.hexdigest()


def worker_inputs(conn, time_window, settings, schema="ground_truth"):
    """Use the same QC/hourly aggregation as the IDW fallback for all 24 hours."""
    from contracts import TimeWindow
    from spatial_model.data.observations import load_ground_truth_window
    from spatial_model.inference.serving import GridInputs
    from feature_matrix.sources import SourceReader
    history = []
    for offset in range(23, -1, -1):
        start = time_window.start - pd.Timedelta(hours=offset)
        window = TimeWindow(start, start + pd.Timedelta(hours=1))
        frame = load_ground_truth_window(conn, window, settings["qc"], schema)
        if frame.empty:
            continue
        frame = frame.pivot(index="station_id", columns="pollutant", values="value").reset_index()
        frame = frame.rename(columns={"station_id": "station_uuid", "pm25": "pm25_ugm3", "no2": "no2_ugm3"})
        for p in ("pm25_ugm3", "no2_ugm3"):
            if p not in frame:
                frame[p] = np.nan
        frame["time_utc"] = window.end
        history.append(frame)
    if not history:
        return None
    return GridInputs(SourceReader(os.environ.get("SPATIAL_FEATURE_ROOT", "dataset_processed")), pd.concat(history, ignore_index=True))
