# TI-SE-09 — Verifikasi Antarmuka Pengguna

Dokumen ini mencatat apa yang sudah diperiksa pada antarmuka (`src/frontend`),
dengan apa, dan apa yang masih perlu dilengkapi sebelum issue ditutup.

## Kontrak yang dipakai

Antarmuka awalnya dibangun terhadap kontrak dokumen desain subbab 3.5.2
(`IRouteQuery`, `IExposureSurface`, Tabel 3.16) dan mock
(`src/frontend/mock/server.mjs`), karena Backend belum siap. Kontrak itu kini
diimplementasikan Backend (TI-SE-08: `POST /v1/routes` dan `GET /v1/exposure-surface`
di Cache API, diteruskan API Gateway) dan antarmuka sudah diuji terhadapnya (lihat
"Uji dengan Backend asli"). Asumsi awal dan hasil pemeriksaannya:

| Hal | Asumsi di antarmuka | Hasil terhadap Backend |
|---|---|---|
| Satuan paparan | Memakai `exposure_index_min` (indeks·menit) bila ada; bila hanya `exposure_ug_min_m3`, satuan µg/m³·menit. | Sesuai: Backend mengirim keduanya, antarmuka memakai indeks·menit. |
| Format `bbox` | `GET /v1/exposure-surface?bbox=minLon,minLat,maxLon,maxLat&zoom=N`. | Sesuai. |
| Zoom ≥ 14 | Bila bbox > 25 km², antarmuka meminta `zoom=13` (sel agregat) agar tidak menerima 400. | Sesuai batas Backend. Diperiksa sampai layar 2560 × 1440: semua permintaan 200. |
| Metadata heatmap | `metadata.time_window_start` dan `metadata.data_stale`. | Sesuai. |
| Nama jalan | `properties.name` pada fitur exposure-surface (opsional). | Backend tidak mengirim nama jalan; label nama tidak tampil. |
| Alasan fallback | `policy_timeout`, `policy_error`, `invalid_route` (contoh Kode 3.2). | **Disesuaikan**: Backend mengirim `model_timeout`, `model_unavailable`, `model_error`, `model_invalid_response`, `model_no_route`, `policy_unavailable`. Semua kini punya teks sendiri. |
| Versi data jalan | Tidak ditampilkan. | **Ditambahkan**: `metadata.graph_version` tampil sebagai "Versi data jalan (OpenStreetMap)". |
| Asal/tujuan | Dipilih di peta, diseret, atau diketik sebagai `lat, lon`. Pencarian nama tempat (geocoding) tidak ada di kontrak, jadi belum ada. | Tetap. |

## Keadaan yang diuji

"Unit" = `npm test` (Vitest, 94 kasus, termasuk `src/contract.test.ts` yang membaca
respons asli Backend di `src/fixtures/ti-se-08/`). "Browser" = dijalankan di peramban
terhadap mock; hasil dibaca dari DOM/peta dan, untuk tata letak, dari tangkapan layar.

### Pemuatan dan status

| Keadaan | Hasil yang diharapkan | Diuji |
|---|---|---|
| Pencarian berjalan | Tombol "Mencari rute…" nonaktif, status "maks. 12 detik", tombol Batalkan, tidak ada hasil lama di panel/peta | Browser (`slow`, `timeout`) |
| Batalkan pencarian | Permintaan dihentikan, pesan "Pencarian dibatalkan", tombol aktif lagi | Browser, Unit (`aborted`) |
| Tidak ada respons 12 detik | Permintaan dibatalkan (AbortController), pesan galat + "Coba lagi", tanpa hasil | Browser, Unit |
| Permintaan baru setelah hasil | Rute, legenda rute, dan panel hasil lama dihapus sebelum permintaan dikirim | Browser |
| Heatmap gagal diperbarui | Heatmap lama tetap tampil tetapi diredupkan dan diberi label "data lama (HH.MM WIB), bukan kondisi terbaru" + "Coba lagi"; rute tidak terpengaruh | Browser (`heat_error`) |
| Heatmap > 5 detik | Permintaan dibatalkan setelah 5 detik; selama menunggu tampil "Memperbarui heatmap… menampilkan data HH.MM WIB", lalu perlakuan sama dengan heatmap gagal | Browser (`heat_slow`) |
| Geser/zoom beruntun | Satu permintaan, 500 ms setelah gerakan terakhir; permintaan sebelumnya dibatalkan | Browser (lima geseran dalam 300 ms → 1 request); belum ada uji otomatis |
| Ganti lapisan Gabungan/PM2.5/NO2 | Warna dihitung ulang dari data di memori; tanpa permintaan baru | Browser (jumlah request tetap) |

### Galat dan data parsial

| Keadaan | Hasil yang diharapkan | Diuji |
|---|---|---|
| Isian kosong / format salah / di luar Jakarta / asal = tujuan | Pesan di kolom, tombol Cari rute nonaktif, tidak ada request | Unit, Browser |
| `422 point_not_on_network` (tujuan / asal) | Kolom yang disebut pesan diberi bingkai merah + pesan + saran menggeser titik | Browser (`point_not_on_network`, `point_origin`), Unit |
| `422 outside_study_area` | Pesan server + saran memilih titik di Jakarta | Browser, Unit |
| `422 no_route_found` dan respons 200 tanpa fitur rute | Keadaan "Rute tidak ditemukan" dengan saran memindahkan titik/ganti moda | Browser (`no_route`, `no_recommended`), Unit |
| `400 invalid_request` | "Permintaan tidak valid", periksa isian | Browser, Unit |
| `429 rate_limited` + `Retry-After` | Tombol nonaktif dengan hitung mundur; pesan hilang setelah waktu habis | Browser (`rate_limited`), Unit |
| `503 cache_unavailable`, `503 route_service_unavailable`, `504 upstream_timeout` | Pesan khusus + "Coba lagi" | Browser, Unit |
| `502` berbadan HTML / jaringan putus | Pesan umum "server bermasalah" / "tidak dapat terhubung" + "Coba lagi"; teks server tidak ditampilkan mentah | Browser (`gateway_html`), Unit |
| `200` dengan badan placeholder Backend saat ini, bukan JSON, atau koordinat tertukar `[lat, lon]` | "Respons server tidak dapat dibaca"; tidak ada rute digambar | Browser (`bad_shape`, `bad_json`), Unit |
| `data_stale = true` | Peringatan kuning dengan jam estimasi terakhir | Browser (`stale`) |
| `missing_edge_count > 0` | Catatan bahwa sebagian ruas tanpa estimasi | Browser (`partial`) |
| `route_source = baseline` (fallback) | Kartu berjudul "Rute rekomendasi" + lencana "Metode cadangan", penjelasan prioritas tidak diterapkan; legenda "Rute rekomendasi (cadangan)" | Browser (`fallback`) |
| Rute rekomendasi identik dengan rute tercepat | Satu kartu, banner "Sama", tanpa klaim penurunan | Browser (`fallback`), Unit |
| Paparan rute rekomendasi lebih tinggi | Banner netral "+X%", bukan "penurunan" | Unit (`higher`) |
| Keyakinan 0,39 / 0,40 / 0,69 / 0,70 | Rendah / sedang / sedang / tinggi | Unit |

### Disclaimer, sumber, dan versi

- Disclaimer ("estimasi model … bukan rekomendasi medis atau diagnosis
  kesehatan") berada di kaki panel pada semua keadaan, termasuk sebelum ada hasil.
- Bagian "Sumber data dan versi" pada hasil memuat jam data, sumber rute,
  versi model downscaling dan model rute, jumlah ruas tanpa estimasi, ID
  permintaan, dan teks disclaimer dari server bila ada.
- Istilah yang dipakai "rute rendah paparan"; tidak ada kata "aman" atau "sehat".

### Pembeda visual dan legenda

- Rute rendah paparan: garis hijau tebal; rute tercepat: garis abu-abu putus-putus
  (dibedakan oleh bentuk, bukan hanya warna). Legenda rute ada di kartu rute
  dan di kartu legenda peta.
- Heatmap: gradasi satu arah krem → merah tua, batas kelas 0,5/1,0/2,0/3,0,
  keterangan rumus indeks (pedoman WHO 15 dan 25 µg/m³), satuan, area bergaris
  = keyakinan rendah, abu-abu = tanpa estimasi.
- Satuan selalu ditulis: menit, km, indeks·menit, µg/m³.

## Uji dengan Backend asli (TI-SE-08)

Tanggal 2026-10-26. Rantai lengkap dijalankan secara lokal:

```
Data Worker (osm_cache, spatial_model) → PostgreSQL + PostGIS (testdb)
  → Cache API (feat/ti-se-08, ROUTE_SOLVER_PROVIDER=mock) → API Gateway (src/gateway)
  → npm run dev (BACKEND_URL = gateway) → peramban
```

Data uji berupa graf jalan 20 × 20 (400 simpul, 760 ruas, ±110 m per ruas) di sekitar Tanah Abang,
ditulis kode Data Worker. Graf ini memiliki satu time window complete dengan pita polusi diagonal
(indeks 0,57 sampai 3,0).

| # | Keadaan | Hasil | Tangkapan layar |
|---|---|---|---|
| 1 | Isian perjalanan, prioritas condong ke bersih (α 0,20 · β 0,80) | Tombol aktif setelah dua titik valid | [01](ti-se-09/01-isian-perjalanan.jpg) |
| 2 | Rute ditemukan | Banner "−12,7% paparan … waktu tempuh yang sama"; kartu rute rendah paparan (garis hijau) dan rute tercepat (garis putus-putus) dengan satuan menit, km, indeks·menit; heatmap per ruas dengan legenda | [02](ti-se-09/02-dua-rute-dan-heatmap.jpg) |
| 3 | Sumber data dan versi | Jam data (20.00 WIB), sumber rute, versi model downscaling dan rute, versi data jalan, ID permintaan dari gateway, disclaimer server | [03](ti-se-09/03-sumber-data-dan-versi.jpg) |
| 4 | Model rute galat (`ROUTE_SOLVER_FAULT=error`) → fallback Backend | "Memakai metode cadangan: Model utama mengalami galat …"; lencana "Metode cadangan"; tidak ada klaim prioritas diterapkan | [04](ti-se-09/04-fallback-model-galat.jpg) |
| 5 | Tujuan jauh dari jalan → 422 `point_not_on_network` | Kolom Tujuan berbingkai merah dengan pesan Backend dan saran menggeser titik | [05](ti-se-09/05-galat-titik-tujuan.jpg) |
| 6 | Window ditandai `writing` → 503 `cache_unavailable` | "Data kualitas udara belum tersedia" dengan ID permintaan; tidak ada rute digambar; heatmap lama diredupkan dan diberi label "data lama (20.00 WIB), bukan kondisi terbaru" | [06](ti-se-09/06-cache-kosong-heatmap-lama.jpg) |
| 7 | Tampilan ponsel (375 × 812) | Lembar bawah dengan kartu rute; peta dengan kedua rute dan heatmap | [07](ti-se-09/07-ponsel-hasil-rute.jpg) |

Pemeriksaan tambahan di peramban:
- Permintaan heatmap pada layar 1920 × 1080 dan 2560 × 1440 saat peta diperkecil semuanya 200.
- Ganti lapisan tidak memicu permintaan baru.
- Galat 413 `payload_too_large` dan 502 `upstream_unavailable` (dari gateway) kini memiliki teks sendiri
  (`src/contract.test.ts`).

Di Railway, cache polusi belum memiliki time window complete, jadi permintaan rute di sana
akan menampilkan keadaan nomor 6 sampai Data Worker menulis window pertama.

## Yang belum bisa dibuktikan dari sini

- **Uji kegunaan internal** perlu penguji manusia; belum dilakukan.
  Skenario yang disarankan: (1) cari rute dan jelaskan arti banner selisih,
  (2) bedakan rute rendah paparan dan tercepat tanpa membaca legenda, (3)
  jelaskan arti area bergaris, (4) pulihkan diri dari galat titik tujuan.
- **Rekaman alur** (video) belum dibuat; tangkapan layar di atas mencakup alur utama dan
  keadaan galat terhadap Backend asli. Keadaan lain (rate limit, timeout, data parsial)
  hanya dapat dibuat dengan mock.
- Belum diperiksa dengan pembaca layar sungguhan; yang dipenuhi: elemen form
  standar, label untuk setiap kontrol, target sentuh ≥ 44 px, fokus terlihat,
  pesan galat `role="alert"`, dan pembeda rute yang tidak hanya bergantung warna.
