# Bukti Verifikasi TI-SE-08: API publik rute dan visualisasi paparan

Tanggal: 2026-10-26. Endpoint dijalankan di Cache API (`osm.app`) dan diteruskan API Gateway
(`src/gateway`). Data graf dan polusi untuk uji alur ditulis kode Data Worker ke PostgreSQL 18.6 +
PostGIS 3.6.4 lokal. Uji runtime memakai image Cache API yang terhubung ke basis data Railway
(environment `development`), langsung dan lewat image API Gateway.

## Ringkasan per acceptance criteria

| Acceptance criteria | Implementasi | Bukti uji |
|---|---|---|
| Spesifikasi API mendokumentasikan skema request, respons sukses, dan respons galat | [`openapi.json`](openapi.json): skema `RouteRequestIn`, 200 `application/geo+json` (`RouteFeatureCollection`, `SurfaceFeatureCollection`) dengan contoh, dan galat 400/413/422/429/500/502/503/504 serta `default` dengan `ErrorResponse` dan contoh per kode Tabel 3.16. Berkas diperiksa sama dengan kode | `test_openapi.py` (20) |
| GeoJSON valid, memiliki CRS, dan propertinya terdokumentasi | FeatureCollection RFC 7946, koordinat [lon, lat]; `metadata.crs` = `urn:ogc:def:crs:OGC::CRS84` dan `metadata.coordinate_order`. Model respons memakai `extra="forbid"`, sehingga properti yang tidak terdokumentasi membuat uji gagal | `assert_valid_route_geojson`, `test_zoom_14_and_up_returns_edges`, `test_examples_are_what_the_api_answers` |
| Metrik paparan dan waktu memiliki satuan di respons | `metadata.units` untuk setiap properti numerik (menit, meter, indeks·menit, µg·min/m³, µg/m³, pecahan 0–1, %) dan `metadata.exposure_index_definition` | `test_valid_request_returns_route_geojson` (setiap properti numerik punya satuan), `test_units_and_crs_are_documented` |
| Uji API mencakup request valid, validasi gagal, rute tidak tersedia, dan kegagalan upstream | `tests/test_routes.py`, `tests/test_surface.py`, `tests/test_surface_db.py`, koleksi `smoke.py` | Tabel di bawah |

## Hasil uji

| Paket | Hasil |
|---|---|
| `src/backend/public_api` | **111 passed**: 104 tanpa DB (rute 30, permukaan paparan 36, OpenAPI 20, koleksi uji 13, contoh 5) + 7 alur di DB lokal |
| `src/backend/solver`, `planner`, `pollution`, `osm` | 80, 37, 30, 115 passed (osm termasuk 15 uji integrasi ke Railway); `osm.verify`: **LULUS** |
| `src/data_worker` | 126 passed. Lima berkas uji tim lain (scraper) tidak terkumpul karena modul `requests` tidak ada di venv uji; kode `data_worker` tidak berubah |
| Uji tim (`src/backend/tests`, Python 3.11) | 35 passed |
| Container Cache API (Python 3.11) | `osm` 100, `pollution` 20, `planner` 35, `solver` 74, `public_api` 104 passed; uji yang butuh DB dilewati |
| Container API Gateway (`docker build --target test src/gateway`) | Semua lulus, termasuk routing baru, satu `X-Request-ID`, 413/502 JSON |

### `POST /v1/routes`

| Kelompok | Kasus | Hasil |
|---|---|---|
| Request valid | Mock (rute model), fallback karena model galat, gzip, sama dengan `/plan`, data Data Worker | 200 `application/geo+json`, `Cache-Control: no-store`, lolos skema; fallback bertanda `route_source: baseline`, `fallback_reason: model_error` |
| Validasi gagal | α + β ≠ 1, α < 0, moda `car`, polutan `co`, lintang 106, tujuan hilang, preferensi bukan objek, body array, JSON rusak, body kosong, `text/plain`, metode GET | 400 `invalid_request` (405 untuk GET), **cache tidak dibaca** |
| Rute tidak tersedia | Luar wilayah studi, titik jauh dari jalan, asal = tujuan, solver menyatakan tidak ada rute (fallback mati) | 422 `outside_study_area`, `point_not_on_network`, `no_route_found` (`same_node`, `solver_no_route`) |
| Kegagalan upstream | Route Model lambat (fallback mati), Route Model galat, cache kosong, DB tidak terjangkau, query timeout, exception tak terduga | 503 `route_service_unavailable` (`solver_timeout` < 2 s, `solver_error`), 503 `cache_unavailable` (`cache_empty`, `database_unavailable`, `query_timeout`), 500 `internal_error` tanpa detail internal |
| Ukuran request | `Content-Length` 20 KB, chunked 41 KB, chunked kecil, batas 100 B | 413 `payload_too_large` tanpa membaca cache; body kecil diterima |

### `GET /v1/exposure-surface`

| Kasus | Hasil |
|---|---|
| Zoom 15 pada graf 4 × 4 dari Data Worker | 24 ruas LineString dengan geometri `edge_pollution.geom`; NO2 yang hilang tetap `null` |
| Zoom 13, 12, 11 | Sel 250/500/1000 m. Jumlah ruas, rata-rata berbobot panjang (hanya ruas bernilai), dan nilai maksimum **sama dengan perhitungan independen di Python** |
| Versi graf baru tanpa estimasi polusi | 0 Feature (ruas versi lama tidak ditampilkan) |
| Window berstatus `writing` | 503 `cache_unavailable`, `cache_empty` |
| bbox terlalu luas untuk zoom (zoom 15 > 25 km², zoom 13 seluruh Jakarta), bbox salah format/terbalik/NaN, zoom 23 atau −1, polutan `co`, parameter hilang | 400 `invalid_request`, cache tidak dibaca |
| Grid | Sel selaras dan tidak bergeser saat peta digeser; ring Polygon tertutup dan berlawanan arah jarum jam (RFC 7946) |
| `Cache-Control` | `public, max-age` = sisa detik time window satu jam berjalan (3600 / 30 / 1 pada tiga waktu uji); galat tanpa `max-age` |

## Koleksi uji pada deployment

`python -m public_api.smoke` (17 kasus) terhadap image runtime yang terhubung ke Railway:

| Target | Hasil |
|---|---|
| Cache API langsung (`docker run -p 8001:8000 --env-file`) | **17/17 lulus** |
| Lewat API Gateway (image gateway → Cache API) | **17/17 lulus** |

Cache polusi Railway belum memiliki time window `complete`. Karena itu kasus yang membaca polusi
(rute valid, asal = tujuan, heatmap) menjawab 503 `cache_unavailable`/`cache_empty`, yang
diterima koleksi untuk kondisi ini. Validasi, 413, wilayah studi, dan titik di laut dijawab sebelum
membaca cache dan sesuai harapan. Rute dengan data polusi dibuktikan di basis data lokal
(`test_route_on_worker_data`, `examples/route_policy.geojson`).

Uji lewat gateway menemukan dua hal yang lalu diperbaiki:

1. Respons berisi dua header `X-Request-ID`, dari Cache API dan dari gateway. Gateway kini
   menyembunyikan salinan upstream (`proxy_hide_header`), dan uji gateway memeriksa tepat satu
   header.
2. Gateway mengganti `X-Request-ID` dari klien dengan ID-nya sendiri, sesuai desain. Koleksi kini
   memeriksa bahwa header sama dengan `request_id` di body.

## Contoh respons

[`examples/`](examples/) berisi respons sungguhan API pada data Data Worker, dengan request ID
tetap:

| Berkas | Isi |
|---|---|
| `route_policy.geojson` | Rute dari model (`route_source: policy`) |
| `route_fallback.geojson` | Fallback karena model galat (`route_source: baseline`, `fallback_reason: model_error`) |
| `surface_edges_z15.geojson` | 24 ruas pada zoom 15 |
| `surface_grid_z13.geojson` | 4 sel 250 m pada zoom 13 |
| `errors.json` | 400, 413, 422 (tiga kode), 503 `cache_empty`, 503 `solver_error` |

`test_examples_are_what_the_api_answers` memastikan berkas-berkas ini tetap sama dengan jawaban API
(kecuali waktu time window).

## Perubahan API Gateway

- `POST /v1/routes` dan `GET /v1/exposure-surface` (exact match) diteruskan ke Cache API.
  `/v1/routes/*` tetap ke backend dan tidak membuka endpoint inspeksi.
- Header `X-Request-Received-At` (`$msec`) dikirim ke upstream; Cache API menghitung anggaran 8 s
  dari waktu itu.
- `client_max_body_size 16k` pada `/v1/routes`.
- 413, 502, dan 504 dari NGINX kini memakai skema galat seragam (`payload_too_large`,
  `upstream_unavailable`, `upstream_timeout`).
- Hanya satu `X-Request-ID` per respons.

`src/backend/main.py` tidak diubah.

## Catatan

- **Rate limiting (429 `rate_limited`, Tabel 3.15) belum diterapkan di gateway.** Kodenya sudah
  didokumentasikan di OpenAPI dengan catatan itu. Penerapannya butuh alamat IP klien yang
  sebenarnya di belakang edge Railway (`set_real_ip_from`), jadi perlu diputuskan bersama DevOps.
- **CRS ada di `metadata`**, bukan member `crs` tingkat atas. RFC 7946 menghapus member itu, dan
  koordinat GeoJSON selalu CRS84.
- **Kinerja agregasi grid pada data Jakarta penuh belum diukur**, karena cache polusi Railway masih
  kosong. Agregasi berjalan di SQL dengan indeks GiST `edge_pollution.geom` dan dibatasi
  `statement_timeout` 1,5 s (lewat batas itu → 503 `query_timeout`). Perlu diukur setelah Data
  Worker menulis window pertama.
- **CORS mati secara bawaan.** Isi `API_CORS_ORIGINS` dengan domain Frontend saat sudah ada.
- **Respons dikompresi gzip** bila klien mengirim `Accept-Encoding: gzip` (respons ≥ 1 KiB).
