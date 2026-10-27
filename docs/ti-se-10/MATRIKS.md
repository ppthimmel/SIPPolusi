# Matriks uji end-to-end TI-SE-10

Dihasilkan oleh `tests/e2e/report.py` dari eksekusi 2026-10-27T12:50:19+00:00 – 2026-10-27T12:52:56+00:00 (UTC): **62/63 pemeriksaan lulus**. Versi lengkap di [hasil/versions.json](hasil/versions.json).

- Commit: `d5c5dea` (feat/ti-se-10)
- Dataset: `c9503fc53769`, graf `jakarta-20261005`, ground truth 2026-10-26T12:00Z, 2026-10-26T13:00Z, 2026-10-26T14:00Z
- Basis data: PostgreSQL 18.6 (Debian 18.6-1.pgdg13+2) on x86_64-pc-linux-

| # | Skenario | Pemeriksaan | Hasil | Bukti |
|---|---|---|---|---|
| 1 | E2E-01 cache kosong | POST /v1/routes -> 503 cache_unavailable/cache_empty | ✅ lulus | [503, "cache_unavailable", "cache_empty"] |
| 2 | E2E-01 cache kosong | kegagalan tidak tampil sebagai sukses (status bukan 200, galat terstruktur) | ✅ lulus | [503, "cache_unavailable", "cache_empty"] |
| 3 | E2E-01 cache kosong | GET /v1/exposure-surface -> 503 cache_empty | ✅ lulus | [503, "cache_unavailable", "cache_empty"] |
| 4 | E2E-01 cache kosong | /health: database ok, belum ada time window | ✅ lulus | {"status": "ok", "database": "ok", "graph_version": "jakarta-20261005", "pollution_time_window": null, "pollution_data_stale": null} |
| 5 | E2E-01 cache kosong | /health/ready: siap (graf ada) dengan polusi dilaporkan kosong | ✅ lulus | {"status": 200, "body": {"status": "ready", "service": "cache-api", "checks": {"database": "ok", "graph": "ok", "pollution": "empty"}}} |
| 6 | E2E-02 sukses Worker -> frontend | siklus Worker berhasil (exit 0, tanpa langkah gagal) | ✅ lulus | {"exit": 0, "failed": [], "status": "degraded"} |
| 7 | E2E-02 sukses Worker -> frontend | fetch_ground_truth menjelajahi portal (tiruan) dengan status ok | ✅ lulus | {"status": "ok", "run_status": "ok", "measurements": 231, "stations": 116, "failed_requests": 0} |
| 8 | E2E-02 sukses Worker -> frontend | inferensi menulis window complete; tiap polutan fallback IDW bertanda (Model Registry tidak dikonfigurasi) | ✅ lulus | {"run_id": "20261026T1200Z-spatial-efaaa52edeec2acd355b5c57e7e43bf48e4de68d51de963347cf740082b31ba0-729cc3b2426d-c36e8d1a", "run_status": "complete", "model_version": "spatial-efaaa52edeec2acd355b5c57e7e43bf48e4de68d5… |
| 9 | E2E-02 sukses Worker -> frontend | cache: window 12:00 complete, model sama dengan laporan Worker | ✅ lulus | [["complete", "spatial-efaaa52edeec2acd355b5c57e7e43bf48e4de68d51de963347cf740082b31ba0-729cc3b2426d", "1"]] |
| 10 | E2E-02 sukses Worker -> frontend | cache: semua ruas punya estimasi, tidak ada nilai nol | ✅ lulus | {"zero_index": "0", "null_index": "0", "zero_conc": "0", "rows": "21987", "edges": 21987} |
| 11 | E2E-02 sukses Worker -> frontend | cache: setiap baris menyimpan run_id Worker (trace_run_id -> inference_run) | ✅ lulus | {"trace_run_id": ["20261026T1200Z-spatial-efaaa52edeec2acd355b5c57e7e43bf48e4de68d51de963347cf740082b31ba0-729cc3b2426d-c36e8d1a"], "worker_run_id": "20261026T1200Z-spatial-efaaa52edeec2acd355b5c57e7e43bf48e4de68d51de… |
| 12 | E2E-02 sukses Worker -> frontend | POST /v1/routes lewat gateway -> 200 GeoJSON | ✅ lulus | {"status": 200, "elapsed_s": 0.408} |
| 13 | E2E-02 sukses Worker -> frontend | versi konsisten: time window, model downscaling, dan graf sama dengan cache | ✅ lulus | {"time_window_start": "2026-10-26T12:00:00Z", "model_version": {"downscaling": "spatial-efaaa52edeec2acd355b5c57e7e43bf48e4de68d51de963347cf740082b31ba0-729cc3b2426d", "route_policy": "stub-e2e-1"}, "graph_version": "… |
| 14 | E2E-02 sukses Worker -> frontend | rute dari model (stub IRouteSolve) dengan label policy | ✅ lulus | {"route_source": "policy"} |
| 15 | E2E-02 sukses Worker -> frontend | metadata.estimation menyebut sumber estimasi dan polutan fallback yang dicatat Worker | ✅ lulus | {"estimation": {"source": "idw", "fallback_pollutants": ["no2", "pm25"]}, "worker": {"estimation_source": "idw", "fallback": ["no2", "pm25"]}} |
| 16 | E2E-02 sukses Worker -> frontend | tidak ada paparan nol pada rute 200 | ✅ lulus | {"exposure_index_min": [170.8021, 170.8021], "exposure_ug_min_m3": [1877.33, 1877.33]} |
| 17 | E2E-02 sukses Worker -> frontend | korelasi: X-Request-ID = metadata.request_id = log gateway = log Cache API | ✅ lulus | {"x_request_id": "9c3cf57120c0089f0397529f5992ebed"} |
| 18 | E2E-02 sukses Worker -> frontend | log Cache API berformat JSON dengan correlation_id = X-Request-ID | ✅ lulus | {"level": "info", "logger": "solver", "correlation_id": "9c3cf57120c0089f0397529f5992ebed"} |
| 19 | E2E-02 sukses Worker -> frontend | missing_edge_count di respons sesuai cakupan Worker (100 %) | ✅ lulus | {"missing_edge_count": 0, "worker_coverage": 1.0} |
| 20 | E2E-02 sukses Worker -> frontend | heatmap per ruas 200 dari window yang sama, tanpa nilai nol | ✅ lulus | {"status": 200, "features": 1384} |
| 21 | E2E-02 sukses Worker -> frontend | frontend tersaji dan meneruskan /v1/* ke gateway | ✅ lulus | {"page": 200, "route_via_frontend": 200} |
| 22 | E2E-03 window baru dan idempotensi | Worker menulis window 13:00 | ✅ lulus | 20261026T1300Z-spatial-dc3a36ee2064d6b89715c6de2db7d5d23e88ac72b83c953becc9c050f64612b4-729cc3b2426d-93742272 |
| 23 | E2E-03 window baru dan idempotensi | API langsung memakai window terbaru 13:00 | ✅ lulus | 2026-10-26T13:00:00Z |
| 24 | E2E-03 window baru dan idempotensi | heatmap: ETag window lama tidak lagi cocok (200 data baru, bukan 304) | ✅ lulus | {"status": 200} |
| 25 | E2E-03 window baru dan idempotensi | heatmap: revalidasi tanpa perubahan -> 304 | ✅ lulus | {"status": 304} |
| 26 | E2E-03 window baru dan idempotensi | Worker idempoten: window 13:00 yang sudah complete dilewati | ✅ lulus | skipped |
| 27 | E2E-04 sumber data tidak tersedia | portal SPKU mati: fetch_ground_truth berstatus failed, bukan ok | ✅ lulus | {"status": "failed", "run_status": "aborted", "failed_requests": 2, "note": "lintasan SPKU aborted: daftar stasiun kosong; lintasan dibatalkan tanpa menulis"} |
| 28 | E2E-04 sumber data tidak tersedia | Worker menandai siklus gagal (exit 1) pada pengambilan data dan inferensi | ✅ lulus | {"exit": 1, "failed": ["fetch_ground_truth", "trigger_downscale_inference"], "inference": "NoGroundTruthError: tidak ada pengukuran sensor darat pada time window ini"} |
| 29 | E2E-04 sumber data tidak tersedia | tidak ada window 15:00 yang ditulis | ✅ lulus | null |
| 30 | E2E-04 sumber data tidak tersedia | API tetap memakai window complete terakhir (13:00), bukan nilai kosong | ✅ lulus | 2026-10-26T13:00:00Z |
| 31 | E2E-04 sumber data tidak tersedia | tidak ada paparan nol pada rute 200 | ✅ lulus | {"exposure_index_min": [165.6287, 165.6287], "exposure_ug_min_m3": [1634.683, 1634.683]} |
| 32 | E2E-04 sumber data tidak tersedia | data_stale mengikuti umur window (> 3 jam setelah berakhir) | ✅ lulus | {"data_stale": true, "expected": true} |
| 33 | E2E-05 window sedang ditulis | window berstatus writing tidak terbaca; API tetap 13:00 | ✅ lulus | 2026-10-26T13:00:00Z |
| 34 | E2E-05 window sedang ditulis | setelah Worker selesai, window 14:00 langsung dipakai | ✅ lulus | 2026-10-26T14:00:00Z |
| 35 | E2E-06 inferensi rute gagal, fallback aktif | model slow: 200 rute tercepat bertanda baseline, fallback_reason model_timeout | ✅ lulus | {"status": 200, "fallback_reason": "model_timeout", "elapsed_s": 7.936} |
| 36 | E2E-06 inferensi rute gagal, fallback aktif | tidak ada paparan nol pada rute 200 | ✅ lulus | {"exposure_index_min": [194.9408, 194.9408], "exposure_ug_min_m3": [2206.832, 2206.832]} |
| 37 | E2E-06 inferensi rute gagal, fallback aktif | model slow: log mencatat outcome=fallback dan penyebab solver_timeout | ✅ lulus | star-baseline fallback_reason=model_timeout mode=walk alpha=0.3 beta=0.7 deadline_ms=7379 inference_ms=58.5 primary_ms=7686.1 reason=solver_timeout detail='no reply within 7679 ms'", "service": "cache-api", "correlati… |
| 38 | E2E-06 inferensi rute gagal, fallback aktif | model error: 200 rute tercepat bertanda baseline, fallback_reason model_error | ✅ lulus | {"status": 200, "fallback_reason": "model_error", "elapsed_s": 0.294} |
| 39 | E2E-06 inferensi rute gagal, fallback aktif | tidak ada paparan nol pada rute 200 | ✅ lulus | {"exposure_index_min": [194.9408, 194.9408], "exposure_ug_min_m3": [2206.832, 2206.832]} |
| 40 | E2E-06 inferensi rute gagal, fallback aktif | model error: log mencatat outcome=fallback dan penyebab solver_error | ✅ lulus | ar-baseline fallback_reason=model_error mode=walk alpha=0.3 beta=0.7 deadline_ms=7391 inference_ms=54.8 primary_ms=125.2 reason=solver_error detail='Route Model menjawab HTTP 500.'", "service": "cache-api", "correlati… |
| 41 | E2E-06 inferensi rute gagal, fallback aktif | model mislabel: 200 rute tercepat bertanda baseline, fallback_reason model_invalid_response | ✅ lulus | {"status": 200, "fallback_reason": "model_invalid_response", "elapsed_s": 0.387} |
| 42 | E2E-06 inferensi rute gagal, fallback aktif | tidak ada paparan nol pada rute 200 | ✅ lulus | {"exposure_index_min": [194.9408, 194.9408], "exposure_ug_min_m3": [2206.832, 2206.832]} |
| 43 | E2E-06 inferensi rute gagal, fallback aktif | model mislabel: log mencatat outcome=fallback dan penyebab invalid_solver_response | ✅ lulus | a=0.3 beta=0.7 deadline_ms=7330 inference_ms=77.7 primary_ms=135.7 reason=invalid_solver_response detail='route_source policy dengan preference_applied=False, fallback_reason=None'", "service": "cache-api", "correlati… |
| 44 | E2E-06 inferensi rute gagal, fallback aktif | model no_route: 200 rute tercepat bertanda baseline, fallback_reason model_no_route | ✅ lulus | {"status": 200, "fallback_reason": "model_no_route", "elapsed_s": 0.308} |
| 45 | E2E-06 inferensi rute gagal, fallback aktif | tidak ada paparan nol pada rute 200 | ✅ lulus | {"exposure_index_min": [194.9408, 194.9408], "exposure_ug_min_m3": [2206.832, 2206.832]} |
| 46 | E2E-06 inferensi rute gagal, fallback aktif | model no_route: log mencatat outcome=fallback dan penyebab solver_no_route | ✅ lulus | son=model_no_route mode=walk alpha=0.3 beta=0.7 deadline_ms=7387 inference_ms=64.7 primary_ms=126.5 reason=solver_no_route detail='solver reported no route on a reachable subgraph'", "service": "cache-api", "correlati… |
| 47 | E2E-06 inferensi rute gagal, fallback aktif | layanan model mati: fallback model_unavailable | ✅ lulus | {"status": 200, "fallback_reason": "model_unavailable"} |
| 48 | E2E-07 inferensi timeout, fallback mati | 503 route_service_unavailable/solver_timeout dalam batas 10 s gateway | ✅ lulus | {"error": [503, "route_service_unavailable", "solver_timeout"], "elapsed_s": 7.872} |
| 49 | E2E-07 inferensi timeout, fallback mati | kegagalan tidak tampil sebagai sukses (status bukan 200, galat terstruktur) | ✅ lulus | [503, "route_service_unavailable", "solver_timeout"] |
| 50 | E2E-07 inferensi timeout, fallback mati | model menyatakan tidak ada rute: 503 solver_no_route, bukan 422 | ✅ lulus | [503, "route_service_unavailable", "solver_no_route"] |
| 51 | E2E-08 API dan basis data gagal | Cache API mati: gateway 502 upstream_unavailable (JSON seragam) | ❌ gagal | [504, "upstream_timeout", null] |
| 52 | E2E-08 API dan basis data gagal | lewat frontend: 502 yang sama diteruskan | ✅ lulus | [502, "upstream_unavailable", null] |
| 53 | E2E-08 API dan basis data gagal | basis data mati: 503 cache_unavailable/database_unavailable | ✅ lulus | [503, "cache_unavailable", "database_unavailable"] |
| 54 | E2E-08 API dan basis data gagal | /health melaporkan degraded saat basis data mati | ✅ lulus | {"status": "degraded", "database": "unavailable", "graph_version": null, "pollution_time_window": null, "pollution_data_stale": null} |
| 55 | E2E-08 API dan basis data gagal | /health/ready 503 saat basis data mati, /health/live tetap 200 | ✅ lulus | {"ready": [503, {"status": "not_ready", "service": "cache-api", "checks": {"database": "unavailable", "graph": "unknown", "pollution": "unknown"}}], "live": 200} |
| 56 | E2E-08 API dan basis data gagal | kegagalan tidak tampil sebagai sukses (status bukan 200, galat terstruktur) | ✅ lulus | [503, "cache_unavailable", "database_unavailable"] |
| 57 | E2E-08 API dan basis data gagal | pulih otomatis setelah basis data kembali | ✅ lulus | {"status": 200} |
| 58 | E2E-09 versi graf dan cache tidak cocok | estimasi dari graf lain diabaikan: 503 cache_empty, bukan paparan nol | ✅ lulus | [503, "cache_unavailable", "cache_empty"] |
| 59 | E2E-09 versi graf dan cache tidak cocok | kegagalan tidak tampil sebagai sukses (status bukan 200, galat terstruktur) | ✅ lulus | [503, "cache_unavailable", "cache_empty"] |
| 60 | E2E-09 versi graf dan cache tidak cocok | kembali normal setelah versi cocok | ✅ lulus | {"status": 200} |
| 61 | E2E-10 kontrak API publik lewat gateway | koleksi uji (17 kasus: validasi, 413, 422, heatmap) lulus | ✅ lulus | 17/17 lulus |
| 62 | E2E-11 log aman | log semua layanan tanpa DSN/kata sandi | ✅ lulus | [] |
| 63 | E2E-11 log aman | log Cache API dan gateway tanpa koordinat perjalanan (PNF-06) | ✅ lulus | 0 |
