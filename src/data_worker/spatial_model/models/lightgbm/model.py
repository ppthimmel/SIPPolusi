"""Native LightGBM construction and concentration reconstruction."""
import os
import numpy as np
from spatial_model.configs import DEFAULT_SEED, load_model_config

def predict(model, metadata, frame):
    columns = metadata["columns"]
    if list(frame.columns) != columns:
        frame = frame.loc[:, columns]
    raw = model.predict(frame)
    transform = metadata["target_transform"]
    if transform == "direct":
        value = raw
    elif transform == "resid_mean":
        value = frame.src_mean.to_numpy() + raw
    elif transform == "resid_idw":
        value = frame.idw_p2_k8.to_numpy() + raw
    elif transform == "resid_idw_p1all":
        value = frame.idw_p1_all.to_numpy() + raw
    elif transform == "logratio_mean":
        value = np.expm1(np.log1p(frame.src_mean.to_numpy()) + raw)
    else:
        raise ValueError("Unsupported target transformation")
    if not np.isfinite(value).all():
        raise ValueError("Nonfinite NO2 prediction")
    return np.maximum(value, 0)


# ---------------------------------------------------------------------- model factories
def make_model(hp, *, seed=DEFAULT_SEED, threads=None):
    if threads is None:
        threads = int(os.environ.get("THREADS", str(load_model_config("lightgbm")["threads"])))
    import lightgbm as lgb
    base = dict(n_estimators=400, learning_rate=0.05, num_leaves=15,
                min_child_samples=50, subsample=0.8, subsample_freq=1,
                colsample_bytree=0.8, reg_lambda=1.0)
    return lgb.LGBMRegressor(**(base | hp), random_state=seed, n_jobs=threads,
                             deterministic=True, force_row_wise=True, verbose=-1)
