# `solver`: orkestrasi dan integrasi Route Model (TI-SE-06, TI-SE-07)

Lapisan yang memanggil penyelesai rute lewat satu antarmuka, `IRouteSolve`. Penyedianya bisa
diganti lewat konfigurasi, jadi lapisan ini bisa dipakai sebelum Multi-Objective Route Model
final tersedia. Rujukan desain: Tabel 3.6 (`compute_deadline`, `call_route_solve`), Tabel 3.7,
Tabel 3.14–3.16 (`IRouteSolve`, kode galat), Kode 3.3, UT-BE-08, dan IT-K-04.

```
RouteRequest ─► planner.build_solver_input (TI-SE-05)
             ─► compute_deadline: budget 8 000 ms − waktu terpakai − margin 500 ms
             ─► call_route_solve: solver.solve(...), batas waktu deadline_ms + 300 ms
             ─► periksa rute terhadap subgraf
             ─► gagal? fallback baseline bertanda eksplisit (TI-SE-07, bisa dimatikan)
             ─► ambil geometri, hitung metrik ─► SolveResult ─► GeoJSON (Kode 3.2)
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
| `mock` (bawaan) | Deterministik. Pembanding = Dijkstra waktu tempuh; rekomendasi = Dijkstra pada α·τ/T_ref + β·I·τ/E_ref; `route_source: policy`, `model_version: mock-1`. `ROUTE_SOLVER_FAULT` = `timeout`, `error`, `no_route`, atau `invalid` menyuntikkan galat | Pembanding rute deterministik untuk MORL serta pengembangan dan uji integrasi |
| `baseline` | A* rute tercepat dari `route_model` (TI-AI-07), dijalankan di proses ini pada subgraf dan simpul snap yang sama. `route_source: baseline`, `fallback_reason: policy_unavailable` | Rute nyata sebelum MORL tersedia |
| `http` | Klien `POST {ROUTE_SOLVER_URL}/internal/v1/solve`, JSON gzip (Kode 3.3) | Layanan Multi-Objective Route Model produksi |

## Batas waktu dan pembatalan

`call_route_solve` membatasi setiap solver dengan `asyncio.wait_for(deadline_ms + grace)`. Batas ini
hanya dapat terpicu bila event loop tidak tertahan, dan pekerjaan yang sudah lewat batas harus
benar-benar berhenti. Karena itu `mock` dan `baseline` menjalankan pencarian rute lewat
[`cancel.py`](cancel.py) (`run_cancellable`), bukan langsung di dalam metode `async`:

- Pencarian berjalan di thread pekerja, sehingga event loop tetap bebas dan batas waktu terpicu
  tepat waktu.
- Saat batas waktu tercapai, pemanggil dibatalkan dan `CancelToken` di-set. Pencarian memeriksa
  token itu di setiap ekspansi simpul, lalu berhenti dengan `Cancelled`.
- `mock` memeriksa token di loop Dijkstra-nya sendiri. A* `baseline` berada di `route_model` (kode
  tim AI, tidak diubah), jadi token diperiksa lewat dua dict yang dibaca `RoadRouter`: `coords` (per
  ruas saat graf dibangun) dan `arcs` (per simpul yang diekspansi A*). `tests/test_cancel.py` gagal
  bila `route_model` berhenti memakai keduanya.
- Satu tahap tidak memiliki titik pemeriksaan: komponen terhubung kuat (`networkx`) di konstruktor
  `RoadRouter`. Tahap ini membatasi berapa lama thread baseline masih berjalan setelah dibatalkan
  (±0,2 s untuk 180 ribu ruas, ±0,3 s untuk 320 ribu ruas). Respons ke pengguna tetap tepat waktu.

Pada graf uji 400 × 400 (320 ribu ruas, tujuan tidak terjangkau):

| Solver | Batas waktu | Sebelum | Sesudah |
|---|---|---|---|
| `mock` | 30 ms | Menjawab setelah 303 ms; batas waktu tidak terpicu karena pencarian menahan event loop | 503 `solver_timeout` setelah 49 ms; pencarian berhenti |
| `baseline` | 100 ms | 503 setelah 113 ms, tetapi thread terus menghitung sampai 2 235 ms | 503 setelah 113 ms; thread berhenti pada 395 ms |

Penyedia `http` tidak memakai thread: permintaan HTTP dibatalkan langsung oleh `wait_for`.

## Konfigurasi

| Variabel | Bawaan | Keterangan |
|---|---|---|
| `ROUTE_SOLVER_PROVIDER` | `mock` | `mock`, `baseline`, atau `http` |
| `ROUTE_SOLVER_URL` | – | Wajib untuk `http` |
| `ROUTE_SOLVER_FAULT` | `none` | Khusus `mock`: `timeout`, `error`, `no_route`, `invalid` |
| `ROUTE_SOLVER_BUDGET_MS` | `8000` | Batas latensi Backend |
| `ROUTE_SOLVER_RESPONSE_MARGIN_MS` | `500` | Waktu yang disisihkan untuk menyusun respons |
| `ROUTE_SOLVER_GRACE_MS` | `300` | Tambahan batas waktu di atas `deadline_ms` |
| `ROUTE_SOLVER_FALLBACK` | `baseline` | `baseline` = pakai A* bila solver gagal (bertanda eksplisit); `none` = 503 sesuai desain (Tabel 3.12) |
| `ROUTE_SOLVER_FALLBACK_TIMEOUT_MS` | `1500` | Batas waktu fallback |
| `ROUTE_SOLVER_GATEWAY_TIMEOUT_MS` | `10000` | Batas waktu API Gateway; fallback hanya dijalankan bila masih ada sisa waktu di bawah batas ini |

Waktu awal perhitungan diambil dari header `X-Request-Received-At` yang ditambahkan API Gateway.
Bila header itu tidak ada, dipakai waktu permintaan tiba di Backend.

## Fallback saat solver gagal (TI-SE-07)

Dengan `ROUTE_SOLVER_FALLBACK=baseline` (bawaan), bila solver yang dikonfigurasi gagal, Backend
menjalankan A* baseline in-process pada subgraf yang sama. Responsnya diberi penanda eksplisit:

| Penyebab | `fallback_reason` |
|---|---|
| Solver tidak menjawab dalam batas waktu | `model_timeout` |
| Layanan solver tidak dapat dihubungi | `model_unavailable` |
| Solver galat / HTTP 5xx | `model_error` |
| Jawaban tidak sesuai kontrak (JSON rusak, field hilang, ruas di luar subgraf, rute putus, label rute saling bertentangan, rute berlabel baseline bukan rute tercepat) | `model_invalid_response` |
| Solver menyatakan tidak ada rute, padahal planner menjamin tujuan dapat dicapai | `model_no_route` |

Pada respons fallback:
- `route_source` bernilai `"baseline"` dan `preference_applied` bernilai `false`, baik di metadata
  maupun di setiap Feature;
- `solver` bernilai `"baseline"` dan `model_version` bernilai `"astar-baseline"`;
- `fallback` (di `/solve`) mencatat penyedia yang gagal dan penyebabnya;
- `notes` menjelaskan bahwa preferensi tidak diterapkan.

Rute rekomendasi tidak pernah disajikan seolah-olah memperhitungkan preferensi.

Fallback **tidak** dijalankan bila:
- `ROUTE_SOLVER_FALLBACK=none`;
- penyedianya sendiri `baseline`;
- tidak ada sisa waktu di bawah batas API Gateway;
- galatnya berasal dari validasi, snap, atau cache.

Bila fallback ikut gagal, penyebab awalnya yang dilaporkan.

Ini menyimpang dari Tabel 3.12, yang menetapkan 503 bila Route Model tidak dapat dihubungi. Alasannya,
baseline sekarang berjalan di Backend (`route_model`), bukan di layanan Route Model. Perilaku desain
tetap tersedia dengan `ROUTE_SOLVER_FALLBACK=none`.

## Pemeriksaan label rute

Label pada jawaban solver diperiksa, tidak langsung dipercaya, agar rute tidak pernah ditampilkan
dengan asal-usul yang salah. Pemeriksaan ini berlaku untuk semua penyedia:

- **Label harus konsisten** (`check_provenance`):
  - `route_source: policy` wajib `preference_applied: true` dan tanpa `fallback_reason`;
  - `route_source: baseline` wajib `preference_applied: false` dan memiliki `fallback_reason`.

  Bila layanan HTTP tidak mengirim `preference_applied`, nilainya diturunkan dari `route_source`.
- **Rute berlabel baseline harus rute tercepat** (`check_fastest`). Ini berlaku untuk rute
  pembanding, dan untuk rute rekomendasi bila `route_source: baseline`. Backend menghitung waktu
  tempuh tercepat subgraf sendiri dengan Dijkstra ([`paths.py`](paths.py)). Rute lain dengan waktu
  yang sama juga diterima. Rute rekomendasi `policy` boleh lebih lambat, karena preferensi dapat
  menukar waktu dengan paparan.
- **Waktu.** Pencarian rute tercepat berjalan bersamaan dengan panggilan solver, di thread yang
  dapat dibatalkan, sehingga biasanya tidak menambah latensi. Pencarian ini dibatasi
  `deadline_ms + grace`; bila lewat, hasilnya `solver_timeout`. Bila solver gagal lebih dulu,
  pencarian dihentikan.
- **Pengecualian.** Pencarian ini tidak dijalankan untuk penyedia `baseline` in-process, karena
  rutenya sudah rute tercepat menurut konstruksinya.

Pelanggaran dianggap `invalid_solver_response`: fallback dengan `model_invalid_response`, atau 503
bila tidak ada fallback.

## Pencatatan (log)

Satu baris per permintaan di logger `solver` (format `log_config`: teks lokal, JSON di Railway):

```
route_inference request_id=… solver=http outcome=fallback route_source=baseline model_version=astar-baseline
  fallback_reason=model_timeout mode=walk alpha=0.4 beta=0.6 deadline_ms=1409 inference_ms=0.9
  primary_ms=1513.0 reason=solver_timeout detail='no reply within 1509 ms'
```

- `outcome` bernilai `ok`, `fallback`, atau `failed`. `ok` dicatat di level INFO, yang lain WARNING.
- Penyebab teknis (exception, HTTP status) hanya ada di `detail`; respons ke pengguna memakai pesan
  umum.
- **Koordinat tidak pernah dicatat** (PNF-06).

## Respons galat

Pesan galat ke pengguna bersifat umum ("Layanan penentuan rute tidak merespons tepat waktu. Silakan
coba lagi."). Nama exception, kode HTTP, dan URL layanan tidak pernah dikirim ke pengguna.
Respons galat di bawah muncul bila tidak ada fallback.

| Status | `code` | `reason` | Kapan |
|---|---|---|---|
| 503 | `route_service_unavailable` | `solver_timeout` | Solver tidak menjawab dalam `deadline_ms + grace` |
| 503 | `route_service_unavailable` | `solver_unreachable` | Layanan solver tidak dapat dihubungi |
| 503 | `route_service_unavailable` | `solver_error` | Solver gagal, HTTP 5xx/4xx, atau melempar galat apa pun |
| 503 | `route_service_unavailable` | `invalid_solver_response` | Jawaban melanggar kontrak: ruas tidak ada di subgraf, tidak bersambung, arah dilarang, format salah, label rute bertentangan, atau rute berlabel baseline bukan rute tercepat |
| 503 | `route_service_unavailable` | `solver_no_route` | Solver menyatakan tidak ada rute, termasuk saat tidak ada sisa waktu untuk fallback. Planner hanya meneruskan subgraf yang tujuannya dapat dicapai, jadi ini kegagalan solver, bukan perjalanan tanpa rute |
| 503 | `route_service_unavailable` | `deadline_exceeded` | Batas latensi habis sebelum solver dipanggil |
| 422 | `no_route_found` | `same_node` | Titik asal dan tujuan ter-snap ke simpul yang sama |

Galat validasi (400), wilayah studi, dan snap (422), serta cache (503 `cache_unavailable`)
diteruskan apa adanya dari planner (TI-SE-05).

## Endpoint

Keduanya menerima `RouteRequest` yang sama dengan `/v1/routes/solver-input`; contoh tersedia di `/docs`.

| Endpoint | Respons |
|---|---|
| `POST /v1/routes/solve` | `SolveResult` (JSON internal, TI-SE-06) |
| `POST /v1/routes/plan` | GeoJSON FeatureCollection (`application/geo+json`, Kode 3.2, TI-SE-07): dua Feature `recommended`/`comparison` dengan `route_source`, `preference_applied`, metrik, dan `edge_ids`; serta `metadata` berisi `route_source`, `fallback_reason`, `model_version` {`downscaling`, `route_policy`}, `data_stale`, `comparison`, `notes`, dan `disclaimer` |

Endpoint publik `POST /v1/routes` (TI-SE-08, [`../public_api`](../public_api/README.md)) memberikan
respons yang sama dengan `/plan`. `/solve` dan `/plan` tetap sebagai endpoint inspeksi dan tidak
diteruskan oleh API Gateway. Metadata GeoJSON kini juga memuat `crs`, `coordinate_order`, `units`,
dan `exposure_index_definition`.

**Keadaan saat ini.** Selama cache polusi Railway masih kosong, permintaan valid menjawab 503
`cache_empty`. Hasil 200 dibuktikan di basis data lokal: [VERIFICATION.md](VERIFICATION.md) (TI-SE-06) dan
[VERIFICATION_TI-SE-07.md](VERIFICATION_TI-SE-07.md) (integrasi model, fallback, GeoJSON).

## Menguji

Dari `src/backend/solver`, dengan venv dan dependensi dari [`../pollution`](../pollution/README.md#menguji):

```bash
pytest -m "not cacheflow"                                                    # tanpa DB → 93 passed
TEST_DATABASE_URL=postgresql://test:test@localhost:55432/test pytest         # semua → 99 passed
```

- **Uji kontrak** (`tests/test_contract.py`) menjalankan ketiga penyedia (mock, baseline, dan HTTP
  ke layanan tiruan `httpx.MockTransport`) tanpa layanan AI.
- **Uji integrasi** (`tests/test_model_service.py`) menjalankan server Route Model tiruan
  sungguhan (uvicorn di port lokal) untuk jawaban valid, malformat, lambat, galat, dan layanan
  mati.
