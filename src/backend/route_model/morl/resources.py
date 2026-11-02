"""Keeps a long training run from harming the machine it runs on.

Checked once per update. Crossing a limit stops the run cleanly, with the reason in
its manifest and its checkpoints in place, instead of swapping the machine to a halt
or filling its disk. The samples kept here are a small bounded window; the full RSS
history goes to metrics.jsonl on disk.
"""
from __future__ import annotations

import os
import shutil
import statistics
import subprocess
from collections import deque
from dataclasses import asdict, dataclass
from pathlib import Path

import torch

MB = 1024 ** 2
GB = 1024 ** 3
# Leave these cores to the rest of the machine, whatever a config asks for.
RESERVED_CORES = 2
_MIN_GROWTH_WINDOW = 9  # three segments of at least three samples


@dataclass(frozen=True)
class ResourceLimits:
    max_rss_gb: float = 16.0
    # Stop when RSS grows this much across this many updates: a leak, long before the absolute limit.
    growth_window_updates: int = 50
    growth_limit_mb: float = 512.0
    min_free_disk_gb: float = 2.0
    keep_checkpoints: int = 3

    def __post_init__(self):
        if self.growth_window_updates < _MIN_GROWTH_WINDOW:
            raise ValueError(f"growth_window_updates must be at least {_MIN_GROWTH_WINDOW}")
        if self.keep_checkpoints < 1:
            raise ValueError("keep_checkpoints must be at least 1")

    def as_dict(self):
        return asdict(self)


def current_rss_bytes() -> int:
    """Resident memory now (getrusage only reports the peak)."""
    statm = Path("/proc/self/statm")
    if statm.exists():
        return int(statm.read_text().split()[1]) * os.sysconf("SC_PAGE_SIZE")
    output = subprocess.run(["ps", "-o", "rss=", "-p", str(os.getpid())], capture_output=True, text=True, check=True)
    return int(output.stdout.strip()) * 1024


def accelerator_allocated_bytes(device) -> int | None:
    device = torch.device(device)
    if device.type == "mps":
        return torch.mps.current_allocated_memory()
    if device.type == "cuda":
        return torch.cuda.memory_allocated(device)
    return None


def release_accelerator_cache(device) -> None:
    device = torch.device(device)
    if device.type == "mps":
        torch.mps.empty_cache()
    elif device.type == "cuda":
        torch.cuda.empty_cache()


def thread_limit(requested: int) -> int:
    return max(1, min(requested, (os.cpu_count() or 1) - RESERVED_CORES))


class ResourceGuard:
    def __init__(self, limits: ResourceLimits, path, device="cpu", rss=current_rss_bytes, disk=None):
        self.limits, self.path, self.device, self._rss = limits, Path(path), device, rss
        self._disk = disk or (lambda: shutil.disk_usage(self.path).free)
        self._samples = deque(maxlen=limits.growth_window_updates)

    def check(self) -> tuple[dict, str | None]:
        """(values to log, reason to stop or None)."""
        rss, free = self._rss(), self._disk()
        self._samples.append(rss)
        snapshot = {"rss_mb": round(rss / MB, 1), "free_disk_gb": round(free / GB, 1)}
        allocated = accelerator_allocated_bytes(self.device)
        if allocated is not None:
            snapshot["accelerator_allocated_mb"] = round(allocated / MB, 1)
        limits = self.limits
        if rss > limits.max_rss_gb * GB:
            return snapshot, f"Memory limit: RSS {rss / GB:.2f} GB exceeds {limits.max_rss_gb} GB"
        if free < limits.min_free_disk_gb * GB:
            return snapshot, f"Disk limit: {free / GB:.2f} GB free, below {limits.min_free_disk_gb} GB"
        growth = self._sustained_growth()
        if growth is not None:
            return snapshot, (f"Memory growth: RSS rose {growth / MB:.0f} MB in {limits.growth_window_updates} "
                              f"updates (limit {limits.growth_limit_mb} MB)")
        return snapshot, None

    def _sustained_growth(self) -> float | None:
        """Bytes grown across the window, if RSS kept rising through it.

        A leak rises throughout. The allocator warming up rises once and then holds,
        so the later segments must keep rising too (each by a quarter of the limit).
        """
        if len(self._samples) < self._samples.maxlen:
            return None
        samples = list(self._samples)
        third = len(samples) // 3
        first, middle, last = (statistics.median(samples[:third]), statistics.median(samples[third:-third]),
                               statistics.median(samples[-third:]))
        limit = self.limits.growth_limit_mb * MB
        if last - first > limit and middle - first > limit / 4 and last - middle > limit / 4:
            return last - first
        return None


def prune_checkpoints(folder, keep: int) -> None:
    """Keep the newest `keep` periodic checkpoints; best.pt and last.pt are separate files."""
    for old in sorted(Path(folder).glob("update_*.pt"))[:-keep]:
        old.unlink()
