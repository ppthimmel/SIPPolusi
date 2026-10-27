# `public_api`: API publik SIPPolusi (TI-SE-08)

Kontrak lintas komponen versi 1.0.0 (TI-SE-01), skema JSON dan pemetaan cache:
[`data-contracts-TI-SE-01.md`](../../../docs/software/data-contracts-TI-SE-01.md).

Dua endpoint yang dipakai Frontend lewat API Gateway, dengan kontrak yang stabil dan
terdokumentasi. Rujukan desain: Tabel 3.14–3.16, Kode 3.1 dan 3.2, `get_exposure_surface` di
Tabel 3.6, dan Subbab 3.1 (skala warna heatmap).

| Endpoint | Interface | Isi |
|---|---|---|
| `POST /v1/routes` | IRouteQuery | RouteRequest (Kode 3.1) → GeoJSON rute rekomendasi dan rute pembanding (Kode 3.2) |
| `GET /v1/exposure-surface` | IExposureSurface | bbox + zoom → GeoJSON ruas atau sel grid untuk heatmap |

Kedua endpoint berjalan di Cache API (`osm.app`) dan memakai planner (TI-SE-05) serta solver
(TI-SE-06, TI-SE-07) yang sudah ada. API Gateway meneruskan **hanya** dua path ini (exact match)
ke Cache API, dengan rate limit per alamat IP. Endpoint inspeksi `/v1/routes/solve`, `/plan`, dan
`/solver-input`, serta baca internal `/v1/osm/*` dan `/v1/pollution/*`, tidak dapat diakses lewat
gateway (TI-DO-05, lihat [`src/gateway/README.md`](../../gateway/README.md)).

## Bukti untuk issue

| Bukti | Berkas |
|---|---|
| Spesifikasi OpenAPI (request, respons sukses, respons galat, satuan, CRS) | [`openapi.json`](openapi.json); juga `/docs` di Cache API |
| Koleksi uji | [`smoke.py`](smoke.py) (dapat dijalankan ke URL mana pun) dan [`requests.http`](requests.http) (VS Code REST Client / JetBrains) |
| Contoh respons GeoJSON | [`examples/`](examples/): `route_policy`, `route_fallback`, `surface_edges_z15`, `surface_grid_z13`, `errors.json` |
| Hasil uji | [`VERIFICATION.md`](VERIFICATION.md) |

## Koordinat dan satuan

- **CRS.** Semua koordinat adalah [lon, lat] WGS 84 (`urn:ogc:def:crs:OGC::CRS84`), sesuai RFC 7946.
  RFC 7946 menghapus member `crs` di tingkat atas, jadi CRS dinyatakan di `metadata.crs` dan
  `metadata.coordinate_order` agar GeoJSON tetap valid bagi pustaka peta.
- **Satuan.** `metadata.units` memetakan setiap properti numerik ke satuannya:

  | Properti | Satuan |
  |---|---|
  | `travel_time_min`, `delta_travel_time_min` | menit |
  | `distance_m` | meter |
  | `exposure_index_min` | indeks·menit (E(R) = Σ Iₑ·τₑ) |
  | `exposure_ug_min_m3` | µg·min/m³ (konsentrasi polutan acuan × menit) |
  | `exposure_index`, `exposure_index_max` | indeks tanpa satuan |
  | `pm25_ugm3`, `no2_ugm3` | µg/m³ |
  | `concentration_coverage`, `confidence_score` | pecahan 0–1 |
  | `delta_exposure_pct` | % |

- **Indeks paparan** (`metadata.exposure_index_definition`, Subbab 3.3.1):
  I = ½ · (PM2.5 / 15 + NO2 / 25), dengan pedoman WHO 2021. Nilai 1,0 berarti tepat pada pedoman.
  Bila hanya satu polutan tersedia, indeks dihitung dari polutan itu saja.

## `POST /v1/routes`

Request (`Content-Type: application/json`, maks. 16 KiB):

```json
{"origin": {"lat": -6.1754, "lon": 106.8272}, "destination": {"lat": -6.1950, "lon": 106.8230},
 "mode": "walk", "pollutant": "pm25", "preference": {"alpha": 0.5, "beta": 0.5}}
```

| Field | Aturan |
|---|---|
| `origin`, `destination` | `lat` −90…90, `lon` −180…180; harus di wilayah studi (daratan DKI Jakarta) |
| `mode` | `walk` atau `bike` |
| `pollutant` | `pm25` (bawaan) atau `no2`; polutan acuan untuk `exposure_ug_min_m3` |
| `preference` | `alpha`, `beta` masing-masing 0–1, `alpha + beta = 1` (toleransi 1e-6) |

Respons 200 (`application/geo+json`, `Cache-Control: no-store` karena berisi koordinat perjalanan):

- **`features[0]`** adalah rute rekomendasi (`role: recommended`) dan **`features[1]`** rute
  tercepat sebagai pembanding (`role: comparison`). Keduanya berupa LineString searah perjalanan
  dengan properti `route_source`, `preference_applied`, metrik di atas, dan `edge_ids`.
- **`metadata`** berisi `request_id`, `mode`, `pollutant`, `preference`, `route_source`,
  `preference_applied`, `fallback_reason`, `time_window_start`, `data_stale`,
  `missing_edge_count`, `graph_version`, `model_version` {`downscaling`, `route_policy`},
  `solver`, `comparison` {`delta_travel_time_min`, `delta_exposure_pct`}, `notes`, `crs`,
  `coordinate_order`, `units`, `exposure_index_definition`, dan `disclaimer`.
- **Fallback** tetap 200. Bila Route Model gagal, rute rekomendasi adalah rute tercepat dengan
  `route_source: "baseline"`, `preference_applied: false`, dan `fallback_reason` (`model_timeout`,
  `model_unavailable`, `model_error`, `model_invalid_response`, `model_no_route`). `notes`
  menjelaskannya kepada pengguna.
- **Label rute diperiksa.** `route_source`, `preference_applied`, dan `fallback_reason` dari model
  harus konsisten, dan setiap rute berlabel baseline harus rute tercepat. Jawaban yang melanggar
  dianggap `model_invalid_response`, jadi rute tidak pernah ditampilkan dengan asal-usul yang salah
  (lihat [`../solver/README.md`](../solver/README.md#pemeriksaan-label-rute)).

## `GET /v1/exposure-surface`

| Parameter | Aturan |
|---|---|
| `bbox` | `min_lon,min_lat,max_lon,max_lat` (WGS 84), wajib |
| `zoom` | 0–22, wajib |
| `pollutant` | `combined` (bawaan), `pm25`, atau `no2`; hanya dicatat di `metadata.layer` |

| Zoom | Feature | Batas bbox |
|---|---|---|
| ≥ 14 | Satu LineString per ruas: `edge_id`, `exposure_index`, `pm25_ugm3`, `no2_ugm3`, `confidence_score`, `estimation_source` | 25 km² (desain) |
| 13 | Polygon sel 250 m | 10 000 sel (±625 km²) |
| 12 | Polygon sel 500 m | 10 000 sel (±2 500 km²; seluruh DKI ±4 400 sel) |
| ≤ 11 | Polygon sel 1 km | 10 000 sel |

- **Satu respons memuat ketiga nilai** (indeks gabungan, PM2.5, NO2), sehingga Frontend dapat
  mengganti lapisan tanpa request baru (Tabel 3.14).
- **Sel grid** memuat `cell_id`, `edge_count`, `exposure_index` dan `exposure_index_max`,
  `pm25_ugm3`, `no2_ugm3`, serta `confidence_score`. Rata-rata dihitung berbobot panjang ruas,
  hanya atas ruas yang memiliki nilai, sehingga nilai yang hilang tidak dianggap nol. Ruas masuk
  ke sel yang memuat titik tengah kotak pembatasnya.
- **Grid tetap** dalam derajat (ukuran meter dikonversi pada lintang Jakarta, berjangkar di
  lon 0, lat 0), jadi `cell_id` dan nilai sel tidak berubah saat peta digeser. Sel di tepi bbox
  tetap utuh karena data dibaca dari envelope yang diselaraskan ke grid.
- **Data yang dipakai** adalah time window `complete` terakhir dan hanya ruas dari `graph_version`
  aktif.
- **`metadata.exposure_index_classes`** = `[0.5, 1.0, 2.0, 3.0]`, yaitu batas kelas skala warna
  (Subbab 3.1).
- **Cache.** Body heatmap tidak memuat `request_id`. ID permintaan hanya ada di header
  `X-Request-ID`, jadi body yang sama aman dipakai ulang untuk permintaan lain. Respons diberi:
  - `Cache-Control: public, no-cache`;
  - `ETag` lemah atas versi graf, time window (awal, waktu selesai, versi model), `data_stale`,
    dan parameter permintaan.

  Browser atau cache wajib memvalidasi ulang dengan `If-None-Match` sebelum memakai respons yang
  tersimpan. Bila tidak ada yang berubah, jawabannya **304** tanpa menjalankan query heatmap.
  Begitu time window complete yang lebih baru ada (atau window ditulis ulang, atau data menjadi
  kedaluwarsa), ETag berubah dan data baru langsung dikirim. Respons galat tidak diberi header
  cache.

  Ini menyimpang dari Tabel 3.15 (`max-age` sampai akhir time window): `max-age` membuat cache
  menyimpan window lama sampai satu jam setelah window baru selesai (masukan QA).

## Galat

Semua galat memakai skema `{"error": {"code", "message", "request_id", "reason"?}}` dengan
`Content-Type: application/json`. `request_id` sama dengan header `X-Request-ID`.

| Status | `code` | `reason` | Kapan | Sumber |
|---|---|---|---|---|
| 400 | `invalid_request` | – | Skema salah, JSON rusak, α/β di luar [0, 1], α + β ≠ 1, moda/polutan tidak dikenal, bbox salah atau terlalu luas untuk zoom, zoom di luar 0–22 | Cache API |
| 405 | `method_not_allowed` | – | Metode HTTP lain | Cache API |
| 413 | `payload_too_large` | – | Body > 16 KiB | Gateway / Cache API |
| 422 | `outside_study_area` | – | Titik di luar DKI Jakarta | Cache API |
| 422 | `point_not_on_network` | – | Tidak ada simpul yang dapat dilalui dalam 200 m | Cache API |
| 422 | `no_route_found` | `same_node` | Asal dan tujuan ter-snap ke simpul yang sama | Cache API |
| 429 | `rate_limited` | – | Rate limit per alamat IP (Tabel 3.15): `/v1/routes` 20/menit, burst 5; `/v1/exposure-surface` 60/menit, burst 10. Header `Retry-After` (3 atau 1 detik) | Gateway |
| 500 | `internal_error` | – | Galat tak terduga; detail hanya di log | Cache API |
| 502 | `upstream_unavailable` | – | Cache API tidak dapat dihubungi | Gateway |
| 503 | `cache_unavailable` | `cache_empty`, `database_unavailable`, `query_timeout` | Cache kosong (belum ada time window complete) atau tidak dapat dibaca | Cache API |
| 503 | `route_service_unavailable` | `solver_timeout`, `solver_unreachable`, `solver_error`, `invalid_solver_response`, `solver_no_route`, `deadline_exceeded` | Route Model gagal dan fallback tidak tersedia. `solver_no_route`: model tidak menemukan rute padahal planner sudah memastikan tujuan dapat dicapai | Cache API |
| 504 | `upstream_timeout` | – | Cache API tidak menjawab dalam 10 s | Gateway |

Galat 400 dan 413 dikembalikan tanpa membaca cache. Pesan galat tidak pernah memuat nama
exception, URL layanan, atau detail koneksi basis data.

## Batas ukuran request

`BodySizeLimit` ([`limits.py`](limits.py)) menolak body di atas `API_MAX_BODY_BYTES` (16 KiB)
dengan 413 sebelum body sampai ke parser:

- bila `Content-Length` yang dinyatakan melebihi batas, permintaan langsung ditolak;
- selain itu, byte yang benar-benar diterima selalu dihitung saat body dibaca, apa pun
  header-nya (dengan atau tanpa `Content-Length` atau `Transfer-Encoding`, atau body lebih
  panjang dari yang dinyatakan). Begitu melewati batas, permintaan ditolak.

API Gateway menerapkan batas yang sama (`client_max_body_size 16k`) pada `POST /v1/routes`.

## Spesifikasi OpenAPI

`/docs` dan `/openapi.json` di Cache API menampilkan semua endpoint. [`openapi.json`](openapi.json)
hanya memuat kontrak publik (path yang diteruskan gateway). Perbarui setelah mengubah endpoint:

```bash
python -m public_api.openapi --write
```

`tests/test_openapi.py` gagal bila berkas itu tidak sama dengan kode. Dokumen juga diperbaiki
agar sesuai perilaku aplikasi:

- galat validasi didokumentasikan sebagai 400 `ErrorResponse` (bukan 422 `HTTPValidationError`
  bawaan FastAPI);
- setiap operasi punya respons `default` dengan skema galat seragam;
- contoh respons mempertahankan nilai `null`.

## Koleksi uji

```bash
python -m public_api.smoke --base-url http://localhost:8001     # Cache API lokal (docker run -p 8001:8000)
python -m public_api.smoke --base-url http://localhost:8080     # lewat API Gateway lokal
python -m public_api.smoke --export-http                        # perbarui requests.http
```

Ada 17 kasus: rute valid (jalan kaki dan sepeda), validasi (α + β, moda, lintang, JSON rusak),
413, wilayah studi, titik di laut, asal = tujuan, metode salah, heatmap pada zoom 15, 12, dan 11,
serta bbox/zoom tidak valid. Kasus yang membaca cache polusi juga menerima 503 `cache_empty`,
karena cache Railway belum terisi. Setiap respons diperiksa terhadap skema dan harus membawa
tepat satu `X-Request-ID`. Untuk rute dan galat, header itu harus sama dengan `request_id` di body;
body heatmap tidak memuat `request_id`.

## Konfigurasi

| Variabel | Bawaan | Keterangan |
|---|---|---|
| `API_MAX_BODY_BYTES` | `16384` | Batas ukuran body (413) |
| `API_SURFACE_EDGE_MAX_KM2` | `25` | Luas bbox terbesar untuk zoom ≥ 14 |
| `API_SURFACE_MAX_CELLS` | `10000` | Jumlah sel terbanyak untuk zoom < 14 |
| `API_CORS_ORIGINS` | kosong | Origin Frontend yang diizinkan CORS, dipisah koma; kosong = CORS mati |

## Menguji

Dari `src/backend/public_api`, dengan venv dan dependensi dari [`../pollution`](../pollution/README.md#menguji):

```bash
pytest -m "not cacheflow"                                                    # tanpa DB → 119 passed
TEST_DATABASE_URL=postgresql://test:test@localhost:55432/test pytest         # semua → 126 passed
```

Uji `cacheflow` memakai PostGIS lokal ([`../testdb`](../testdb/docker-compose.yml)) dan kode Data
Worker untuk menulis graf dan time window. Uji ini memeriksa ruas per zoom, agregasi grid terhadap
perhitungan independen di Python, versi graf aktif, window `writing`, serta contoh di `examples/`.
Untuk menulis ulang contoh, jalankan dengan `UPDATE_EXAMPLES=1`.
