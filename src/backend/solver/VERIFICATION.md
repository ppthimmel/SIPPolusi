# Bukti Verifikasi TI-SE-06: Orkestrasi pemanggilan Route Model

Tanggal: 2026-10-26. Uji yang membutuhkan data berjalan di PostgreSQL 18.6 + PostGIS 3.6.4 lokal
yang diisi kode Data Worker, karena cache polusi Railway masih kosong. Uji galat juga dijalankan
ke Railway (environment `development`, baca-saja).

## Ringkasan per acceptance criteria

| Acceptance criteria | Implementasi | Bukti uji |
|---|---|---|
| Backend dapat menjalankan *mock solver* melalui antarmuka yang sama dengan solver produksi | `RouteSolver` (`IRouteSolve`); `MockSolver`, `BaselineSolver`, dan `HttpSolver` memakai antarmuka yang sama. Penyedia dipilih `ROUTE_SOLVER_PROVIDER` tanpa mengubah endpoint | `test_every_provider_honours_the_contract` (3 penyedia), `test_provider_changes_without_endpoint_change`, `test_factory_reads_configuration`, `test_solve_on_data_written_by_the_worker[mock\|baseline]` |
| *Timeout*, kegagalan solver, dan hasil tanpa rute dikembalikan sebagai respons terstruktur | `call_route_solve` dengan batas `deadline_ms + 300 ms`; galat diklasifikasikan menjadi 503 `route_service_unavailable` (dengan `reason`) atau 422 `no_route_found` (dengan `reason`) | `test_timeout_is_503_solver_timeout`, `test_injected_error_is_503_solver_error`, `test_unexpected_exception_is_not_a_500`, `test_no_route_is_422_with_reason`, `test_unknown_edge_is_invalid_response`, `test_broken_paths_are_invalid_response`, `test_no_time_left_is_deadline_exceeded`, `test_same_node_is_422_same_node`, `test_faults_are_structured`, `test_timeout_is_structured`, `test_failures_are_classified` (HTTP) |
| `SolveResult` menyertakan geometri rute, metrik, sumber rute, dan metadata untuk tahap berikutnya | Geometri LineString searah perjalanan dari basis data; T(R), E(R), jarak, konsentrasi × waktu, confidence; `route_source`, `preference_applied`, `fallback_reason`, `model_version`, `solver`; `context` (graph_version, time window, `data_stale`, simpul snap, …) | `test_every_provider_honours_the_contract`, `test_solve_with_default_mock`, contoh di bawah |
| Uji kontrak dapat dijalankan pada implementasi mock tanpa layanan AI aktif | Semua uji memakai mock, baseline in-process, atau HTTP ke `httpx.MockTransport` | 39 uji tanpa DB lulus di container Docker, tanpa layanan Route Model |

## Hasil uji

| Paket | Hasil |
|---|---|
| `src/backend/solver` | **42 passed**: 39 tanpa DB (kontrak 4, deadline 4, mock 2, orkestrasi 12, HTTP 6, API 11) + 3 alur di DB lokal |
| `src/backend/planner` | 37 passed |
| `src/backend/osm` | 115 passed (100 unit + 15 integrasi ke Railway) |
| `src/backend/pollution` | 30 passed |
| `src/data_worker` penulis graf | 12 passed |
| Container Cache API | `osm` 100, `pollution` 20, `planner` 35, `solver` 39 passed; uji yang butuh DB dilewati |

### Uji timeout

| Uji | Pengaturan | Hasil |
|---|---|---|
| `test_timeout_is_503_solver_timeout` | budget 400 ms, margin 0, grace 50 ms; mock tidur `deadline_ms` + 5 s | 503 `solver_timeout`, diputus < 2 s (tidak menunggu 5 s solver) |
| `test_timeout_is_structured` (API) | sama, lewat `POST /v1/routes/solve` | `{"code": "route_service_unavailable", "reason": "solver_timeout"}` |
| `test_failures_are_classified[timeout]` | `HttpSolver`, transport melempar `ReadTimeout` | `SolverError(solver_timeout)` |
| `test_received_at_header_counts_toward_the_budget` | `X-Request-Received-At` 9 s lalu | 503 `deadline_exceeded`, solver tidak dipanggil |
| `test_ut_be_08a/b` | `compute_deadline` | 2 000 ms terpakai → 5 500; budget 6 000, margin 300, 1 000 ms terpakai → 4 700 |

## Fixture mock

- **`MockSolver`** ([`mock.py`](mock.py)) bersifat deterministik:
  - pembanding = Dijkstra waktu tempuh;
  - rekomendasi = Dijkstra pada α·τ/T_ref + β·I·τ/E_ref;
  - seri dipecah berdasarkan id simpul dan id ruas.

  `ROUTE_SOLVER_FAULT` = `timeout`, `error`, `no_route`, atau `invalid` menyuntikkan galat
  (FAULT_INJECTION, IT-K-04).
- **`mock_service_transport`** ([`tests/conftest.py`](tests/conftest.py)) meniru layanan Route
  Model HTTP:
  - membaca request gzip `/internal/v1/solve` dan menjawab sesuai kontrak;
  - bisa diatur gagal dengan `timeout`, `unreachable`, `error`, `invalid`, atau `no_route`.

## Contoh `SolveResult`

Data: graf 4 × 4 di basis data lokal yang ditulis Data Worker. Ruas pada rute tercepat
(1, 3, 5, 7, 14, 21) diberi PM2.5 60 µg/m³, ruas lain 15 µg/m³; NO₂ 40 µg/m³ untuk semua.
Penyedia `mock`.

```
POST /v1/routes/solve
X-Request-ID: contoh-ti-se-06

{"origin": {"lat": -6.2, "lon": 106.8}, "destination": {"lat": -6.197, "lon": 106.803},
 "mode": "walk", "pollutant": "pm25", "preference": {"alpha": 0.3, "beta": 0.7}}
```

```json
{
  "solver": "mock", "route_source": "policy", "preference_applied": true,
  "fallback_reason": null, "model_version": "mock-1",
  "recommended": {
    "role": "recommended", "route_source": "policy", "preference_applied": true,
    "edge_ids": [2, 8, 10, 13, 20, 24],
    "geometry": {"type": "LineString", "coordinates": [[106.8, -6.2], [106.8, -6.199], "…", [106.803, -6.197]]},
    "metrics": {"travel_time_min": 8.462, "distance_m": 660.0, "exposure_index_min": 11.0,
                "exposure_ug_min_m3": 126.922, "concentration_coverage": 1.0, "confidence_score": 0.6,
                "edge_count": 6, "imputed_edge_count": 0}
  },
  "comparison": {
    "role": "comparison", "route_source": "baseline", "preference_applied": false,
    "edge_ids": [1, 3, 5, 7, 14, 21],
    "geometry": {"type": "LineString", "coordinates": [[106.8, -6.2], [106.801, -6.2], "…", [106.803, -6.197]]},
    "metrics": {"travel_time_min": 8.462, "distance_m": 660.0, "exposure_index_min": 23.6922,
                "exposure_ug_min_m3": 507.69, "concentration_coverage": 1.0, "confidence_score": 0.6,
                "edge_count": 6, "imputed_edge_count": 0}
  },
  "comparison_summary": {"delta_travel_time_min": 0.0, "delta_exposure_pct": -53.57},
  "deadline_ms": 7490, "solver_elapsed_ms": 0.2,
  "context": {"request_id": "contoh-ti-se-06", "mode": "walk", "preference": {"alpha": 0.3, "beta": 0.7},
              "pollutant": "pm25", "origin_node": 1, "destination_node": 16,
              "origin_snap_distance_m": 0.0, "destination_snap_distance_m": 0.0,
              "graph_version": "jakarta-20261001", "time_window_start": "2026-10-26T09:00:00Z",
              "data_stale": false, "missing_edge_count": 0, "pollution_model_version": "idw-p2-k8"},
  "notes": []
}
```

Cara membaca:
- **Pembanding (rute tercepat):** ruas berpolusi tinggi punya Iₑ = ½ × (60 ÷ 15 + 40 ÷ 25) = 2,8,
  sehingga E = 2,8 × 1,41 menit × 6 = 23,69 indeks·menit.
- **Rekomendasi (β = 0,7):** memilih ruas dengan Iₑ = 1,3, sehingga E = 11,0 dengan waktu tempuh
  yang sama, atau paparan **−53,57%**.
- **Geometri** kedua rute dimulai di simpul asal dan berakhir di simpul tujuan.

## Galat di Railway (2026-10-26)

| Penyedia | Request | Hasil |
|---|---|---|
| `mock` dan `baseline` | Monas → Bundaran HI | 503 `cache_unavailable`, `reason: cache_empty` (cache polusi kosong) |
| `mock` dan `baseline` | Pantai Ancol → laut | 422 `point_not_on_network`, "Titik tujuan: …" |

## Catatan

- **Batas waktu baseline.** `BaselineSolver` menjalankan A* di thread. Bila batas waktu tercapai,
  Backend berhenti menunggu dan menjawab 503, tetapi perhitungan di thread itu tetap selesai di
  latar belakang. Untuk subgraf ukuran kota, waktu A* jauh di bawah batas 8 s.
- **Kontrak `IRouteSolve` HTTP** mengikuti Kode 3.3, ditambah atribut per ruas yang disiapkan
  planner (`travel_time_s`, `exposure_index`, `bidirectional`, `weight_source`). Kontrak ini perlu
  disepakati dengan tim AI (TI-AI-10) sebelum layanan Route Model dibangun.
- **Tahap berikutnya.** TI-SE-07 dan TI-SE-08 menyusun GeoJSON FeatureCollection dan endpoint
  `POST /v1/routes` dari `SolveResult` ini.
