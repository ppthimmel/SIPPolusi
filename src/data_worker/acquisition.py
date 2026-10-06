"""Penjadwal Akuisisi: satu siklus Data Worker (Dokumen Desain, Gambar 3.7)."""

from __future__ import annotations

import logging
import time
from typing import Callable

from contracts import JAKARTA_BBOX, TimeWindow
from ground_truth import fetch_ground_truth
from spku.config import Config

log = logging.getLogger("data_worker.acquisition")

Step = Callable[[TimeWindow, Config], dict]


def _step_ground_truth(time_window: TimeWindow, config: Config) -> dict:
    batch = fetch_ground_truth("spku", JAKARTA_BBOX, time_window, config=config)
    return {
        "status": "ok" if batch.available else "unavailable",
        "measurements": len(batch.measurements),
        "stations": len({m.station_id for m in batch.measurements}),
        "unavailable_stations": len(batch.unavailable_stations),
        "run_id": batch.run.get("run_id"),
        "run_status": batch.run.get("status"),
        "note": batch.note,
    }


def _step_downscale_inference(time_window: TimeWindow, config: Config) -> dict:
    """trigger_downscale_inference: ST-GNN, jatuh ke IDW bila gagal (PF-12)."""
    from spatial_model.inference import run_downscale_inference, run_idw_fallback

    window_start = time_window.start.strftime("%Y-%m-%dT%H:%M:%SZ")
    try:
        result = run_downscale_inference(window_start)
    except Exception as exc:  # noqa: BLE001 - fallback IDW
        log.exception("inferensi downscale gagal, beralih ke IDW")
        result = {**run_idw_fallback(window_start), "fallback_reason": f"{type(exc).__name__}: {exc}"}
    # Kedua fungsi masih kerangka; laporkan sebagai not_implemented sampai terisi.
    return {"status": result.pop("status", "not_implemented"), **result}


#: Langkah B2-B15 berurutan. ``None`` berarti belum diimplementasikan dan
#: dicatat sebagai not_implemented, bukan galat, agar siklus tetap berjalan.
DEFAULT_STEPS: list[tuple[str, Step | None]] = [
    ("fetch_satellite_archive", None),
    ("fetch_tropospheric_column", None),
    ("fetch_meteo", None),
    ("fetch_ground_truth", _step_ground_truth),
    ("run_quality_control", None),
    ("align_spatiotemporal", None),
    ("write_features", None),
    ("trigger_downscale_inference", _step_downscale_inference),
]


def schedule_acquisition(
    time_window: TimeWindow,
    *,
    config: Config | None = None,
    steps: list[tuple[str, Step | None]] | None = None,
) -> dict:
    """Jalankan satu siklus akuisisi untuk ``time_window``.

    Setiap langkah dijalankan berurutan dan statusnya dicatat. Galat pada
    satu langkah tidak menghentikan langkah berikutnya (UT-DW-01b, PF-14).
    """
    config = config or Config.load()
    report: dict = {"time_window": str(time_window), "steps": []}
    for name, step in steps if steps is not None else DEFAULT_STEPS:
        started = time.monotonic()
        if step is None:
            entry = {"step": name, "status": "not_implemented"}
        else:
            try:
                entry = {"step": name, **step(time_window, config)}
            except Exception as exc:  # noqa: BLE001 - siklus tidak boleh berhenti
                log.exception("langkah %s gagal", name)
                entry = {"step": name, "status": "failed", "error": f"{type(exc).__name__}: {exc}"}
        entry["duration_s"] = round(time.monotonic() - started, 2)
        report["steps"].append(entry)
        log.info("siklus %s: %s -> %s", time_window, name, entry["status"])
    report["failed"] = [s["step"] for s in report["steps"] if s["status"] == "failed"]
    return report
