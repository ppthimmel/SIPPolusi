"""Push the result of one Data Worker cycle to the Prometheus Pushgateway (IMetricsSink, issue #112).

The Data Worker runs as a short Cloud Run Job (one cycle per execution), so Prometheus cannot scrape it; the
cycle pushes its result once, when it ends, to ``PUSHGATEWAY_URL`` under the grouping key ``job=data_worker``.
Prometheus scrapes the Pushgateway with ``honor_labels``.

The push uses POST, which replaces only the metric families it sends. A failed cycle therefore leaves
``sippolusi_job_last_success_timestamp_seconds`` from the last successful one in place, and the
"no successful cycle for 2 hours" alert keeps working. Spatial Downscaling Model families are sent only when the
cycle reached the inference step, so a cycle that stopped earlier does not erase the last inference result.

Without ``PUSHGATEWAY_URL`` nothing is sent. A failed push is logged as a warning and never changes the cycle's
status or exit code: losing a metric must not affect the functional path (IMetricsSink contract).
"""
from __future__ import annotations

import logging
import math
import os
import time
from collections.abc import Iterable, Mapping, Sequence

log = logging.getLogger("data_worker.metrics")

GROUPING_PATH = "/metrics/job/data_worker"
CYCLE_STATUSES = ("ok", "degraded", "failed", "skipped")
SUCCESS_STATUSES = {"ok", "degraded"}
INFERENCE_STEP = "trigger_downscale_inference"
CONFIDENCE_CATEGORIES = {"low": "low_lt_0_4", "medium": "medium_0_4_to_0_7", "high": "high_ge_0_7"}
PUSH_TIMEOUT_S = 10

_HELP = {
    "sippolusi_job_last_finished_timestamp_seconds": ("gauge", "Unix time the last cycle finished, any status."),
    "sippolusi_job_last_success_timestamp_seconds": ("gauge", "Unix time the last ok or degraded cycle finished."),
    "sippolusi_job_status": ("gauge", "1 for the status of the last cycle (ok, degraded, failed, skipped)."),
    "sippolusi_job_duration_seconds": ("gauge", "Duration of the last cycle."),
    "sippolusi_job_time_window_end_timestamp_seconds": ("gauge", "End of the time window of the last cycle."),
    "sippolusi_job_step_status": ("gauge", "1 for the status of each step in the last cycle."),
    "sippolusi_job_step_duration_seconds": ("gauge", "Duration of each step in the last cycle."),
    "sippolusi_downscale_coverage_ratio": ("gauge", "Share of road edges that received an estimate (last run)."),
    "sippolusi_downscale_rows_written": ("gauge", "Edge weight rows written to the pollution cache (last run)."),
    "sippolusi_downscale_duration_seconds": ("gauge", "Spatial Downscaling Model inference duration (last run)."),
    "sippolusi_downscale_fallback": ("gauge", "1 when the pollutant fell back to IDW in the last run."),
    "sippolusi_downscale_model_source": ("gauge", "1 for the estimation source used per pollutant (last run)."),
    "sippolusi_downscale_confidence_edges": ("gauge", "Edges per confidence category (low, medium, high)."),
}


def _escape(value: object) -> str:
    return str(value).replace("\\", "\\\\").replace("\n", "\\n").replace('"', '\\"')


def _number(value: float) -> str:
    """Exact text for a sample value. ``:g`` keeps only 6 significant digits, which moved Unix timestamps by
    up to ~5,000 s (1795858295.6 became 1.79586e+09); the shortest round-trip repr keeps every digit."""
    value = float(value)
    if math.isnan(value):
        return "NaN"
    if math.isinf(value):
        return "+Inf" if value > 0 else "-Inf"
    if value.is_integer() and abs(value) < 2 ** 53:
        return str(int(value))
    return repr(value)


def _sample(name: str, value: float, labels: Mapping[str, object] | None = None) -> str:
    if labels:
        rendered = ",".join(f'{k}="{_escape(v)}"' for k, v in sorted(labels.items()))
        return f"{name}{{{rendered}}} {_number(value)}"
    return f"{name} {_number(value)}"


def _render(samples: Iterable[tuple[str, float, Mapping[str, object] | None]]) -> str:
    by_family: dict[str, list[str]] = {}
    for name, value, labels in samples:
        by_family.setdefault(name, []).append(_sample(name, value, labels))
    lines: list[str] = []
    for family, rows in by_family.items():
        kind, text = _HELP[family]
        lines += [f"# HELP {family} {text}", f"# TYPE {family} {kind}", *rows]
    return "\n".join(lines) + "\n"


def cycle_samples(status: str, duration_s: float, window_end_s: float | None, steps: Sequence[dict],
                  now: float | None = None) -> list[tuple[str, float, Mapping[str, object] | None]]:
    """Samples describing one finished cycle (``steps`` as recorded in ``worker.cycle_run``)."""
    now = time.time() if now is None else now
    samples: list[tuple[str, float, Mapping[str, object] | None]] = [
        ("sippolusi_job_last_finished_timestamp_seconds", now, None),
        ("sippolusi_job_duration_seconds", duration_s, None),
    ]
    if status in SUCCESS_STATUSES:
        samples.append(("sippolusi_job_last_success_timestamp_seconds", now, None))
    statuses = CYCLE_STATUSES if status in CYCLE_STATUSES else (*CYCLE_STATUSES, status)
    samples += [("sippolusi_job_status", 1 if s == status else 0, {"status": s}) for s in statuses]
    if window_end_s is not None:
        samples.append(("sippolusi_job_time_window_end_timestamp_seconds", window_end_s, None))
    for step in steps:
        name = step.get("step")
        if not name:
            continue
        samples.append(("sippolusi_job_step_status", 1, {"step": name, "status": step.get("status", "unknown")}))
        if step.get("duration_s") is not None:
            samples.append(("sippolusi_job_step_duration_seconds", step["duration_s"], {"step": name}))
        if name == INFERENCE_STEP:
            samples += _inference_samples(step)
    return samples


def _inference_samples(step: Mapping) -> list[tuple[str, float, Mapping[str, object] | None]]:
    """Spatial Downscaling Model summary (RunSummary, B27) of a cycle that ran the inference step."""
    if step.get("status") in ("disabled", "failed", "unavailable") and not step.get("model_metadata"):
        return []
    samples: list[tuple[str, float, Mapping[str, object] | None]] = []
    if step.get("coverage_ratio") is not None:
        samples.append(("sippolusi_downscale_coverage_ratio", step["coverage_ratio"], None))
    if step.get("rows_written") is not None:
        samples.append(("sippolusi_downscale_rows_written", step["rows_written"], None))
    if step.get("duration_s") is not None:
        samples.append(("sippolusi_downscale_duration_seconds", step["duration_s"], None))
    for pollutant, meta in sorted((step.get("model_metadata") or {}).items()):
        samples.append(("sippolusi_downscale_fallback", 1 if meta.get("fallback_reason") else 0,
                        {"pollutant": pollutant}))
        samples.append(("sippolusi_downscale_model_source", 1,
                        {"pollutant": pollutant, "source": meta.get("source") or "unknown"}))
    distribution = step.get("confidence_distribution") or {}
    for category, key in CONFIDENCE_CATEGORIES.items():
        if key in distribution:
            samples.append(("sippolusi_downscale_confidence_edges", distribution[key], {"category": category}))
    return samples


def push_cycle(status: str, duration_s: float, window_end_s: float | None, steps: Sequence[dict] = (), *,
               url: str | None = None, session=None) -> bool:
    """Push one finished cycle; returns True when the Pushgateway accepted it. Never raises."""
    url = url if url is not None else os.environ.get("PUSHGATEWAY_URL", "")
    if not url:
        return False
    try:
        body = _render(cycle_samples(status, duration_s, window_end_s, steps))
        if session is None:
            import requests
            session = requests
        response = session.post(url.rstrip("/") + GROUPING_PATH, data=body.encode(),
                                headers={"Content-Type": "text/plain; version=0.0.4"}, timeout=PUSH_TIMEOUT_S)
        response.raise_for_status()
        return True
    except Exception as exc:  # noqa: BLE001 - a lost metric must not fail the cycle
        log.warning("metrik siklus tidak terkirim ke Pushgateway: %s", type(exc).__name__)
        return False
