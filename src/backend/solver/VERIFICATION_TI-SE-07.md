# Bukti Verifikasi TI-SE-07: Integrasi Backend dengan model rute

Tanggal: 2026-10-26. Model MORL final (TI-AI-10) belum tersedia, jadi integrasi diuji terhadap
**layanan Route Model tiruan** yang berjalan sebagai server HTTP sungguhan (uvicorn di port lokal)
dan memenuhi kontrak `IRouteSolve`. Data graf dan polusi ditulis kode Data Worker ke PostgreSQL
18.6 + PostGIS 3.6.4 lokal.

## Ringkasan per acceptance criteria

| Acceptance criteria | Implementasi | Bukti uji |
|---|---|---|
| Rute hasil model dapat dipetakan kembali ke ruas graf dan GeoJSON yang valid | Setiap rute dari model diperiksa terhadap subgraf (ruas ada, bersambung dari simpul asal ke tujuan, arah diizinkan), lalu dibentuk menjadi GeoJSON FeatureCollection (RFC 7946, Kode 3.2) dari geometri ruas di basis data | `test_plan_returns_valid_geojson_mapped_to_graph_edges`, `test_valid_model_response`, `test_plan_through_http_model_on_worker_data[valid]`, `test_broken_paths_are_invalid_response` |
| Respons membedakan rute model, fallback, dan kegagalan tanpa menyesatkan pengguna | Rute model: `route_source: policy`, `preference_applied: true`. Fallback: `route_source: baseline`, `preference_applied: false`, `fallback_reason: model_*`, dan `notes` yang menjelaskannya. Kegagalan: 503 `route_service_unavailable` dengan `reason` | `test_failures_fall_back_with_explicit_marker`, `test_plan_marks_fallback_explicitly`, `test_bad_model_falls_back_explicitly`, `test_bad_model_without_fallback_is_structured` |
| Timeout/error layanan model tidak membuat endpoint menggantung atau membocorkan detail internal | Batas `deadline_ms + 300 ms`; fallback dibatasi agar total di bawah timeout gateway 10 s; pesan ke pengguna umum, detail hanya di log | `test_bad_model_*` (respons < 2,5 s walau model tidur 3 s), `test_failure_without_fallback_hides_internals`, `test_unavailable_model`, `test_no_fallback_when_no_time_is_left` |
| Uji integrasi mencakup respons model valid, malformat, lambat, dan tidak tersedia | `tests/test_model_service.py` dengan server tiruan; `tests/test_flow.py` ujung-ke-ujung dengan basis data | Tabel di bawah |

## Konfigurasi integrasi

| Variabel | Nilai untuk layanan Route Model | Keterangan |
|---|---|---|
| `ROUTE_SOLVER_PROVIDER` | `http` | `mock` / `baseline` untuk pengembangan |
| `ROUTE_SOLVER_URL` | `http://<layanan-route-model>.railway.internal:<port>` | Backend memanggil `POST {URL}/internal/v1/solve` (JSON gzip, header `X-Request-ID`) |
| `ROUTE_SOLVER_BUDGET_MS` / `RESPONSE_MARGIN_MS` / `GRACE_MS` | `8000` / `500` / `300` | Batas latensi desain (Tabel 3.6, 3.15) |
| `ROUTE_SOLVER_FALLBACK` | `baseline` | `none` = 503 sesuai Tabel 3.12 |
| `ROUTE_SOLVER_FALLBACK_TIMEOUT_MS` / `GATEWAY_TIMEOUT_MS` | `1500` / `10000` | Batas fallback; total permintaan tetap di bawah timeout gateway |

Kontrak jawaban yang diharapkan dari layanan (Kode 3.3):

```json
{"route_source": "policy", "fallback_reason": null, "model_version": "4",
 "recommended": {"edge_ids": [ ... ]}, "comparison": {"edge_ids": [ ... ]}}
```

Jawaban 422 `{"error": {"code": "no_route_found"}}` berarti tidak ada rute. Kode lain atau isi yang
tidak sesuai kontrak diperlakukan sebagai kegagalan model.

## Hasil uji

| Paket | Hasil |
|---|---|
| `src/backend/solver` | **71 passed**: 42 dari TI-SE-06 + 29 baru (fallback 9, GeoJSON 5, integrasi layanan model 12, ujung-ke-ujung 3) |
| `src/backend/osm`, `pollution`, `planner` | 115, 30, 37 passed |
| Container Cache API | `solver` 65 passed, 6 skipped (uji integrasi dengan server tiruan ikut berjalan di container) |

Uji integrasi terhadap server Route Model tiruan (`tests/test_model_service.py`, batas 1 500 ms):

| Jawaban model | Fallback aktif | Fallback mati |
|---|---|---|
| Valid | 200, `route_source: policy`, `model_version.route_policy: stub-morl-3` | sama |
| Malformat: bukan JSON (`<html>…`) | 200, `fallback_reason: model_invalid_response` | 503 `invalid_solver_response` |
| Malformat: field kontrak hilang | 200, `model_invalid_response` | 503 `invalid_solver_response` |
| Rute di luar subgraf (`edge_ids: [999999]`) | 200, `model_invalid_response` | 503 `invalid_solver_response` |
| HTTP 500 | 200, `model_error` | 503 `solver_error` |
| Lambat (tidur 3 s) | 200, `model_timeout`, selesai < 2,5 s | 503 `solver_timeout`, < 2,5 s |
| Tidak tersedia (port tertutup) | 200, `model_unavailable` | 503 `solver_unreachable`; pesan tanpa alamat atau nama exception; log memuat `ConnectError` |

## Jejak uji ujung-ke-ujung terarah

Alur: Data Worker menulis graf 4 × 4 dan time window polusi → `POST /v1/routes/plan` → planner
(TI-SE-05) → `HttpSolver` → server Route Model tiruan → respons GeoJSON. Log dicatat dengan
`log_config` tim (format teks). Satu baris per permintaan, tanpa koordinat.

| Skenario | Respons | Waktu | Log `route_inference` |
|---|---|---|---|
| A. Model valid | 200 `application/geo+json`, `route_source: policy` | 0,16 s | `INFO … request_id=trace-A solver=http outcome=ok route_source=policy model_version=stub-morl-3 mode=walk alpha=0.4 beta=0.6 deadline_ms=1409 inference_ms=22.9` |
| B. Model lambat, fallback aktif | 200, `route_source: baseline`, `fallback_reason: model_timeout` | 1,76 s | `WARNING … request_id=trace-B solver=http outcome=fallback route_source=baseline model_version=astar-baseline fallback_reason=model_timeout mode=walk alpha=0.4 beta=0.6 deadline_ms=1409 inference_ms=0.9 primary_ms=1513.0 reason=solver_timeout detail='no reply within 1509 ms'` |
| C. Model mati, fallback aktif | 200, `fallback_reason: model_unavailable` | 0,13 s | `WARNING … request_id=trace-C solver=http outcome=fallback route_source=baseline model_version=astar-baseline fallback_reason=model_unavailable … primary_ms=6.2 reason=solver_unreachable detail='Route Model tidak dapat dihubungi (ConnectError).'` |
| D. Model lambat, fallback mati | 503 `application/json` | 1,62 s | `WARNING … request_id=trace-D solver=http outcome=failed mode=walk alpha=0.4 beta=0.6 deadline_ms=1411 reason=solver_timeout detail='no reply within 1511 ms'` |

## Contoh respons dengan `route_source`

Koordinat dan `edge_ids` dipotong (`…`).

**A. Rute dari model** (`route_source: policy`):

```json
{"type": "FeatureCollection",
 "features": [
  {"type": "Feature", "geometry": {"type": "LineString", "coordinates": [[106.8, -6.2], "…", [106.803, -6.197]]},
   "properties": {"role": "recommended", "route_source": "policy", "preference_applied": true,
                  "travel_time_min": 8.462, "distance_m": 660.0, "exposure_index_min": 12.4105,
                  "exposure_ug_min_m3": 169.23, "concentration_coverage": 1.0, "confidence_score": 0.3,
                  "imputed_edge_count": 0, "edge_ids": [1, 3, "…"]}},
  {"type": "Feature", "geometry": {"type": "LineString", "coordinates": [[106.8, -6.2], "…", [106.803, -6.197]]},
   "properties": {"role": "comparison", "route_source": "baseline", "preference_applied": false, "…": "…"}}],
 "metadata": {"request_id": "trace-A", "mode": "walk", "pollutant": "pm25", "preference": {"alpha": 0.4, "beta": 0.6},
              "route_source": "policy", "preference_applied": true, "fallback_reason": null,
              "time_window_start": "2026-10-26T10:00:00Z", "data_stale": false, "missing_edge_count": 0,
              "graph_version": "jakarta-20261001",
              "model_version": {"downscaling": "idw-p2-k8", "route_policy": "stub-morl-3"},
              "solver": "http", "comparison": {"delta_travel_time_min": 0.0, "delta_exposure_pct": 0.0},
              "notes": [], "disclaimer": "Estimasi konsentrasi polutan merupakan keluaran model, …"}}
```

**B. Fallback karena model lambat** (`route_source: baseline`, `fallback_reason: model_timeout`):

```json
{"type": "FeatureCollection",
 "features": [
  {"type": "Feature", "geometry": {"type": "LineString", "coordinates": [[106.8, -6.2], "…", [106.803, -6.197]]},
   "properties": {"role": "recommended", "route_source": "baseline", "preference_applied": false,
                  "travel_time_min": 8.462, "distance_m": 660.0, "exposure_index_min": 12.4105, "…": "…"}},
  {"…": "comparison, sama dengan rekomendasi"}],
 "metadata": {"request_id": "trace-B", "route_source": "baseline", "preference_applied": false,
              "fallback_reason": "model_timeout",
              "model_version": {"downscaling": "idw-p2-k8", "route_policy": "astar-baseline"}, "solver": "baseline",
              "notes": ["Layanan model rute tidak dapat dipakai untuk permintaan ini; rute rekomendasi adalah rute tercepat (baseline) dan preferensi waktu-paparan tidak diterapkan."],
              "…": "…"}}
```

**D. Kegagalan tanpa fallback:**

```json
{"error": {"code": "route_service_unavailable",
           "message": "Layanan penentuan rute tidak merespons tepat waktu. Silakan coba lagi.",
           "request_id": "trace-D", "reason": "solver_timeout"}}
```

## Catatan

- **Model MORL final (TI-AI-10) belum tersedia.** Integrasi dibuktikan dengan layanan tiruan yang
  memenuhi kontrak yang sama. Saat layanan asli siap, cukup atur `ROUTE_SOLVER_PROVIDER=http` dan
  `ROUTE_SOLVER_URL`. Kontrak request/response perlu disepakati dengan tim AI.
- **Penyimpangan desain yang disengaja:** Backend menjalankan fallback baseline sendiri, karena baseline
  sekarang berjalan di Backend. Perilaku desain (503) tersedia dengan `ROUTE_SOLVER_FALLBACK=none`.
- **Pembatalan baseline.** Perhitungan baseline kini berhenti setelah batas waktunya tercapai (masukan
  QA untuk TI-SE-06); lihat [README, "Batas waktu dan pembatalan"](README.md#batas-waktu-dan-pembatalan).
- **Metrik Prometheus** (`route_fallback_total{reason}`, `route_request_duration_seconds{stage}`,
  IMetricsSink) belum dibuat. Saat ini pencatatan dilakukan lewat log.
- **Di Railway** cache polusi masih kosong, jadi permintaan valid menjawab 503 `cache_empty` sebelum
  model dipanggil.
