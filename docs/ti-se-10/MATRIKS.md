# Matriks uji end-to-end TI-SE-10

Dihasilkan oleh `tests/e2e/report.py` dari eksekusi 2026-10-26T15:45:37+00:00 – 2026-10-26T15:48:04+00:00 (UTC): **54/56 pemeriksaan lulus**. Versi lengkap di [hasil/versions.json](hasil/versions.json).

- Commit: `a1541b3` (feat/ti-se-10, dengan perubahan belum di-commit)
- Dataset: `c9503fc53769`, graf `jakarta-20261005`, ground truth 2026-10-26T12:00Z, 2026-10-26T13:00Z, 2026-10-26T14:00Z
- Basis data: PostgreSQL 18.6 (Debian 18.6-1.pgdg13+2) on x86_64-pc-linux-

| # | Skenario | Pemeriksaan | Hasil | Bukti |
|---|---|---|---|---|
| 1 | E2E-01 cache kosong | POST /v1/routes -> 503 cache_unavailable/cache_empty | ✅ lulus | [503, "cache_unavailable", "cache_empty"] |
| 2 | E2E-01 cache kosong | kegagalan tidak tampil sebagai sukses (status bukan 200, galat terstruktur) | ✅ lulus | [503, "cache_unavailable", "cache_empty"] |
| 3 | E2E-01 cache kosong | GET /v1/exposure-surface -> 503 cache_empty | ✅ lulus | [503, "cache_unavailable", "cache_empty"] |
| 4 | E2E-01 cache kosong | /health: database ok, belum ada time window | ✅ lulus | {"status": "ok", "database": "ok", "graph_version": "jakarta-20261005", "pollution_time_window": null, "pollution_data_stale": null} |
| 5 | E2E-02 sukses Worker -> frontend | siklus Worker berhasil (exit 0, tanpa langkah gagal) | ✅ lulus | {"exit": 0, "failed": []} |
| 6 | E2E-02 sukses Worker -> frontend | inferensi menulis window complete (IDW karena ST-GNN belum terdaftar) | ✅ lulus | {"run_id": "20261026T1200Z-idw-p2-k8-c21c1a50", "run_status": "complete", "model_version": "idw-p2-k8", "rows_written": 21987, "coverage_ratio": 1.0, "fallback_reason": "ModelUnavailableError: model spatial-downscalin… |
| 7 | E2E-02 sukses Worker -> frontend | cache: window 12:00 complete, model sama dengan laporan Worker | ✅ lulus | [["complete", "idw-p2-k8", "1"]] |
| 8 | E2E-02 sukses Worker -> frontend | cache: semua ruas punya estimasi, tidak ada nilai nol | ✅ lulus | {"zero_index": "0", "null_index": "0", "zero_conc": "0", "rows": "21987", "edges": 21987} |
| 9 | E2E-02 sukses Worker -> frontend | POST /v1/routes lewat gateway -> 200 GeoJSON | ✅ lulus | {"status": 200, "elapsed_s": 0.22} |
| 10 | E2E-02 sukses Worker -> frontend | versi konsisten: time window, model downscaling, dan graf sama dengan cache | ✅ lulus | {"time_window_start": "2026-10-26T12:00:00Z", "model_version": {"downscaling": "idw-p2-k8", "route_policy": "stub-e2e-1"}, "graph_version": "jakarta-20261005", "route_source": "policy", "solver": "http"} |
| 11 | E2E-02 sukses Worker -> frontend | rute dari model (stub IRouteSolve) dengan label policy | ✅ lulus | {"route_source": "policy"} |
| 12 | E2E-02 sukses Worker -> frontend | tidak ada paparan nol pada rute 200 | ✅ lulus | {"exposure_index_min": [170.8021, 170.8021], "exposure_ug_min_m3": [1877.33, 1877.33]} |
| 13 | E2E-02 sukses Worker -> frontend | korelasi: X-Request-ID = metadata.request_id = log gateway = log Cache API | ✅ lulus | {"x_request_id": "efb44c3b4009a94c2321f82bb4d5d351"} |
| 14 | E2E-02 sukses Worker -> frontend | missing_edge_count di respons sesuai cakupan Worker (100 %) | ❌ gagal | {"missing_edge_count": 3, "worker_coverage": 1.0} |
| 15 | E2E-02 sukses Worker -> frontend | heatmap per ruas 200 dari window yang sama, tanpa nilai nol | ✅ lulus | {"status": 200, "features": 1384} |
| 16 | E2E-02 sukses Worker -> frontend | frontend tersaji dan meneruskan /v1/* ke gateway | ✅ lulus | {"page": 200, "route_via_frontend": 200} |
| 17 | E2E-03 window baru dan idempotensi | Worker menulis window 13:00 | ✅ lulus | 20261026T1300Z-idw-p2-k8-2828752e |
| 18 | E2E-03 window baru dan idempotensi | API langsung memakai window terbaru 13:00 | ✅ lulus | 2026-10-26T13:00:00Z |
| 19 | E2E-03 window baru dan idempotensi | heatmap: ETag window lama tidak lagi cocok (200 data baru, bukan 304) | ✅ lulus | {"status": 200} |
| 20 | E2E-03 window baru dan idempotensi | heatmap: revalidasi tanpa perubahan -> 304 | ✅ lulus | {"status": 304} |
| 21 | E2E-03 window baru dan idempotensi | Worker idempoten: window 13:00 yang sudah complete dilewati | ✅ lulus | skipped |
| 22 | E2E-04 sumber data tidak tersedia | Worker menandai siklus gagal (exit 1) pada inferensi | ✅ lulus | {"exit": 1, "fetch_ground_truth": {"status": "unavailable", "measurements": 0, "run_status": "aborted"}, "inference": "NoGroundTruthError: tidak ada pengukuran sensor darat pada time window ini"} |
| 23 | E2E-04 sumber data tidak tersedia | tidak ada window 15:00 yang ditulis | ✅ lulus | null |
| 24 | E2E-04 sumber data tidak tersedia | API tetap memakai window complete terakhir (13:00), bukan nilai kosong | ✅ lulus | 2026-10-26T13:00:00Z |
| 25 | E2E-04 sumber data tidak tersedia | tidak ada paparan nol pada rute 200 | ✅ lulus | {"exposure_index_min": [165.6287, 165.6287], "exposure_ug_min_m3": [1634.683, 1634.683]} |
| 26 | E2E-04 sumber data tidak tersedia | data_stale mengikuti umur window (> 3 jam setelah berakhir) | ✅ lulus | {"data_stale": false, "expected": false} |
| 27 | E2E-05 window sedang ditulis | window berstatus writing tidak terbaca; API tetap 13:00 | ✅ lulus | 2026-10-26T13:00:00Z |
| 28 | E2E-05 window sedang ditulis | setelah Worker selesai, window 14:00 langsung dipakai | ✅ lulus | 2026-10-26T14:00:00Z |
| 29 | E2E-06 inferensi rute gagal, fallback aktif | model slow: 200 rute tercepat bertanda baseline, fallback_reason model_timeout | ✅ lulus | {"status": 200, "fallback_reason": "model_timeout", "elapsed_s": 7.914} |
| 30 | E2E-06 inferensi rute gagal, fallback aktif | tidak ada paparan nol pada rute 200 | ✅ lulus | {"exposure_index_min": [194.9408, 194.9408], "exposure_ug_min_m3": [2206.832, 2206.832]} |
| 31 | E2E-06 inferensi rute gagal, fallback aktif | model slow: log mencatat outcome=fallback dan penyebab solver_timeout | ✅ lulus | b37d70e81b5bd solver=http outcome=fallback route_source=baseline model_version=astar-baseline fallback_reason=model_timeout mode=walk alpha=0.3 beta=0.7 deadline_ms=7421 inference_ms=51.9 primary_ms=7722.0 reason=solv… |
| 32 | E2E-06 inferensi rute gagal, fallback aktif | model error: 200 rute tercepat bertanda baseline, fallback_reason model_error | ✅ lulus | {"status": 200, "fallback_reason": "model_error", "elapsed_s": 0.194} |
| 33 | E2E-06 inferensi rute gagal, fallback aktif | tidak ada paparan nol pada rute 200 | ✅ lulus | {"exposure_index_min": [194.9408, 194.9408], "exposure_ug_min_m3": [2206.832, 2206.832]} |
| 34 | E2E-06 inferensi rute gagal, fallback aktif | model error: log mencatat outcome=fallback dan penyebab solver_error | ✅ lulus | f45c5b4347bc solver=http outcome=fallback route_source=baseline model_version=astar-baseline fallback_reason=model_error mode=walk alpha=0.3 beta=0.7 deadline_ms=7438 inference_ms=48.4 primary_ms=82.4 reason=solver_er… |
| 35 | E2E-06 inferensi rute gagal, fallback aktif | model mislabel: 200 rute tercepat bertanda baseline, fallback_reason model_invalid_response | ✅ lulus | {"status": 200, "fallback_reason": "model_invalid_response", "elapsed_s": 0.203} |
| 36 | E2E-06 inferensi rute gagal, fallback aktif | tidak ada paparan nol pada rute 200 | ✅ lulus | {"exposure_index_min": [194.9408, 194.9408], "exposure_ug_min_m3": [2206.832, 2206.832]} |
| 37 | E2E-06 inferensi rute gagal, fallback aktif | model mislabel: log mencatat outcome=fallback dan penyebab invalid_solver_response | ✅ lulus | odel_version=astar-baseline fallback_reason=model_invalid_response mode=walk alpha=0.3 beta=0.7 deadline_ms=7440 inference_ms=53.0 primary_ms=87.6 reason=invalid_solver_response detail='route_source policy dengan pref… |
| 38 | E2E-06 inferensi rute gagal, fallback aktif | model no_route: 200 rute tercepat bertanda baseline, fallback_reason model_no_route | ✅ lulus | {"status": 200, "fallback_reason": "model_no_route", "elapsed_s": 0.194} |
| 39 | E2E-06 inferensi rute gagal, fallback aktif | tidak ada paparan nol pada rute 200 | ✅ lulus | {"exposure_index_min": [194.9408, 194.9408], "exposure_ug_min_m3": [2206.832, 2206.832]} |
| 40 | E2E-06 inferensi rute gagal, fallback aktif | model no_route: log mencatat outcome=fallback dan penyebab solver_no_route | ✅ lulus |  outcome=fallback route_source=baseline model_version=astar-baseline fallback_reason=model_no_route mode=walk alpha=0.3 beta=0.7 deadline_ms=7440 inference_ms=48.8 primary_ms=83.7 reason=solver_no_route detail='solver… |
| 41 | E2E-06 inferensi rute gagal, fallback aktif | layanan model mati: fallback model_unavailable | ✅ lulus | {"status": 200, "fallback_reason": "model_unavailable"} |
| 42 | E2E-07 inferensi timeout, fallback mati | 503 route_service_unavailable/solver_timeout dalam batas 10 s gateway | ✅ lulus | {"error": [503, "route_service_unavailable", "solver_timeout"], "elapsed_s": 7.811} |
| 43 | E2E-07 inferensi timeout, fallback mati | kegagalan tidak tampil sebagai sukses (status bukan 200, galat terstruktur) | ✅ lulus | [503, "route_service_unavailable", "solver_timeout"] |
| 44 | E2E-07 inferensi timeout, fallback mati | model menyatakan tidak ada rute: 503 solver_no_route, bukan 422 | ✅ lulus | [503, "route_service_unavailable", "solver_no_route"] |
| 45 | E2E-08 API dan basis data gagal | Cache API mati: gateway 502 upstream_unavailable (JSON seragam) | ❌ gagal | [504, "upstream_timeout", null] |
| 46 | E2E-08 API dan basis data gagal | lewat frontend: 502 yang sama diteruskan | ✅ lulus | [502, "upstream_unavailable", null] |
| 47 | E2E-08 API dan basis data gagal | basis data mati: 503 cache_unavailable/database_unavailable | ✅ lulus | [503, "cache_unavailable", "database_unavailable"] |
| 48 | E2E-08 API dan basis data gagal | /health melaporkan degraded saat basis data mati | ✅ lulus | {"status": "degraded", "database": "unavailable", "graph_version": null, "pollution_time_window": null, "pollution_data_stale": null} |
| 49 | E2E-08 API dan basis data gagal | kegagalan tidak tampil sebagai sukses (status bukan 200, galat terstruktur) | ✅ lulus | [503, "cache_unavailable", "database_unavailable"] |
| 50 | E2E-08 API dan basis data gagal | pulih otomatis setelah basis data kembali | ✅ lulus | {"status": 200} |
| 51 | E2E-09 versi graf dan cache tidak cocok | estimasi dari graf lain diabaikan: 503 cache_empty, bukan paparan nol | ✅ lulus | [503, "cache_unavailable", "cache_empty"] |
| 52 | E2E-09 versi graf dan cache tidak cocok | kegagalan tidak tampil sebagai sukses (status bukan 200, galat terstruktur) | ✅ lulus | [503, "cache_unavailable", "cache_empty"] |
| 53 | E2E-09 versi graf dan cache tidak cocok | kembali normal setelah versi cocok | ✅ lulus | {"status": 200} |
| 54 | E2E-10 kontrak API publik lewat gateway | koleksi uji (17 kasus: validasi, 413, 422, heatmap) lulus | ✅ lulus | 17/17 lulus |
| 55 | E2E-11 log aman | log semua layanan tanpa DSN/kata sandi | ✅ lulus | [] |
| 56 | E2E-11 log aman | log Cache API dan gateway tanpa koordinat perjalanan (PNF-06) | ✅ lulus | 0 |
