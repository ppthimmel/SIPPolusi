"""Pure IDW interpolation shared by evaluation and inference."""
import numpy as np

DEFAULT_POWER = 2.0
DEFAULT_NEIGHBORS = 8
COINCIDENT_M = 1e-6


class NoGroundTruthError(RuntimeError):
    """Tidak ada pengukuran sensor darat untuk diinterpolasi (UT-SDM-08d)."""


def idw_interpolate(
    src_xy: np.ndarray,
    src_values: np.ndarray,
    dst_xy: np.ndarray,
    power: float = DEFAULT_POWER,
    neighbors: int | None = DEFAULT_NEIGHBORS,
    chunk: int = 20_000,
) -> dict[str, np.ndarray]:
    """IDW dari titik sumber ke titik tujuan.

    ``src_xy`` dan ``dst_xy`` berbentuk (n, 2) dalam meter. ``neighbors``
    ``None`` atau ≤ 0 berarti seluruh stasiun dipakai. Nilai sumber NaN
    diabaikan.

    Mengembalikan kamus berisi ``value`` (estimasi), ``n_used`` (jumlah
    stasiun yang dipakai), ``nearest_m`` (jarak stasiun terdekat),
    ``mean_dist_m`` (rerata jarak stasiun yang dipakai), dan ``source_index``
    (indeks baris ``src_xy`` yang dipakai, berbentuk (len(dst_xy), n_used)).
    """
    src_xy = np.asarray(src_xy, dtype=float).reshape(-1, 2)
    src_values = np.asarray(src_values, dtype=float).ravel()
    dst_xy = np.asarray(dst_xy, dtype=float).reshape(-1, 2)
    if len(src_xy) != len(src_values):
        raise ValueError("src_xy dan src_values wajib sama panjang")
    if power < 0:
        raise ValueError("power tidak boleh negatif")

    keep = np.isfinite(src_values) & np.isfinite(src_xy).all(axis=1)
    original_index = np.flatnonzero(keep)
    src_xy, src_values = src_xy[keep], src_values[keep]
    n_src = len(src_values)
    n_dst = len(dst_xy)
    if n_src == 0:
        raise NoGroundTruthError("tidak ada pengukuran sensor darat yang valid")

    k = n_src if not neighbors or neighbors <= 0 else min(int(neighbors), n_src)
    value = np.empty(n_dst)
    n_used = np.full(n_dst, k, dtype=int)
    nearest = np.empty(n_dst)
    mean_dist = np.empty(n_dst)
    used = np.empty((n_dst, k), dtype=int)

    for lo in range(0, n_dst, chunk):
        hi = min(lo + chunk, n_dst)
        d = np.sqrt(((dst_xy[lo:hi, None, :] - src_xy[None, :, :]) ** 2).sum(axis=2))
        if k < n_src:
            idx = np.argpartition(d, k - 1, axis=1)[:, :k]
        else:
            idx = np.broadcast_to(np.arange(n_src), (hi - lo, n_src))
        used[lo:hi] = original_index[idx]
        dk = np.take_along_axis(d, idx, axis=1)
        vk = src_values[idx]
        nearest[lo:hi] = dk.min(axis=1)
        mean_dist[lo:hi] = dk.mean(axis=1)

        coincident = dk < COINCIDENT_M
        with np.errstate(divide="ignore"):
            w = 1.0 / np.power(np.where(coincident, 1.0, dk), power)
        est = (w * vk).sum(axis=1) / w.sum(axis=1)
        hit = coincident.any(axis=1)
        if hit.any():
            # Rata-rata stasiun yang berimpit (umumnya tepat satu).
            cw = coincident[hit].astype(float)
            est[hit] = (cw * vk[hit]).sum(axis=1) / cw.sum(axis=1)
        value[lo:hi] = est

    return {"value": value, "n_used": n_used, "nearest_m": nearest, "mean_dist_m": mean_dist,
            "source_index": used}


def idw_model_version(power: float, neighbors: int) -> str:
    return f"idw-p{power:g}-k{neighbors}"
