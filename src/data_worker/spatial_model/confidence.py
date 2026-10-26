"""Estimator confidence score (Dokumen Desain Tabel 3.8, PF-12, UT-SDM-06).

Desain menetapkan sifatnya, bukan rumusnya: nilai pada [0, 1], tidak naik
ketika jarak ke sensor darat terdekat bertambah, lebih rendah pada zona tanpa
AOD, dan paling tinggi 0,4 untuk estimasi fallback IDW (kategori keyakinan
rendah). Bobot akhirnya ditetapkan dari galat prediksi per zona.

Rumus provisional yang dipakai sampai kalibrasi PF-12 tersedia:

    c = c_max · exp(−d / L) · (f_aod bila sel tanpa AOD, selain itu 1)

* d: jarak pusat sel ke sensor darat terdekat (meter);
* L = 3000 m: pada baseline TI-AI-04, RMSE PM2.5 naik tajam ketika stasiun
  sumber terdekat berjarak 3–5 km (38,4 µg/m³ terhadap 23–28 µg/m³ di bawah
  3 km);
* c_max = 0,39 untuk IDW dan 1 untuk ST-GNN;
* f_aod = 0,7 (hanya berlaku untuk ST-GNN; IDW tidak memakai AOD).

c_max IDW 0,39, bukan 0,4: desain meminta IDW "paling tinggi 0,4, sehingga
seluruh sel berada pada kategori keyakinan rendah", sedangkan kategori rendah
didefinisikan < 0,4 (Subbab 3.1; UT-FE: 0,40 sudah "sedang"). Nilai 0,39
memenuhi keduanya.

Contoh IDW: 0 km → 0,39; 1 km → 0,28; 3 km → 0,14; 5 km → 0,07.
"""

from __future__ import annotations

import numpy as np

ESTIMATION_SOURCES = ("stgnn", "idw")
#: Batas atas IDW; < 0,4 agar seluruh sel IDW berkategori keyakinan rendah.
IDW_CAP = 0.39
DEFAULT_LENGTH_SCALE_M = 3000.0
DEFAULT_NO_AOD_FACTOR = 0.7


def estimate_confidence(
    nearest_station_m,
    affected_zones=None,
    estimation_source: str = "idw",
    length_scale_m: float = DEFAULT_LENGTH_SCALE_M,
    no_aod_factor: float = DEFAULT_NO_AOD_FACTOR,
) -> np.ndarray:
    """Confidence score per sel pada [0, 1].

    ``affected_zones`` adalah penanda boolean sel yang suku latarnya dihitung
    tanpa AOD (keluaran ``mark_affected_zones``); diabaikan untuk IDW.
    Jarak kosong (tidak ada sensor) menghasilkan 0.
    """
    if estimation_source not in ESTIMATION_SOURCES:
        raise ValueError(f"estimation_source tidak dikenal: {estimation_source!r}")
    if length_scale_m <= 0:
        raise ValueError("length_scale_m wajib positif")
    if not 0.0 <= no_aod_factor <= 1.0:
        raise ValueError("no_aod_factor wajib pada [0, 1]")
    d = np.asarray(nearest_station_m, dtype=float)
    d = np.where(np.isfinite(d), np.maximum(d, 0.0), np.inf)
    cap = IDW_CAP if estimation_source == "idw" else 1.0
    score = cap * np.exp(-d / length_scale_m)
    if estimation_source == "stgnn" and affected_zones is not None:
        score = np.where(np.asarray(affected_zones, dtype=bool), score * no_aod_factor, score)
    return np.clip(score, 0.0, 1.0)


def confidence_distribution(scores) -> dict:
    """Sebaran confidence score untuk RunSummary: kuantil dan jumlah per kategori Subbab 3.1."""
    s = np.asarray(scores, dtype=float)
    s = s[np.isfinite(s)]
    if len(s) == 0:
        return {"n": 0}
    q = np.quantile(s, [0.0, 0.25, 0.5, 0.75, 1.0])
    return {
        "n": int(len(s)),
        "min": float(q[0]), "p25": float(q[1]), "median": float(q[2]), "p75": float(q[3]), "max": float(q[4]),
        "mean": float(s.mean()),
        "low_lt_0_4": int((s < 0.4).sum()),
        "medium_0_4_to_0_7": int(((s >= 0.4) & (s < 0.7)).sum()),
        "high_ge_0_7": int((s >= 0.7).sum()),
    }
