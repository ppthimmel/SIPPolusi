import numpy as np

TEMPORAL_VALUES = ['no2_mol_m2', 'ntl', 'temperature_2m', 'relative_humidity_2m', 'wind_speed_10m', 'wind_direction_10m', 'boundary_layer_height', 'precipitation', 'surface_pressure', 'geoscf_pm25_ugm3', 'geoscf_no2_ugm3', 'geoscf_fcst_pm25_ugm3', 'geoscf_fcst_no2_ugm3', 'hour_sin', 'hour_cos', 'weekday_sin', 'weekday_cos']
TEMPORAL_AGES = ['no2_mol_m2_age_hours', 'ntl_age_hours', 'geoscf_pm25_age_hours', 'geoscf_no2_age_hours', 'weather_age_hours', 'geoscf_fcst_pm25_lead_hours', 'geoscf_fcst_no2_lead_hours']
LAND_VALUES = ['ndvi','ndbi']
LAND_AGES = ['ndvi_age_hours','ndbi_age_hours']
SENSOR_FEATURES = ['dist_nearest_sensor_m']

"""Train-fitted transformations with explicit masks; raw datasets are never imputed."""

import numpy as np


BASE_TEMPORAL_COLUMNS = [*TEMPORAL_VALUES, *TEMPORAL_AGES]
TEMPORAL_COLUMNS = BASE_TEMPORAL_COLUMNS + ["idw_concentration", "idw_spread", "idw_nearest_m", "idw_source_count"]
LOCAL_COLUMNS = [*LAND_VALUES, *LAND_AGES]
TRANSFORMED_TEMPORAL = [name for c in TEMPORAL_COLUMNS for name in
                        (["wind_direction_sin", "wind_direction_cos"] if c == "wind_direction_10m" else [c])]


def transform_values(values, columns):
    values = np.asarray(values, dtype=np.float64)
    if values.shape[-1] != len(columns):
        raise ValueError("Input width differs from feature contract")
    out = []
    for i, name in enumerate(columns):
        x = np.where(np.isfinite(values[..., i]), values[..., i], np.nan)
        if name.endswith("_age_hours") or name == "dist_nearest_sensor_m":
            if np.any(x < 0):
                raise ValueError(f"Negative age/distance: {name}")
            x = np.log1p(x)
        if name == "wind_direction_10m":
            theta = np.deg2rad(x)
            out.extend([np.sin(theta), np.cos(theta)])
        else:
            out.append(x)
    return np.stack(out, axis=-1)


class FeatureScaler:
    def __init__(self, columns):
        self.columns = list(columns)
        self.mean = self.scale = self.count = None

    def fit(self, values):
        transformed = transform_values(values, self.columns)
        x = transformed.reshape(-1, transformed.shape[-1])
        mask = np.isfinite(x)
        self.count = mask.sum(axis=0)
        safe_count = np.maximum(self.count, 1)
        self.mean = np.where(mask, x, 0).sum(axis=0) / safe_count
        variance = np.where(mask, (x - self.mean) ** 2, 0).sum(axis=0) / safe_count
        self.scale = np.sqrt(variance)
        self.scale[self.scale < 1e-8] = 1.0
        return self

    def transform(self, values, columns):
        if list(columns) != self.columns or self.mean is None:
            raise ValueError("Unfitted scaler or feature order mismatch")
        x = transform_values(values, self.columns)
        mask = np.isfinite(x)
        scaled = np.where(mask, (x - self.mean) / self.scale, 0)
        result = np.concatenate([scaled, mask.astype(np.float64)], axis=-1).astype("float32")
        if not np.isfinite(result).all():
            raise ValueError("Nonfinite transformed f")
        return result

    def state(self):
        return dict(columns=self.columns, mean=self.mean.tolist(), scale=self.scale.tolist(), count=self.count.tolist())

    @classmethod
    def from_state(cls, state):
        obj = cls(state["columns"])
        obj.mean = np.asarray(state["mean"], dtype=float)
        obj.scale = np.asarray(state["scale"], dtype=float)
        obj.count = np.asarray(state["count"], dtype=int)
        if np.any(obj.scale <= 0) or not np.isfinite(obj.mean).all() or not np.isfinite(obj.scale).all():
            raise ValueError("Invalid preprocessing statistics")
        return obj


class Preprocessor:
    def fit(self, temporal, local, targets):
        self.temporal = FeatureScaler(TEMPORAL_COLUMNS).fit(temporal)
        self.local = FeatureScaler(LOCAL_COLUMNS).fit(local)
        y = np.asarray(targets, dtype=float)
        if not y.size or not np.isfinite(y).all():
            raise ValueError("Fit target statistics on finite training labels only")
        self.target_mean = float(y.mean())
        self.target_scale = max(float(y.std()), 1e-8)
        return self

    def transform(self, temporal, local, temporal_columns=TEMPORAL_COLUMNS, local_columns=LOCAL_COLUMNS):
        return (self.temporal.transform(temporal, temporal_columns),
                self.local.transform(local, local_columns))

    def state(self):
        return dict(temporal=self.temporal.state(), local=self.local.state(),
                    target_mean=self.target_mean, target_scale=self.target_scale,
                    transformed_temporal_columns=TRANSFORMED_TEMPORAL,
                    output_policy="IDW + target_scale * correction, then clamp at zero")

    @classmethod
    def from_state(cls, state):
        if state["transformed_temporal_columns"] != TRANSFORMED_TEMPORAL:
            raise ValueError("Unsupported transformed feature contract")
        obj = cls()
        obj.temporal = FeatureScaler.from_state(state["temporal"])
        obj.local = FeatureScaler.from_state(state["local"])
        obj.target_mean = float(state["target_mean"])
        obj.target_scale = float(state["target_scale"])
        if not np.isfinite(obj.target_mean) or not np.isfinite(obj.target_scale) or obj.target_scale <= 0:
            raise ValueError("Invalid target statistics")
        return obj
