# `solver`: orkestrasi pemanggilan Route Model (TI-SE-06)

Lapisan yang memanggil penyelesai rute lewat satu antarmuka, `IRouteSolve`. Penyedianya bisa
diganti lewat konfigurasi, jadi lapisan ini bisa dipakai sebelum Multi-Objective Route Model
final tersedia. Rujukan desain: Tabel 3.6 (`compute_deadline`, `call_route_solve`), Tabel 3.7,
Tabel 3.14–3.16 (`IRouteSolve`, kode galat), Kode 3.3, UT-BE-08, dan IT-K-04.

```
RouteRequest ─► planner.build_solver_input (TI-SE-05)
             ─► compute_deadline: budget 8 000 ms − waktu terpakai − margin 500 ms
             ─► call_route_solve: solver.solve(...), batas waktu deadline_ms + 300 ms
             ─► periksa rute terhadap subgraf, ambil geometri, hitung metrik
             ─► SolveResult
```

## Antarmuka `IRouteSolve` ([`contract.py`](contract.py))

```python
class RouteSolver(Protocol):
    name: str
    async def solve(self, request: SolveRequest) -> SolverReply: ...
```

- **`SolveRequest`:** masukan solver dari planner (subgraf, simpul hasil snap, waktu tempuh,
  bobot paparan, preferensi) ditambah `deadline_ms`.
- **`SolverReply`:** jawaban solver.

  | Field | Isi |
  |---|---|
  | `status` | `ok` atau `no_route` |
  | `route_source` | `policy` atau `baseline` |
  | `preference_applied` | Apakah preferensi α/β diterapkan |
  | `fallback_reason` | Alasan fallback, bila ada |
  | `model_version` | Versi model |
  | `recommended`, `comparison` | Masing-masing urutan `edge_id` dari simpul asal ke simpul tujuan |

  Solver gagal dengan melempar `SolverError(reason, message)`.

Solver hanya mengembalikan urutan ruas. Backend lalu:
- **memeriksa** setiap ruas ada di subgraf, bersambung dari simpul asal ke simpul tujuan, dan
  arahnya diizinkan untuk moda itu;
- **menghitung** geometri dan metrik dengan cara yang sama untuk semua penyedia.

## `SolveResult`

| Bagian | Isi |
|---|---|
| `recommended`, `comparison` | `role`, `route_source`, `preference_applied`, `edge_ids`, `geometry` (GeoJSON LineString searah perjalanan), `metrics` |
| `metrics` | `travel_time_min` = T(R); `distance_m`; `exposure_index_min` = E(R) = Σ Iₑ,ₜ·τₑ; `exposure_ug_min_m3` (konsentrasi polutan acuan × waktu, `null` bila ada ruas tanpa konsentrasi); `concentration_coverage`; `confidence_score` (rata-rata berbobot paparan); `edge_count`; `imputed_edge_count` |
| `comparison_summary` | `delta_travel_time_min`, `delta_exposure_pct` terhadap rute pembanding |
| Sumber rute | `solver`, `route_source`, `preference_applied`, `fallback_reason`, `model_version` |
| `context` (untuk tahap berikutnya) | `request_id`, moda, preferensi, polutan, simpul dan jarak snap, `graph_version`, `time_window_start`, `data_stale`, `missing_edge_count`, versi model polusi |
| Lain-lain | `deadline_ms`, `solver_elapsed_ms`, `notes` (fallback, data kedaluwarsa, ruas tanpa estimasi) |

## Penyedia solver

Dipilih dengan `ROUTE_SOLVER_PROVIDER`; endpoint-nya tidak berubah.

| Penyedia | Isi | Kapan dipakai |
|---|---|---|
| `mock` (bawaan) | Deterministik. Pembanding = Dijkstra waktu tempuh; rekomendasi = Dijkstra pada α·τ/T_ref + β·I·τ/E_ref; `route_source: policy`, `model_version: mock-1`. `ROUTE_SOLVER_FAULT` = `timeout`, `error`, `no_route`, atau `invalid` menyuntikkan galat | Pengembangan Backend dan uji integrasi tanpa layanan AI |
| `baseline` | A* rute tercepat dari `route_model` (TI-AI-07), dijalankan di proses ini pada subgraf dan simpul snap yang sama. `route_source: baseline`, `fallback_reason: policy_unavailable` | Rute nyata sebelum MORL tersedia |
| `http` | Klien `POST {ROUTE_SOLVER_URL}/internal/v1/solve`, JSON gzip (Kode 3.3) | Layanan Multi-Objective Route Model produksi |

## Konfigurasi

| Variabel | Bawaan | Keterangan |
|---|---|---|
| `ROUTE_SOLVER_PROVIDER` | `mock` | `mock`, `baseline`, atau `http` |
| `ROUTE_SOLVER_URL` | – | Wajib untuk `http` |
| `ROUTE_SOLVER_FAULT` | `none` | Khusus `mock`: `timeout`, `error`, `no_route`, `invalid` |
| `ROUTE_SOLVER_BUDGET_MS` | `8000` | Batas latensi Backend |
| `ROUTE_SOLVER_RESPONSE_MARGIN_MS` | `500` | Waktu yang disisihkan untuk menyusun respons |
| `ROUTE_SOLVER_GRACE_MS` | `300` | Tambahan batas waktu di atas `deadline_ms` |

Waktu awal perhitungan diambil dari header `X-Request-Received-At` yang ditambahkan API Gateway.
Bila header itu tidak ada, dipakai waktu permintaan tiba di Backend.

## Respons galat

| Status | `code` | `reason` | Kapan |
|---|---|---|---|
| 503 | `route_service_unavailable` | `solver_timeout` | Solver tidak menjawab dalam `deadline_ms + grace` |
| 503 | `route_service_unavailable` | `solver_unreachable` | Layanan solver tidak dapat dihubungi |
| 503 | `route_service_unavailable` | `solver_error` | Solver gagal, HTTP 5xx/4xx, atau melempar galat apa pun |
| 503 | `route_service_unavailable` | `invalid_solver_response` | Jawaban melanggar kontrak: ruas tidak ada di subgraf, tidak bersambung, arah dilarang, atau format salah |
| 503 | `route_service_unavailable` | `deadline_exceeded` | Batas latensi habis sebelum solver dipanggil |
| 422 | `no_route_found` | `solver_no_route` | Solver menyatakan tidak ada rute |
| 422 | `no_route_found` | `same_node` | Titik asal dan tujuan ter-snap ke simpul yang sama |

Galat validasi (400), wilayah studi, dan snap (422), serta cache (503 `cache_unavailable`)
diteruskan apa adanya dari planner (TI-SE-05).

## Endpoint

`POST /v1/routes/solve` menerima `RouteRequest` yang sama dengan `/v1/routes/solver-input`. Contoh
request dan galat tersedia di `/docs`. Endpoint publik `POST /v1/routes` (TI-SE-08) nantinya
menyusun GeoJSON FeatureCollection dari `SolveResult` ini.

**Keadaan saat ini.** Selama cache polusi Railway masih kosong, permintaan valid menjawab 503
`cache_empty`. Hasil 200 dibuktikan di basis data lokal ([VERIFICATION.md](VERIFICATION.md)).

## Menguji

Dari `src/backend/solver`, dengan venv dan dependensi dari [`../pollution`](../pollution/README.md#menguji):

```bash
pytest -m "not cacheflow"                                                    # tanpa DB → 39 passed
TEST_DATABASE_URL=postgresql://test:test@localhost:55432/test pytest         # semua → 42 passed
```

Uji kontrak (`tests/test_contract.py`) menjalankan ketiga penyedia (mock, baseline, dan HTTP ke
layanan tiruan `httpx.MockTransport`) tanpa layanan AI.
