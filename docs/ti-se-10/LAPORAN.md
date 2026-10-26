# Laporan Uji Integrasi End-to-End (TI-SE-10)

Tanggal eksekusi: 2026-10-26, 15:45–15:48 UTC. Pipeline yang diuji:

```
Data Worker (SPKU → QC → downscaling) → Spatial Pollution Cache DB / OSM Cache DB
  → Cache API (planner, solver, API publik) → API Gateway → Frontend
                        ↘ Route Model (stub IRouteSolve)
```

**Hasil: 54 dari 56 pemeriksaan lulus.** Dua pemeriksaan yang gagal adalah defect D-01 dan D-02. Matriks
lengkap: [MATRIKS.md](MATRIKS.md); data mentah: [hasil/](hasil/).

## 1. Versi yang diuji

| Komponen | Versi |
|---|---|
| Aplikasi | commit `a1541b3` di `feat/ti-se-10` (= `feat/ti-se-08` `3f2d7b3` + `feat/ti-se-09` `46a8358`), ditambah harness `tests/e2e/` yang belum di-commit |
| Data Worker | `src/data_worker` dari `dev` (commit terakhir `455d541`), image `sippolusi-e2e-worker` |
| Cache API | `src/backend` `3f2d7b3` (TI-SE-04 sampai 08), image `sippolusi-e2e-cache_api` |
| API Gateway | `src/gateway` `49bda67`, image `sippolusi-e2e-gateway` |
| Frontend | `src/frontend` `46a8358` (TI-SE-09), image `sippolusi-e2e-frontend` |
| Basis data | PostgreSQL 18.6 + PostGIS 3.6 (`postgis/postgis:18-3.6`, sama dengan Railway) |
| Model downscaling | `idw-p2-k8` (fallback IDW). ST-GNN `spatial-downscaling production` **belum terdaftar** di Model Registry (TI-AI-11) |
| Model rute | `stub-e2e-1` (stub IRouteSolve, `tests/e2e/route_model_stub.py`); fallback Backend `astar-baseline`. MORL belum ada (TI-AI-10/11) |
| Dataset | `c9503fc53769` ([fixture/manifest.json](../../tests/e2e/fixture/manifest.json)): graf `jakarta-20261005` (OSM 2026-10-05) bbox Jakarta Pusat 106,80–106,845 BT, 6,155–6,20 LS (21 987 ruas, 17 334 simpul); 129 stasiun SPKU; 705 observasi PM2.5/NO2 untuk 12:00, 13:00, 14:00 UTC 26 Okt 2026 |

Digest setiap image ada di [hasil/versions.json](hasil/versions.json).

## 2. Lingkungan dan data terkontrol

- Dataset diekspor **read-only** dari basis data Railway (`development`) oleh
  `tests/e2e/fixture/export_fixture.py`. Setiap berkas punya SHA-256 di manifest. Pemuat memeriksa
  checksum sebelum menulis, lalu membuat skema dari kode pemiliknya (`osm_cache.schema_sql`,
  `spku.Store`).
- Seluruh stack dibangun dari checkout ini dan dijalankan di `tests/e2e/docker-compose.yml`
  dengan basis data kosong. Tidak ada berkas `.env` yang dibaca.
- Data Worker berada di jaringan internal tanpa internet. Portal SPKU tidak terjangkau dengan sengaja,
  sehingga observasi selalu berasal dari fixture dan hasilnya dapat diulang. Kolektor tercatat
  `aborted`, dan siklus memakai observasi tersimpan.
- Runner `tests/e2e/run_e2e.py` (Python standar, `docker compose`, `psql`) menjalankan 11 skenario
  berurutan dari keadaan bersih. Dua eksekusi berturut-turut memberi hasil yang sama (54/56).

## 3. Hasil per acceptance criteria

| Acceptance criteria | Hasil | Bukti |
|---|---|---|
| Skenario sukses membuktikan rute tampil dari data yang diproses Worker hingga frontend | ✅ | E2E-02: Worker menulis window 12:00 (`run_id 20261026T1200Z-idw-p2-k8-c21c1a50`, 21 987 baris, cakupan 1,0). API menjawab 200 dengan `time_window_start`, `model_version.downscaling`, dan `graph_version` yang sama dengan cache. Frontend menampilkan rute dan heatmap ([01](01-sukses-rute-dari-data-worker.jpg), [02](02-sukses-sumber-dan-versi.jpg)). E2E-03/05: window baru langsung dipakai |
| Skenario gagal mencakup sumber data tidak tersedia, cache kosong, inferensi timeout, dan API gagal | ✅ | E2E-04 sumber data tidak tersedia; E2E-01 cache kosong; E2E-06/07 inferensi timeout, galat, label salah, no_route, model mati ([03](03-memuat-saat-model-lambat.jpg), [04](04-fallback-model-timeout.jpg)); E2E-08 Cache API mati dan basis data mati ([05](05-api-gagal-502.jpg)); E2E-05 window sedang ditulis; E2E-09 versi graf tidak cocok |
| Tidak ada kegagalan yang berubah menjadi nilai paparan nol atau rute sukses palsu | ✅ | Setiap respons diperiksa (`no_false_success`): semua 200 punya paparan > 0, semua kegagalan berupa galat terstruktur non-200. Cache tidak berisi indeks atau konsentrasi bernilai nol. Fallback selalu bertanda `route_source: baseline`, `preference_applied: false`, `fallback_reason`. Satu catatan: D-01 mengisi 3 ruas tepi dengan median wilayah (bukan nol) |
| Laporan uji menyebutkan versi aplikasi, model, dataset/cache, hasil, dan defect terbuka | ✅ | Bagian 1, 4, dan 5 laporan ini |

### Penelusuran korelasi (contoh, teredaksi)

Satu permintaan dapat ditelusuri dari frontend sampai inferensi, dan dari cache sampai siklus Worker:

```
Worker   run_id=20261026T1200Z-idw-p2-k8-c21c1a50 model_version=idw-p2-k8 rows_written=21987 coverage=1.0
cache    pollution_window 2026-10-26 12:00Z complete, model_version idw-p2-k8
gateway  {"message":"POST /v1/routes 200","request_id":"55158e618299ad58da290143b81dfe56", "duration_s":0.240, ...}
API      route_inference request_id=55158e618299ad58da290143b81dfe56 solver=http outcome=ok route_source=policy
         model_version=stub-e2e-1 mode=walk alpha=0.3 beta=0.7 deadline_ms=7394 inference_ms=107.4
respons  X-Request-ID = metadata.request_id = 55158e61…; metadata.time_window_start = 2026-10-26T12:00:00Z
```

ID permintaan yang tampil di UI ([02](02-sukses-sumber-dan-versi.jpg), `d057ae6c…`) juga ditemukan di
log gateway dan Cache API. Log semua layanan diperiksa: tidak ada DSN, kata sandi, atau koordinat
perjalanan (E2E-11).

## 4. Ringkasan skenario

| Skenario | Hasil |
|---|---|
| E2E-01 cache kosong | 503 `cache_unavailable`/`cache_empty` untuk rute dan heatmap; tidak ada nilai yang dikarang |
| E2E-02 sukses Worker → frontend | 11/12 lulus; gagal: `missing_edge_count` (D-01) |
| E2E-03 window baru dan idempotensi | Window 13:00 langsung dipakai; ETag heatmap berubah; Worker tidak menulis ulang window yang sudah complete |
| E2E-04 sumber data tidak tersedia | Worker exit 1 (`NoGroundTruthError`), tidak menulis window 15:00; API tetap memakai 13:00 |
| E2E-05 window sedang ditulis | Window `writing` tidak terbaca; begitu `complete`, langsung dipakai |
| E2E-06 inferensi gagal, fallback aktif | Lambat, galat, label salah, no_route, layanan mati → rute tercepat bertanda baseline dengan `fallback_reason` yang tepat, < 10 s; log `outcome=fallback` dengan penyebab |
| E2E-07 inferensi timeout, fallback mati | 503 `solver_timeout` dalam batas gateway; no_route model → 503 `solver_no_route` (bukan 422) |
| E2E-08 API dan basis data gagal | Basis data mati → 503 `database_unavailable`, pulih otomatis; Cache API mati → galat JSON dari gateway, tetapi 504 alih-alih 502 (D-02) |
| E2E-09 versi graf dan cache tidak cocok | Estimasi dari graf lain diabaikan → 503 `cache_empty`, bukan paparan nol |
| E2E-10 kontrak API publik | Koleksi uji 17 kasus (validasi, 413, 422, heatmap) lulus lewat gateway |
| E2E-11 log aman | Tanpa rahasia dan tanpa koordinat |

## 5. Defect terbuka

| ID | Prioritas | Komponen (pemilik) | Defect | Langkah reproduksi | Usulan perbaikan |
|---|---|---|---|---|---|
| D-01 | Sedang | Cache API, `pollution/repository.py` (SE) | Ruas yang kotak pembatasnya menyentuh bbox permintaan tetapi geometrinya tidak, masuk ke subgraf (`&&`) namun estimasinya tidak terbaca (query polusi menambah `ST_Intersects`). Ruas itu diisi median wilayah dengan confidence 0, dan UI menampilkan "3 ruas tanpa estimasi" walau semua ruas punya estimasi | E2E-02: Monas → Bundaran HI, `missing_edge_count: 3`; ruas 111493, 359388, 370670 punya estimasi untuk ketiga window ([01](01-sukses-rute-dari-data-worker.jpg)) | Samakan filter: hapus `ST_Intersects` dari `_SQL_WEIGHTS` (cukup `&&`), atau baca bobot berdasarkan `edge_id` subgraf |
| D-02 | Rendah | API Gateway (DO) | Selama ±10 s setelah Cache API berhenti, gateway menunggu `proxy_connect_timeout` 5 s lalu menjawab 504 `upstream_timeout`, bukan 502 `upstream_unavailable`. Penyebabnya, IP lama masih tersimpan di cache DNS (`resolver … valid=10s`). Pengguna menunggu 5 s dan melihat pesan "terlalu lama merespons" | E2E-08: `docker compose stop cache_api`, lalu langsung `POST /v1/routes` → 504 setelah ±5 s; permintaan berikutnya → 502 | Kecilkan `proxy_connect_timeout` di jaringan privat (mis. 2 s), atau tetap diterima dengan catatan |
| D-03 | Sedang | Cache API, gateway, Worker (DO, TI-DO-08) | Korelasi Worker ↔ cache hanya lewat (`time_window_start`, `model_version`): `pollution_window` tidak menyimpan `run_id`. Log Cache API berupa teks biasa (`INFO:solver:…`), tidak berformat JSON seperti gateway | Lihat [hasil/correlation.log](hasil/correlation.log) | Simpan `run_id` di `pollution_window` (perubahan skema, tanya tim DB dulu); log JSON dengan `request_id` di Cache API |
| D-04 | Sedang | Cache API `/health` (SE/DO, TI-DO-08) | `/health` menjawab HTTP 200 walau `status: degraded` (basis data mati), dan tidak ada endpoint readiness (`/health/ready` → 404), sehingga health check platform tidak dapat membedakan "proses hidup" dari "siap melayani" | Hentikan `db`, `GET /health` → 200 `{"status":"degraded","database":"unavailable"}` | `/health` (liveness) tetap 200; tambah `/health/ready` yang menjawab 503 bila basis data atau cache tidak siap |
| D-05 | Rendah | Data Worker (AI/DO, TI-DO-08) | Langkah `fetch_ground_truth` berstatus `ok` walau kolektor portal `aborted` (portal tidak terjangkau); gangguan portal hanya terlihat di `run_status` | Laporan Worker 12:00: `status: ok`, `run_status: aborted`, `measurements: 231` ([hasil/worker-2026-10-26T1200Z.json](hasil/worker-2026-10-26T1200Z.json)) | Status langkah `degraded` atau `stale` bila kolektor gagal dan hanya data tersimpan yang dipakai |
| D-06 | Sedang (UX) | Model downscaling dan frontend (AI/FE) | Semua confidence IDW < 0,4 (median 0,30), sehingga seluruh heatmap bergaris "keyakinan rendah" dan setiap rute berlencana "Keyakinan Rendah". Peta jadi padat dan pembeda keyakinan tidak informatif | Laporan Worker: `low_lt_0_4 = 21987` dari 21 987 ([01](01-sukses-rute-dari-data-worker.jpg)) | Tinjau rumus confidence IDW atau ambang kelas (subbab 3.1) bersama tim AI |

### Ketergantungan yang belum terpenuhi

| Dependensi | Status | Dampak pada uji |
|---|---|---|
| TI-AI-11 (versi dan kemasan model) | Open | ST-GNN tidak terdaftar → downscaling selalu IDW. Kompatibilitas kontrak model tidak dapat diuji |
| TI-AI-10/11 Multi-Objective Route Model | Belum ada layanan | Rute "policy" berasal dari stub yang memenuhi kontrak IRouteSolve; perilaku MORL sebenarnya belum teruji |
| TI-DO-08 (logging dan health check) | Open | Menjadi sumber D-03, D-04, D-05 |
| Railway | `DOWNSCALE_WRITE_CACHE` belum aktif, PR TI-SE-08/09 belum di-merge | Uji ini lokal. Uji asap di Railway dilakukan setelah flag aktif dan PR masuk |

## 6. Cara mengulang

```bash
docker compose -f tests/e2e/docker-compose.yml --profile tools build
python3 tests/e2e/run_e2e.py
python3 tests/e2e/report.py
```

Lihat [tests/e2e/README.md](../../tests/e2e/README.md). Untuk mengganti dataset, jalankan
`export_fixture.py` dengan `SOURCE_DATABASE_URL` (read-only), lalu catat `dataset_version` yang baru di
laporan.
