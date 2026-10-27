"""Shared IDW sensor features for training and serving."""
import numpy as np
IDW_POWER = 2.0
IDW_NEIGHBORS = 8
IDW_COLUMNS = ["idw_concentration", "idw_spread", "idw_nearest_m", "idw_source_count"]
def idw_features(distances, readings):
    available_distances = np.where(np.isfinite(readings)[None, :], distances, np.inf)
    nearest = np.argsort(available_distances, axis=1, kind="stable")[:, :IDW_NEIGHBORS]
    distance = np.take_along_axis(available_distances, nearest, axis=1)
    values = np.where(np.isfinite(readings[nearest]), readings[nearest], 0)
    available = np.isfinite(distance)
    coincident = distance < 1e-6

    weights = np.zeros_like(distance)
    np.divide(1.0, np.maximum(distance, 1e-6) ** IDW_POWER, out=weights, where=available)
    hits = coincident.any(axis=1)
    weights[hits] = coincident[hits].astype(float)
    weight_sum = weights.sum(axis=1)
    weights = np.divide(weights, weight_sum[:, None], out=np.zeros_like(weights), where=weight_sum[:, None] > 0)

    mean = (weights * values).sum(axis=1)
    spread = np.sqrt((weights * (values - mean[:, None]) ** 2).sum(axis=1))
    nearest_distance = distance.min(axis=1)
    count = available.sum(axis=1)
    result = np.column_stack([mean, spread, nearest_distance, count])
    result[count == 0] = np.nan
    return result.astype("float32")