# `planner`: RouteRequest → masukan solver (TI-SE-05)

Modul Backend yang menerjemahkan `RouteRequest` menjadi subgraf siap-selesaikan untuk
Multi-Objective Route Model. Langkahnya mengikuti Gambar 3.9 (R5–R11) dan Tabel 3.6:

1. **R5 `validate_route_request` + `resolve_mode`:** skema, rentang koordinat, wilayah studi,
   α dan β ∈ [0, 1] dengan |α + β − 1| ≤ 10⁻⁶, moda, dan polutan. Semua dicek **sebelum**
   cache dibaca.
2. **R6–R9:** `read_road_graph(bbox, mode)` dan `read_pollution_weight(bbox, "latest")` dibaca
   **bersamaan**, keduanya untuk versi graf aktif yang sama.
3. **R10 `assemble_request_subgraph`:** snap titik ke simpul yang **layak**, lalu ambil bagian
   graf yang dapat dicapai dari simpul asal (lihat
   [`../osm/README.md`](../osm/README.md#snap-titik-asal-dan-tujuan)).
4. **R11 `attach_edge_weights`:** setiap ruas mendapat waktu tempuh, indeks paparan,
   confidence score, dan sumber bobotnya.

Memanggil Multi-Objective Route Model (`IRouteSolve`, batas waktu, fallback) adalah TI-SE-06.

## Endpoint

`POST /v1/routes/solver-input` menerima `RouteRequest` (Kode 3.1) dan mengembalikan masukan
solver. `?summary=true` menghilangkan daftar `nodes` dan `edges`. Contoh request dan galat
tersedia di `/docs`.

```json
{
  "origin":      {"lat": -6.1754, "lon": 106.8272},
  "destination": {"lat": -6.1950, "lon": 106.8230},
  "mode": "walk",
  "pollutant": "pm25",
  "preference": {"alpha": 0.5, "beta": 0.5}
}
```

## Isi masukan solver

| Bagian | Isi |
|---|---|
| `context` (*route context*) | moda, kecepatan rujukan (1,3 / 4,2 m/s), preferensi α/β, polutan, bbox, titik asal/tujuan beserta simpul hasil snap dan jarak snap-nya, `same_node` |
| `weights` (status ketersediaan bobot) | time window yang dipakai, `data_stale`, umur (menit), jumlah ruas, ruas dengan estimasi, `missing_edge_count`, median yang dipakai mengisi (`imputed_exposure_index`), `concentration_missing_count`, baris yang tersaring (`excluded_rows`) |
| `source` (metadata sumber) | `graph_version`, `osm_timestamp`, versi model polusi, `coverage_ratio`, jumlah ruas per `estimation_source`/`background_source`, baris graf yang ditolak validasi |
| `nodes` | `id`, `lon`, `lat` |
| `edges` | per ruas: `u`, `v`, `length_m`, `oneway`, `bidirectional` (moda ini boleh v → u), `travel_time_s` = panjang ÷ kecepatan, `exposure_index`, `confidence_score`, `weight_source`, `concentration_ugm3` (polutan yang diminta), `pm25_ugm3`, `no2_ugm3`, `estimation_source` |

**Bobot yang hilang tidak pernah dianggap nol:**

| Kondisi | Perlakuan |
|---|---|
| Ruas punya estimasi | `weight_source: "estimate"` |
| Ruas tanpa estimasi (tidak ada baris, atau `exposure_index` kosong) | `exposure_index` = **median** estimasi di dalam bbox, `confidence_score: 0`, `weight_source: "imputed_median"`, dan dihitung di `missing_edge_count` (UT-BE-07) |
| `concentration_ugm3`, `pm25_ugm3`, `no2_ugm3` yang tidak diketahui | Tetap `null`, tidak pernah diisi |
| Tidak ada satu pun estimasi di bbox (tidak ada median) | 503 `cache_unavailable`, `reason: cache_empty` |
| Time window kedaluwarsa (> 3 jam) | Tetap dipakai dengan `data_stale: true` |

Baseline rute tim AI (TI-AI-07) menghitung paparan dari konsentrasi satu polutan dan
membiarkan nilai yang hilang tetap kosong, sedangkan desain memakai indeks gabungan yang diisi
median. Karena itu keduanya disediakan per ruas: `concentration_ugm3` untuk baseline,
`exposure_index` untuk desain.

## Kesalahan domain

| Status | `code` | Kapan | Contoh di `/docs` |
|---|---|---|---|
| 400 | `invalid_request` | Skema salah, koordinat di luar rentang, α atau β di luar [0, 1], α + β ≠ 1, moda atau polutan tidak dikenal | *α + β ≠ 1*, *mode "car"* |
| 422 | `outside_study_area` | Titik asal atau tujuan di luar daratan DKI Jakarta; pesan menyebut "Titik asal" atau "Titik tujuan" | *Tujuan Kepulauan Seribu* |
| 422 | `point_not_on_network` | Tidak ada simpul layak untuk moda itu dalam 200 m dari titik asal atau tujuan | *Pantai Ancol → laut* |
| 422 | `no_route_found` | Ada kandidat simpul, tetapi tidak ada pasangan asal–tujuan yang terhubung di subgraf | dicakup tes; di data Jakarta tidak ditemukan titik nyata dengan radius 200 m |
| 503 | `cache_unavailable` + `reason` | `cache_empty` (belum ada graf aktif / time window `complete`, atau tidak ada estimasi di bbox), `database_unavailable`, `query_timeout` | Semua permintaan valid selama cache polusi Railway kosong |

Galat perjalanan (422) dilaporkan lebih dulu daripada galat cache polusi (503), karena pengguna
bisa menindaklanjutinya dengan memindahkan titik. Kedua cache tetap dibaca bersamaan.

**Keadaan saat ini.** Selama cache polusi di Railway masih kosong, permintaan yang valid
menghasilkan 503 `cache_empty`. Uji yang berhasil (200) berjalan di basis data lokal yang diisi
Data Worker (lihat [VERIFICATION.md](VERIFICATION.md)).

## Menguji

Dari `src/backend/planner`, dengan venv dan dependensi dari [`../pollution`](../pollution/README.md#menguji):

```bash
pytest -m "not cacheflow"                                                    # unit → 35 passed
TEST_DATABASE_URL=postgresql://test:test@localhost:55432/test pytest         # semua → 37 passed
```

Uji `cacheflow` memakai basis data uji lokal ([`../testdb`](../testdb/docker-compose.yml)).
