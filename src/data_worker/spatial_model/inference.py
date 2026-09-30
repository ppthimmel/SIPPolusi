import logging

logger = logging.getLogger(__name__)


def run_downscale_inference(time_window: str) -> dict:
    """Spatio-Temporal Graph Neural Network inference (PF-05, PF-06).

    Estimates PM2.5/NO2 on a 100 m grid from macro-scale and micro-scale
    features, computes a confidence score, and aggregates grid output to
    road edges. Falls back to IDW interpolation on failure (PF-12).
    """
    return {
        "time_window": time_window,
        "coverage_ratio": None,
        "confidence_distribution": None,
        "estimation_source": "stgnn",
        "message": "ST-GNN inference not implemented yet.",
    }


def run_idw_fallback(time_window: str) -> dict:
    """Inverse Distance Weighting fallback from ground sensor measurements,
    used when ST-GNN inference fails (PF-12, PF-14, PNF-03, PNF-07)."""
    return {
        "time_window": time_window,
        "estimation_source": "idw",
        "message": "IDW fallback interpolation not implemented yet.",
    }
