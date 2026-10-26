# Bukti Verifikasi TI-SE-05: Backend merakit subgraf dari cache

Tanggal: 2026-10-13. Uji yang membutuhkan data polusi dijalankan di PostgreSQL 18.6 +
PostGIS 3.6.4 lokal yang diisi kode Data Worker, karena cache polusi Railway masih kosong.
Uji graf dan galat juga dijalankan ke Railway (environment `development`, baca-saja).

## Ringkasan per acceptance criteria

| Acceptance criteria | Implementasi | Bukti uji |
|---|---|---|
| Permintaan valid menghasilkan subgraf terhubung, atau kesalahan domain yang jelas bila rute tidak mungkin | `build_solver_input`: validasi → baca kedua cache bersamaan → `assemble_request_subgraph` (snap layak + keterhubungan) → `attach_edge_weights` | `test_valid_request_gives_connected_subgraph`, `test_route_request_to_solver_input` (DB lokal), tes kesalahan domain di bawah, contoh Railway |
| Moda memengaruhi ruas yang tersedia pada subgraf | Ruas difilter per moda; `bidirectional` mengikuti `oneway` untuk sepeda; kecepatan rujukan per moda; snap memakai ruas yang boleh ditinggalkan/dimasuki moda itu | `test_mode_changes_available_edges`, `test_travel_time_and_direction_follow_the_mode`, `test_bike_origin_skips_node_without_outgoing_edge`, `test_route_request_to_solver_input` (walk 24 ruas, bike 23) |
| Bobot ruas yang hilang/kedaluwarsa tidak diam-diam diperlakukan sebagai nol | Ruas tanpa estimasi diisi median bbox dengan confidence 0 dan penanda `imputed_median`; konsentrasi tidak diisi; tanpa median menghasilkan 503 `cache_empty`; kedaluwarsa ditandai `data_stale` | `test_ut_be_07b_missing_edges_get_bbox_median_and_zero_confidence`, `test_row_without_exposure_index_counts_as_missing_but_keeps_concentration`, `test_no_usable_weight_in_bbox_is_cache_empty_not_zero`, `test_stale_and_excluded_are_reported`, `test_partial_pollution_cache_is_flagged` |
| Uji mencakup koordinat di luar cakupan, titik tidak dapat di-snap, graf terputus, dan cache polusi parsial | Lihat tabel uji | `test_ut_be_02c_outside_study_area`, `test_point_that_cannot_be_snapped`, `test_disconnected_destination`, `test_partial_pollution_cache_is_flagged`, `test_unreachable_and_unsnappable` (DB lokal) |

## Hasil uji

| Paket | Hasil |
|---|---|
| `src/backend/planner` | **37 passed**: 35 unit (UT-BE-02, UT-BE-03, UT-BE-07, perakitan, API) + 2 alur di DB lokal |
| `src/backend/osm` | **115 passed**: 100 unit (termasuk snap layak dan bbox `buffer_m: 0`) + 15 integrasi ke Railway |
| `src/backend/pollution` | **30 passed** |
| `src/data_worker` penulis graf | **12 passed** |
| Container Cache API | `osm` 100 passed, 15 skipped; `pollution` 20 passed, 10 skipped; `planner` 35 passed, 2 skipped (uji yang butuh DB dilewati) |

## Perubahan yang ikut dibuat

- **Snap layak** di `osm.graph.assemble_request_subgraph`:
  - simpul asal harus punya ruas yang boleh ditinggalkan moda, dan simpul tujuan ruas yang
    boleh dimasuki;
  - asal terdekat dipilih lebih dulu, lalu tujuan terdekat yang dapat dicapai; bila tidak ada,
    kandidat asal berikutnya dicoba.

  Perilaku ini sejalan dengan baseline TI-AI-07, sehingga baseline dan MORL dapat memakai
  simpul yang sama.
- **Bug diperbaiki:** `buffer_m: 0` dengan titik segaris, atau asal = tujuan, menghasilkan bbox
  selebar nol dan galat 500. Sekarang bbox diberi margin minimal 1 m.
- **Urutan galat:** galat perjalanan (422) dilaporkan sebelum galat cache polusi (503).
  Pembacaan kedua cache tetap bersamaan.

## Contoh `RouteRequest` → masukan solver

Data: graf 4 × 4 (24 ruas, satu footway khusus pejalan kaki) dan satu time window polusi yang
**parsial**: ruas 5 dan 6 tidak punya estimasi. Keduanya ditulis kode Data Worker ke basis data
lokal, lalu dibaca lewat HTTP API Backend. Daftar `nodes` dan `edges` dipotong.

```
POST /v1/routes/solver-input
X-Request-ID: contoh-ti-se-05

{"origin": {"lat": -6.2, "lon": 106.8}, "destination": {"lat": -6.197, "lon": 106.803},
 "mode": "walk", "pollutant": "pm25", "preference": {"alpha": 0.5, "beta": 0.5}}
```

```json
{
  "request_id": "contoh-ti-se-05",
  "context": {
    "mode": "walk", "speed_mps": 1.3, "preference": {"alpha": 0.5, "beta": 0.5}, "pollutant": "pm25",
    "bbox": [106.795452, -6.204522, 106.807548, -6.192478],
    "origin":      {"lat": -6.2,   "lon": 106.8,   "node_id": 1,  "snap_distance_m": 0.0},
    "destination": {"lat": -6.197, "lon": 106.803, "node_id": 16, "snap_distance_m": 0.0},
    "same_node": false
  },
  "weights": {
    "time_window_start": "2026-10-13T04:00:00Z", "time_window_end": "2026-10-13T05:00:00Z",
    "data_stale": false, "age_minutes": 51.9,
    "edge_count": 24, "estimated_edge_count": 22, "missing_edge_count": 2,
    "imputed_exposure_index": 1.55, "concentration_missing_count": 2, "excluded_rows": {}
  },
  "source": {
    "graph_version": "jakarta-20261001", "pollution_model_version": "idw-p2-k8",
    "pollution_coverage_ratio": 1.0, "estimation_sources": {"idw": 22},
    "background_sources": {"none": 22}, "rejected_graph_rows": {}
  },
  "origin_node": 1, "destination_node": 16, "node_count": 16, "edge_count": 24,
  "nodes": [{"id": 1, "lon": 106.8, "lat": -6.2}, {"id": 2, "lon": 106.801, "lat": -6.2}, "…"],
  "edges": [
    {"id": 1, "u": 1, "v": 2, "length_m": 110.0, "highway": "footway", "oneway": false, "bidirectional": true,
     "travel_time_s": 84.615, "exposure_index": 1.55, "confidence_score": 0.3, "weight_source": "estimate",
     "concentration_ugm3": 22.5, "pm25_ugm3": 22.5, "no2_ugm3": 40.0, "estimation_source": "idw"},
    {"id": 5, "u": 3, "v": 4, "length_m": 110.0, "highway": "residential", "oneway": false, "bidirectional": true,
     "travel_time_s": 84.615, "exposure_index": 1.55, "confidence_score": 0.0, "weight_source": "imputed_median",
     "concentration_ugm3": null, "pm25_ugm3": null, "no2_ugm3": null, "estimation_source": null},
    "…"
  ]
}
```

Cara membaca:
- **Ruas 1** punya estimasi: indeks paparan 1,55 = ½ × (22,5 ÷ 15 + 40 ÷ 25), sesuai rumus
  desain.
- **Ruas 5** tidak punya estimasi, jadi diisi **median** (1,55) dengan `confidence_score: 0`
  dan `weight_source: "imputed_median"`. Konsentrasinya tetap `null`, bukan 0.
- **`travel_time_s`** = 110 m ÷ 1,3 m/s = 84,6 detik.
- Untuk `mode: "bike"`, subgraf yang sama berisi 23 ruas, karena footway (ruas 1) tidak boleh
  dilalui sepeda (`test_route_request_to_solver_input`).

## Kesalahan domain di Railway (2026-10-13)

| Request | Hasil |
|---|---|
| Monas → Bundaran HI, walk, α 0,5 / β 0,5 | 503 `cache_unavailable`, `reason: cache_empty` ("Spatial Pollution Cache DB belum memiliki time window berstatus complete.") |
| Pantai Ancol (−6,1225; 106,8420) → laut (−6,1000; 106,8420) | 422 `point_not_on_network`, "Titik tujuan: tidak ada simpul yang dapat dilalui moda walk dalam radius 200 m." Dilaporkan walaupun cache polusi kosong |
| α 0,6, β 0,5 | 400 `invalid_request` |
| Tujuan Kepulauan Seribu | 422 `outside_study_area`, "Titik tujuan berada di luar wilayah studi (DKI Jakarta)." |

Dari laptop, perjalanan jauh (±10 km) bisa menghasilkan 503 `query_timeout` karena transfer
graf lewat proxy publik Railway lambat (lihat catatan kinerja di
[`../osm/README.md`](../osm/README.md#catatan-kinerja)). Di jaringan privat Railway hal ini
tidak diharapkan terjadi.

## Catatan untuk TI-SE-06 dan TI-AI-10

- Masukan solver belum memuat `deadline_ms`; batas waktu dihitung saat memanggil
  `IRouteSolve` (TI-SE-06, `compute_deadline`).
- Kecepatan rujukan mengikuti desain (1,3 / 4,2 m/s); baseline TI-AI-07 memakai 5 / 15 km/jam
  (1,39 / 4,17 m/s). Radius snap mengikuti desain (200 m); baseline memakai 300 m. Keduanya
  perlu disepakati sebelum perbandingan rute.
- Baseline dan MORL sebaiknya memakai masukan yang sama ini (simpul hasil snap, ruas, waktu
  tempuh, bobot), sesuai catatan di README `route_model`.
