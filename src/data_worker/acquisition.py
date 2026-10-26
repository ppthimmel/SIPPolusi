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
from spku.config import Config

log = logging.getLogger("data_worker.acquisition")

Step = Callable[[TimeWindow, Config], dict]


def ground_truth_status(run_status: str | None, available: bool) -> str:
    """Status langkah ground truth dari status lintasan SPKU dan ketersediaan data.

    Lintasan yang gagal atau dibatalkan tidak pernah dilaporkan ``ok``, walaupun
    time window masih berisi data dari lintasan sebelumnya (riwayat 48 jam):
    sumbernya gagal pada siklus ini (Tabel 3.12). Lintasan ``partial`` berarti
    sebagian stasiun tetap gagal setelah dicoba ulang, sehingga ``degraded``.
    """
    if run_status in ("failed", "aborted") or run_status is None:
        return "failed"
    if not available:
        return "unavailable"
    return "ok" if run_status == "ok" else "degraded"


def _step_ground_truth(time_window: TimeWindow, config: Config) -> dict:
    batch = fetch_ground_truth("spku", JAKARTA_BBOX, time_window, config=config)
    run = batch.run
    status = ground_truth_status(run.get("status"), batch.available)
    note = batch.note
    if status == "failed":
        note = (f"lintasan SPKU {run.get('status')}: {run.get('note')}"
                + ("; time window masih berisi data lintasan sebelumnya" if batch.available else ""))
    return {
        "status": status,
        "source": "spku",
        "available": batch.available,
        "measurements": len(batch.measurements),
        "stations": len({m.station_id for m in batch.measurements}),
        "unavailable_stations": len(batch.unavailable_stations),
        "run_id": run.get("run_id"),
        "run_status": run.get("status"),
        "retried_requests": run.get("retried_requests", 0),
        "failed_requests": run.get("failed_requests", 0),
        "note": note,
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
    progress: Callable[[list[dict]], None] | None = None,
) -> dict:
    """Jalankan satu siklus akuisisi untuk ``time_window``.

    Setiap langkah dijalankan berurutan dan statusnya dicatat. Galat pada
    satu langkah tidak menghentikan langkah berikutnya (UT-DW-01b, PF-14).
    ``progress`` (opsional) menerima daftar status langkah setelah setiap langkah,
    agar siklus yang dihentikan di tengah tetap meninggalkan catatan.
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
        level = logging.WARNING if entry["status"] in ("failed", "degraded", "unavailable") else logging.INFO
        log.log(level, "siklus %s: %s -> %s%s", time_window, name, entry["status"],
                f" ({entry.get('note') or entry.get('error')})"
                if entry["status"] not in ("ok", "not_implemented") and (entry.get("note") or entry.get("error")) else "")
        if progress is not None:
            progress(report["steps"])
    report["failed"] = [s["step"] for s in report["steps"] if s["status"] == "failed"]
    return report
