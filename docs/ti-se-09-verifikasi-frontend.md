# TI-SE-09 — Verifikasi Antarmuka Pengguna

Dokumen ini mencatat apa yang sudah diperiksa pada antarmuka (`src/frontend`),
dengan apa, dan apa yang masih perlu dilengkapi sebelum issue ditutup.

## Kontrak yang dipakai

Backend (`/v1/routes`) masih berupa placeholder dan belum mengikuti kontrak
TI-SE-08, sehingga antarmuka dibangun terhadap kontrak pada dokumen desain
subbab 3.5.2 (`IRouteQuery`, `IExposureSurface`, Tabel 3.16) dan diperiksa
terhadap mock (`src/frontend/mock/server.mjs`). Asumsi yang perlu dikonfirmasi
saat Backend siap:

| Hal | Asumsi di antarmuka |
|---|---|
| Satuan paparan | Memakai `exposure_index_min` (indeks·menit) bila ada; bila hanya `exposure_ug_min_m3` (contoh respons di dokumen), satuan ditampilkan sebagai µg/m³·menit. |
| Format `bbox` | `GET /v1/exposure-surface?bbox=minLon,minLat,maxLon,maxLat&zoom=N`. |
| Zoom ≥ 14 | Bila bbox > 25 km², antarmuka meminta `zoom=13` (sel agregat) agar tidak menerima 400. |
| Metadata heatmap | `metadata.time_window_start` dan `metadata.data_stale` pada respons exposure-surface (opsional; label waktu tidak tampil bila tidak ada). |
| Nama jalan | `properties.name` pada fitur exposure-surface (opsional). |
| Asal/tujuan | Dipilih di peta, diseret, atau diketik sebagai `lat, lon`. Pencarian nama tempat (geocoding) tidak ada di kontrak, jadi belum ada. |

## Keadaan yang diuji

"Unit" = `npm test` (Vitest, 89 kasus). "Browser" = dijalankan di peramban
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

## Yang belum bisa dibuktikan dari sini

- **Uji kegunaan internal** perlu penguji manusia; belum dilakukan.
  Skenario yang disarankan: (1) cari rute dan jelaskan arti banner selisih,
  (2) bedakan rute rendah paparan dan tercepat tanpa membaca legenda, (3)
  jelaskan arti area bergaris, (4) pulihkan diri dari galat titik tujuan.
- **Tangkapan layar/rekaman alur** perlu diambil dari peramban dengan mock yang
  sama (`npm run mock` + `npm run dev`, skenario di atas).
- **Uji dengan Backend asli** menunggu kontrak TI-SE-08 diimplementasikan.
- Belum diperiksa dengan pembaca layar sungguhan; yang dipenuhi: elemen form
  standar, label untuk setiap kontrol, target sentuh ≥ 44 px, fokus terlihat,
  pesan galat `role="alert"`, dan pembeda rute yang tidak hanya bergantung warna.
