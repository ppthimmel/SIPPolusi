# Laporan Uji Integrasi End-to-End (TI-SE-10)

Tanggal eksekusi: 2026-10-27, 13:10–13:12 UTC, pada `dev` terkini beserta perbaikan defect yang ditemukan uji ini. Pipeline yang diuji:

```
Data Worker (portal SPKU → QC → downscaling) → Spatial Pollution Cache DB / OSM Cache DB
  → Cache API (planner, solver, API publik) → API Gateway → Frontend
                        ↘ Route Model (stub IRouteSolve)
```

**Hasil: 63 dari 63 pemeriksaan lulus.** Defect D-01, D-02, D-06, dan D-07 ditemukan oleh uji ini dan
sudah diperbaiki di branch yang sama; D-03, D-04, dan D-05 tertutup oleh perubahan di `dev`. Tidak ada
defect terbuka. Matriks
lengkap: [MATRIKS.md](MATRIKS.md); data mentah: [hasil/](hasil/).

## 1. Versi yang diuji

| Komponen | Versi |
|---|---|
| Aplikasi | commit `e56ea81` di `feat/ti-se-10` = `dev` `f15c8f9` + harness `tests/e2e/` + perbaikan D-01 (`9eba0bd`), D-07 (`d5c5dea`), D-02 (`e834e02`), D-06 (`e56ea81`); tree bersih saat eksekusi |
| Data Worker | `src/data_worker` `c9fd83d` (termasuk TI-SE-02 #63, TI-DO-07 #57, TI-AI-05 #64, TI-DO-08 #62, #66), image `sippolusi-e2e-worker` |
| Cache API | `src/backend` `d5c5dea` (TI-SE-04 sampai 08, #61, TI-DO-08, perbaikan D-01 dan D-07), image `sippolusi-e2e-cache_api` |
| API Gateway | `src/gateway` `e834e02` (perbaikan D-02), image `sippolusi-e2e-gateway` |
| Frontend | `src/frontend` `e56ea81` (TI-SE-09 beserta perbaikan QA, D-07, D-06), image `sippolusi-e2e-frontend` |
| Basis data | PostgreSQL 18.6 + PostGIS 3.6.4 (`postgis/postgis:18-3.6`, sama dengan Railway) |
| Model downscaling | `spatial-<hash>` per window (mis. 12:00 `spatial-efaaa52e…`). Untuk kedua polutan, Model Registry tidak dikonfigurasi di harness, sehingga keduanya fallback ke IDW `idw-p2-k8` dengan `fallback_reason: resolve_registry:ValueError` |
| Model rute | `stub-e2e-1` (stub IRouteSolve, `tests/e2e/route_model_stub.py`); fallback Backend `astar-baseline`. MORL belum ada (TI-AI-10/11) |
| Dataset | `c9503fc53769` ([fixture/manifest.json](../../tests/e2e/fixture/manifest.json)): graf `jakarta-20261005` (OSM 2026-10-05), bbox Jakarta Pusat 106,80–106,845 BT, 6,155–6,20 LS (21 987 ruas, 17 334 simpul); 129 stasiun SPKU; 705 observasi PM2.5/NO2 untuk 12:00, 13:00, 14:00 UTC 26 Okt 2026 |

Digest setiap image ada di [hasil/versions.json](hasil/versions.json).

## 2. Lingkungan dan data terkontrol

- Dataset diekspor **read-only** dari basis data Railway (`development`) oleh
  `tests/e2e/fixture/export_fixture.py`. Setiap berkas punya SHA-256 di manifest. Pemuat memeriksa
  checksum sebelum menulis, lalu membuat skema dari kode pemiliknya (`osm_cache.schema_sql`,
  `spku.Store`).
- Seluruh stack dibangun dari checkout ini dan dijalankan di `tests/e2e/docker-compose.yml`
  dengan basis data kosong. Tidak ada berkas `.env` yang dibaca.
- Data Worker berada di jaringan internal tanpa internet:
  - Sumber sekunder (satelit, meteorologi) dimatikan dengan `FEATURES_SOURCES=none`, karena butuh
    internet dan fixture tidak berisi skema `features`. FeatureMatrix dari sumber sekunder tidak ikut
    teruji.
  - Portal SPKU diganti **portal tiruan** (`tests/e2e/spku_portal.py`). Portal ini menyajikan stasiun dan
    observasi fixture dalam format halaman portal asli, sehingga `fetch_ground_truth` menjalankan jalur
    pengambilan sebenarnya, termasuk penjaga lintasannya. Data yang disajikan sama dengan yang sudah
    dimuat. Hasilnya diverifikasi: satu lintasan menjelajahi 124 stasiun dengan 0 baris baru dan 0 revisi,
    dan hash isi tabel observasi serta stasiun identik sebelum dan sesudah siklus. Titik bertanda sensor
    macet (`qc=S`, 39 baris) tidak disajikan, karena bendera itu berasal dari riwayat 48 jam portal dan
    akan terhapus oleh riwayat 3 jam.
- Cache API memakai `LOG_FORMAT=json`, sama seperti di Railway.
- Runner `tests/e2e/run_e2e.py` (Python standar, `docker compose`, `psql`) menjalankan 11 skenario
  berurutan dari keadaan bersih. Dua eksekusi berturut-turut memberi hasil yang sama (63/63).
- Window fixture berasal dari 26 Oktober, jadi pada eksekusi ini datanya sudah lebih dari 3 jam. Respons
  dan UI menandainya `data_stale` ("Data kualitas udara belum diperbarui"). Itu perilaku yang benar, dan
  diperiksa di E2E-04.

## 3. Hasil per acceptance criteria

| Acceptance criteria | Hasil | Bukti |
|---|---|---|
| Skenario sukses membuktikan rute tampil dari data yang diproses Worker hingga frontend | ✅ | E2E-02: siklus Worker exit 0. Portal dijelajahi, lalu Worker menulis window 12:00 (`run_id 20261026T1200Z-spatial-efaaa52e…-c36e8d1a`, 21 987 baris, cakupan 1,0). Setiap baris cache menyimpan `trace_run_id` yang sama. API menjawab 200 dengan `time_window_start`, `model_version.downscaling`, dan `graph_version` yang sama dengan cache, `missing_edge_count: 0`, dan `estimation` `{source: idw, fallback_pollutants: [no2, pm25]}` yang sama dengan laporan Worker. Frontend menampilkan rute dan heatmap ([01](01-sukses-rute-dari-data-worker.jpg), [02](02-sukses-sumber-dan-versi.jpg)). E2E-03/05: window baru langsung dipakai |
| Skenario gagal mencakup sumber data tidak tersedia, cache kosong, inferensi timeout, dan API gagal | ✅ | E2E-04 portal SPKU mati; E2E-01 cache kosong; E2E-06/07 inferensi timeout, galat, label salah, no_route, model mati ([03](03-memuat-saat-model-lambat.jpg), [04](04-fallback-model-timeout.jpg)); E2E-08 Cache API mati dan basis data mati ([05](05-api-gagal-502.jpg)); E2E-05 window sedang ditulis; E2E-09 versi graf tidak cocok |
| Tidak ada kegagalan yang berubah menjadi nilai paparan nol atau rute sukses palsu | ✅ | Setiap respons diperiksa (`no_false_success`): semua 200 punya paparan > 0, semua kegagalan berupa galat terstruktur non-200. Cache tidak berisi indeks atau konsentrasi bernilai nol. Fallback rute selalu bertanda `route_source: baseline`, `preference_applied: false`, `fallback_reason`. Sumber estimasi IDW kini disebut apa adanya di respons dan UI (D-07) |
| Laporan uji menyebutkan versi aplikasi, model, dataset/cache, hasil, dan defect terbuka | ✅ | Bagian 1, 4, dan 5 laporan ini |

### Penelusuran korelasi (contoh, teredaksi)

Satu permintaan dapat ditelusuri dari frontend sampai inferensi, dan dari cache sampai siklus Worker
([hasil/correlation.txt](hasil/correlation.txt)):

```
Worker   run_id=20261026T1200Z-spatial-efaaa52e…-c36e8d1a rows_written=21987 coverage=1.0
cache    edge_pollution.trace_run_id=20261026T1200Z-spatial-efaaa52e…-c36e8d1a  (→ pollution.inference_run)
gateway  {"message":"POST /v1/routes 200","request_id":"1afb7900d615ab222a8197d5778e8476","duration_s":0.333, ...}
API      {"level":"info","logger":"solver","correlation_id":"1afb7900d615ab222a8197d5778e8476",
          "message":"route_inference request_id=1afb7900… solver=http outcome=ok route_source=policy
          model_version=stub-e2e-1 mode=walk alpha=0.3 beta=0.7 deadline_ms=7342 inference_ms=140.5"}
respons  X-Request-ID = metadata.request_id = 1afb7900…; metadata.time_window_start = 2026-10-26T12:00:00Z
```

ID permintaan yang tampil di UI ([02](02-sukses-sumber-dan-versi.jpg)) juga tercatat di log gateway dan
Cache API. Log semua layanan diperiksa: tidak ada DSN, kata sandi, atau koordinat perjalanan (E2E-11).

## 4. Ringkasan skenario

| Skenario | Hasil |
|---|---|
| E2E-01 cache kosong (5/5) | 503 `cache_unavailable`/`cache_empty` untuk rute dan heatmap; tidak ada nilai yang dikarang. `/health/ready` siap dengan polusi `empty` |
| E2E-02 sukses Worker → frontend (16/16) | Siklus exit 0, fallback IDW bertanda per polutan, `trace_run_id`, log JSON berkorelasi, `missing_edge_count: 0` (D-01 diperbaiki), `metadata.estimation` sesuai Worker (D-07 diperbaiki) |
| E2E-03 window baru dan idempotensi (5/5) | Window 13:00 langsung dipakai; ETag heatmap berubah; Worker tidak menulis ulang window yang sudah complete |
| E2E-04 sumber data tidak tersedia (6/6) | Portal mati: `fetch_ground_truth` `failed`, Worker exit 1 (`NoGroundTruthError`) tanpa menulis window 15:00; API tetap memakai 13:00 dengan `data_stale` yang benar |
| E2E-05 window sedang ditulis (2/2) | Window `writing` tidak terbaca; begitu `complete`, langsung dipakai |
| E2E-06 inferensi gagal, fallback aktif (13/13) | Lambat, galat, label salah, no_route, layanan mati → rute tercepat bertanda baseline dengan `fallback_reason` yang tepat, < 10 s; log `outcome=fallback` dengan penyebab |
| E2E-07 inferensi timeout, fallback mati (3/3) | 503 `solver_timeout` dalam batas gateway; no_route model → 503 `solver_no_route` (bukan 422) |
| E2E-08 API dan basis data gagal (7/7) | Basis data mati → 503 `database_unavailable`, `/health/ready` 503, `/health/live` 200, pulih otomatis. Cache API mati → langsung 502 `upstream_unavailable` dari gateway (D-02 diperbaiki), juga lewat frontend ([05](05-api-gagal-502.jpg)) |
| E2E-09 versi graf dan cache tidak cocok (3/3) | Estimasi dari graf lain diabaikan → 503 `cache_empty`, bukan paparan nol |
| E2E-10 kontrak API publik (1/1) | Koleksi uji 17 kasus (validasi, 413, 422, heatmap) lulus lewat gateway |
| E2E-11 log aman (2/2) | Tanpa rahasia dan tanpa koordinat |

## 5. Defect

### Terbuka

Tidak ada.

### Diperbaiki di branch ini (diverifikasi pada eksekusi ini)

| ID | Defect semula | Perbaikan | Bukti |
|---|---|---|---|
| D-01 | Ruas yang kotak pembatasnya menyentuh bbox permintaan tetapi garisnya tidak, masuk subgraf (`&&`) namun bobotnya tersaring `ST_Intersects`. Ruas itu diisi median wilayah dengan confidence 0, dan UI menampilkan "3 ruas tanpa estimasi" walau semua ruas punya estimasi (Monas → Bundaran HI; ruas 111493, 359388, 370670). Terkonfirmasi juga di Railway oleh QA | `9eba0bd`: `_SQL_WEIGHTS` memakai filter bbox yang sama dengan subgraf (`&&`). Uji regresi `test_weights_cover_every_edge_of_the_road_graph_bbox` (ruas diagonal) gagal dengan query lama dan lulus setelah perbaikan | E2E-02: `missing_edge_count: 0`; UI tanpa catatan ruas tanpa estimasi ([01](01-sukses-rute-dari-data-worker.jpg)). Perlu deploy ke Railway agar hilang di sana |
| D-07 | Fallback downscaling tidak terlihat oleh pengguna: respons hanya membawa `model_version.downscaling: spatial-<hash>`, dan frontend menulis tetap "Model downscaling spasial dari data satelit, meteorologi, dan sensor darat" walau estimasi berasal dari IDW sensor darat | `d5c5dea`: `metadata.estimation` `{source, fallback_pollutants}` dari baris cache ruas rute (OpenAPI dan contoh diperbarui); frontend menulis sumber estimasi sesuai nilai itu | E2E-02: `estimation` = laporan Worker; panel "Sumber estimasi": "Interpolasi dari sensor darat SPKU (IDW); model downscaling tidak tersedia" ([02](02-sukses-sumber-dan-versi.jpg)). Disclaimer di kaki panel juga disesuaikan (`e56ea81`): satelit dan meteorologi disebut hanya bila model downscaling tersedia |

| D-02 | Selama ±10 s setelah Cache API berhenti, gateway menunggu `proxy_connect_timeout` 5 s lalu menjawab 504 `upstream_timeout`, bukan 502 `upstream_unavailable`, karena IP lama masih tersimpan di cache DNS (`resolver … valid=10s`) | `e834e02`: `proxy_connect_timeout` 2 s di jaringan privat; 504 yang koneksi upstream-nya tidak pernah terbuka (`$upstream_connect_time` kosong) dijawab 502 `upstream_unavailable`. Timeout baca tetap 504. Uji bawaan gateway (`docker build --target test`) lulus | E2E-08: Cache API dimatikan lalu langsung `POST /v1/routes` → 502; tangkapan [05](05-api-gagal-502.jpg) diambil segera setelah Cache API berhenti. Kasus alamat tak terjangkau tidak dapat disimulasikan di uji build gateway (Docker Desktop menerima koneksi TCP ke alamat apa pun), sehingga buktinya E2E-08 |
| D-06 | Semua confidence IDW < 0,4 (median 0,30), sehingga seluruh heatmap bergaris "keyakinan rendah"; peta padat dan garis tidak membedakan apa pun | `e56ea81` (frontend). Rumus confidence **tidak** diubah: batas IDW < 0,4 adalah ketentuan desain (Tabel 3.8, `docs/SPATIAL_MODEL_SPEC.md`). Bila seluruh area yang tampil berkeyakinan rendah, peta tidak diberi garis dan legenda menyatakannya sekali; garis tetap dipakai bila keyakinan bervariasi | Peta dan legenda "Keyakinan estimasi rendah di seluruh area yang tampil, jadi peta tidak diberi garis" ([01](01-sukses-rute-dari-data-worker.jpg)); uji unit `allLowConfidence`. Lencana "Keyakinan Rendah" per rute tetap ditampilkan karena memang benar |

### Tertutup oleh perubahan di `dev` (diverifikasi ulang pada eksekusi ini)

| ID | Defect semula | Ditutup oleh | Bukti |
|---|---|---|---|
| D-03 | Korelasi Worker ↔ cache hanya lewat (`time_window_start`, `model_version`); log Cache API berupa teks biasa | TI-AI-05 #64 (`trace_run_id` → `pollution.inference_run`), TI-DO-08 #62 (log JSON dengan `correlation_id`) | E2E-02: `trace_run_id` setiap baris = `run_id` Worker; log Cache API berformat JSON dengan `correlation_id` = `X-Request-ID` ([hasil/correlation.txt](hasil/correlation.txt)) |
| D-04 | `/health` 200 walau `degraded`; tidak ada endpoint readiness | #61, TI-DO-08 #62 (`/health/live`, `/health/ready`) | E2E-08: basis data mati → `/health/ready` 503 (`database: unavailable`), `/health/live` 200. E2E-01: siap dengan polusi `empty` |
| D-05 | `fetch_ground_truth` berstatus `ok` walau kolektor portal `aborted` | TI-DO-07 #57 | E2E-04: portal mati → `fetch_ground_truth` `failed` (`run_status: aborted`), siklus exit 1 |

### Ketergantungan

| Dependensi | Status | Dampak pada uji |
|---|---|---|
| Model Registry dan ST-GNN/LightGBM terdaftar (TI-AI-05, TI-AI-11) | Kode ada di `dev`; registry tidak disertakan di harness | Hanya jalur fallback per polutan yang teruji. Jalur model terdaftar perlu diuji dengan MLflow (atau setara) di stack |
| Multi-Objective Route Model (TI-AI-10/11) | Belum ada layanan | Rute "policy" berasal dari stub yang memenuhi kontrak IRouteSolve; perilaku MORL sebenarnya belum teruji |
| Sumber sekunder (TI-SE-02) | Dimatikan di harness (`FEATURES_SOURCES=none`) | FeatureMatrix dari satelit dan meteorologi tidak teruji; butuh fixture skema `features` |
| Railway | TI-SE-08/09 sudah di-merge; `DOWNSCALE_WRITE_CACHE=1` aktif di `development` sejak 27 Okt; `public_api.smoke` lewat gateway Railway 17/17 lulus (QA) | Uji asap Railway sudah dilakukan; D-01 terkonfirmasi di sana. Perbaikan D-01, D-02, D-06, dan D-07 berlaku di Railway setelah PR ini di-merge dan di-deploy; tidak ada variabel lingkungan baru |

## 6. Cara mengulang

```bash
docker compose -f tests/e2e/docker-compose.yml --profile tools build
python3 tests/e2e/run_e2e.py
python3 tests/e2e/report.py
docker compose -f tests/e2e/docker-compose.yml down -v
```

Lihat [tests/e2e/README.md](../../tests/e2e/README.md). Untuk mengganti dataset, jalankan
`export_fixture.py` dengan `SOURCE_DATABASE_URL` (read-only), lalu catat `dataset_version` yang baru di
laporan.
