"""XGBoost construction, native save/load and library version."""
import importlib.metadata
import os

from spatial_model.configs import DEFAULT_SEED, load_model_config

MODEL_FILE = "model.json"


def make_model(hp, *, seed=DEFAULT_SEED, threads=None):
    if threads is None:
        threads = int(os.environ.get("THREADS", str(load_model_config("xgboost")["threads"])))
    import xgboost as xgb
    return xgb.XGBRegressor(**hp, random_state=seed, n_jobs=threads)


def load(path, columns):
    """Load a saved model and check it expects exactly ``columns`` in that order."""
    import xgboost as xgb
    model = xgb.XGBRegressor()
    model.load_model(str(path))
    if list(model.get_booster().feature_names or []) != list(columns):
        raise ValueError("XGBoost feature order mismatch")
    return model


def library_version():
    """Installed version; the CPU-only wheel is distributed as ``xgboost-cpu``."""
    for name in ("xgboost", "xgboost-cpu"):
        try:
            return importlib.metadata.version(name)
        except importlib.metadata.PackageNotFoundError:
            continue
    raise importlib.metadata.PackageNotFoundError("xgboost")
