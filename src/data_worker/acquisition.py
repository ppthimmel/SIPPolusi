"""Penjadwal Akuisisi: satu siklus Data Worker (Dokumen Desain, Gambar 3.7)."""

from __future__ import annotations

import dataclasses
import logging
import os
import pathlib
import time
from typing import Callable

from contracts import JAKARTA_BBOX, TimeWindow
from ground_truth import fetch_ground_truth
from log_config import log_event
from spku.config import Config

log = logging.getLogger("data_worker.acquisition")

Step = Callable[[TimeWindow, Config], dict]

#: Status langkah yang berarti hasilnya tidak ada atau tidak lengkap.
_PROBLEM_STATUSES = {"failed": logging.ERROR, "unavailable": logging.WARNING, "partial": logging.WARNING}


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
    """trigger_downscale_inference: ST-GNN, jatuh ke IDW bila gagal, lalu EdgeWeight ke cache.

    Aktif hanya bila DOWNSCALE_WRITE_CACHE=1, karena setiap time window menulis
    sekitar 372 ribu baris (±92 MB) ke pollution.edge_pollution. Setelah
    penulisan, hanya DOWNSCALE_KEEP_WINDOWS time window complete terakhir
    (bawaan 6) yang dipertahankan. SPATIAL_ARTIFACT_DIR (opsional) menerima
    artefak penelusuran per run.
    """
    if os.environ.get("DOWNSCALE_WRITE_CACHE") != "1":
        return {"status": "disabled", "note": "atur DOWNSCALE_WRITE_CACHE=1 untuk menulis EdgeWeight ke cache"}
    import psycopg

    from spatial_model import cache
    from spatial_model.inference import load_ground_truth_window, load_idw_settings, run_downscale_inference

    settings = load_idw_settings()
    artifact_dir = os.environ.get("SPATIAL_ARTIFACT_DIR") or None
    with psycopg.connect(config.database) as conn:
        conn.autocommit = True
        ground_truth = load_ground_truth_window(conn, time_window, settings["qc"], config.db_schema)
        graph_version = cache.read_active_graph_version(conn)
        if graph_version is None:
            return {"status": "unavailable", "note": "tidak ada graf road network berstatus active"}
        edges = cache.read_road_edges(conn, graph_version)
        summary = run_downscale_inference(
            time_window, ground_truth=ground_truth, edges=edges, graph_version=graph_version, conn=conn,
            settings=settings, artifact_dir=artifact_dir,
            pieces_cache_dir=(pathlib.Path(artifact_dir) / "pieces") if artifact_dir else None,
        )
        pruned = cache.prune_pollution_windows(conn, int(os.environ.get("DOWNSCALE_KEEP_WINDOWS") or 6))
    result = dataclasses.asdict(summary)
    result["windows_pruned"] = pruned
    result["status"] = "ok" if summary.status in ("complete", "skipped") else summary.status
    result["run_status"] = summary.status
    return result


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
        default_level = logging.WARNING if entry.get("fallback_reason") else logging.INFO
        level = _PROBLEM_STATUSES.get(entry["status"], default_level)
        log_event(log, "job_step", level, time_window=str(time_window), step=name, status=entry["status"],
                  duration_s=entry["duration_s"], run_id=entry.get("run_id"),
                  fallback_reason=entry.get("fallback_reason"), error=entry.get("error"))
    report["failed"] = [s["step"] for s in report["steps"] if s["status"] == "failed"]
    return report
