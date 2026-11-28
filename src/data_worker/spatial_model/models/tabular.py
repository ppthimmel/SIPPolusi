"""Native files of the tabular NO2 families (LightGBM, XGBoost): one design, one ``predict``."""
from pathlib import Path

TABULAR_FAMILIES = ("lightgbm", "xgboost")
LABELS = {"lightgbm": "LightGBM", "xgboost": "XGBoost"}


def model_file(family):
    if family not in TABULAR_FAMILIES:
        raise ValueError("Unsupported tabular model family")
    return "model.txt" if family == "lightgbm" else "model.json"


def load_native(directory, metadata):
    """Model saved next to ``metadata.json``, checked against its recorded column order."""
    family = metadata.get("model_family", "lightgbm")
    path = Path(directory) / model_file(family)
    if family == "lightgbm":
        import lightgbm as lgb
        model = lgb.Booster(model_file=str(path))
        if model.feature_name() != metadata["columns"]:
            raise ValueError("LightGBM feature order mismatch")
        return model, path
    from spatial_model.models.xgboost.model import load
    return load(path, metadata["columns"]), path
