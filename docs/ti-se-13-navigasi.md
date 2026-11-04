# TI-SE-13: Navigasi rute dengan pelacakan GPS

Setelah rute tampil, pengguna dapat menekan **Mulai navigasi** pada kartu rute. Posisinya dilacak lewat
Geolocation API (`watchPosition`) dan ditampilkan di peta beserta arahnya. Panel menunjukkan petunjuk belokan
berikutnya, sisa jarak, sisa waktu, dan perkiraan jam tiba. Keluar rute memicu rute ulang dari posisi saat ini,
dan sesi berakhir saat pengguna tiba. Formulir juga punya tombol **Gunakan lokasi saya sebagai asal**.

```
watchPosition → NavigationSession (perangkat) → panel + peta
                      │ keluar rute ≥ 10 s, paling sering 1× per 30 s
                      └→ POST /v1/routes (asal = posisi sekarang) → rute baru
```

| Bagian | Berkas |
|---|---|
| Geometri rute: jarak sepanjang rute, proyeksi posisi, belokan | [`nav-geometry.ts`](../src/frontend/src/nav-geometry.ts) |
| Sesi navigasi: progres, sinyal lemah, keluar rute, rute ulang, tiba | [`navigation.ts`](../src/frontend/src/navigation.ts) |
| Sumber posisi dan pesan galatnya | [`position.ts`](../src/frontend/src/position.ts) |
| Panel navigasi | [`nav-view.ts`](../src/frontend/src/nav-view.ts) |
| Penanda posisi, lingkar akurasi, kamera | [`map-view.ts`](../src/frontend/src/map-view.ts) |
| Alur: mulai, rute ulang, akhiri | [`main.ts`](../src/frontend/src/main.ts) (bagian "navigation") |
| Simulasi posisi, hanya build pengembangan | [`position-sim.ts`](../src/frontend/src/position-sim.ts) |

Backend tidak berubah: rute ulang memakai `POST /v1/routes` yang sudah ada.

## Petunjuk belokan

Respons rute tidak memuat nama jalan, jadi petunjuk dihitung dari bentuk garis rute: "Belok kiri dalam 120 m".
Di setiap titik sudut, arah 20 m sebelum dan 20 m sesudahnya dibandingkan, sehingga lengkungan yang tersusun dari
banyak ruas pendek terbaca sebagai satu belokan dan patahan kecil pada geometri tidak diumumkan. Dari belokan yang
berjarak kurang dari 25 m, hanya yang paling tajam yang diumumkan.

| Perubahan arah | Petunjuk |
|---|---|
| < 30° | Tidak diumumkan (lurus) |
| 30–60° | Sedikit ke kiri/kanan |
| 60–135° | Belok kiri/kanan |
| 135–165° | Belok tajam ke kiri/kanan |
| ≥ 165° | Putar balik |

Jarak dibulatkan seperti diucapkan: per 5 m di bawah 50 m, per 10 m di bawah 1 km, lalu dalam km. Sisa waktu memakai
kecepatan rata-rata rute itu sendiri dari estimasi Backend (`travel_time_min` dibagi panjang rute).

## Ambang

Semua di [`NAV_LIMITS`](../src/frontend/src/navigation.ts).

| Hal | Nilai | Alasan |
|---|---|---|
| Sinyal lemah | Akurasi > 50 m | Posisi tetap tampil dengan lingkar akurasi, tetapi tidak dipakai untuk menilai keluar rute atau tiba, agar sinyal buruk tidak memicu rute ulang palsu |
| Keluar rute | > 40 m dari rute (atau > akurasi fix bila lebih besar) selama ≥ 10 s | Satu fix yang melenceng tidak cukup; 40 m di atas akurasi GPS ponsel biasa di kota |
| Jeda rute ulang | ≥ 30 s antar permintaan, dihitung sejak permintaan dikirim (juga bila kemudian dibatalkan), satu permintaan pada satu waktu | Gateway membatasi `/v1/routes` 20 per menit per IP (Tabel 3.15); navigasi memakai paling banyak 2 per menit, juga saat akurasi naik-turun di sekitar batas sinyal lemah |
| Setelah gagal | Jeda berlipat: 30, 60, 120 s (maksimal), atau `Retry-After` bila lebih lama | Server yang bermasalah tidak dibanjiri |
| Kembali ke rute | Fix akurat yang kembali berada di rute membatalkan permintaan rute ulang yang masih berjalan; fix lemah tidak | Tidak ada rute baru yang tidak diperlukan, dan sinyal lemah tidak dianggap kembali ke rute |
| Tiba | ≤ 25 m sebelum ujung rute saat berada di rute, atau ≤ 25 m dari tujuan. Untuk sepeda, yang kedua hanya berlaku bila sisa rute ≤ jarak ujung rute ke tujuan + 25 m; juga bila rute baru berakhir di posisi pengguna | Pejalan kaki dapat menyeberang ke tujuan di sisi jalan; pesepeda yang melewati tujuan di jalur seberang masih harus putar balik, jadi navigasinya tidak berakhir terlalu awal. Pelacakan dan *wake lock* langsung dihentikan, pesan lain diganti pesan tiba |
| Proyeksi ke rute | Tidak mundur lebih dari 50 m; maju paling jauh 60 m. Hanya bila posisi berada di luar rute dalam jendela itu, jendela diperluas sampai 60 m + 10 m per detik sejak fix terakhir di rute (tanpa batas sebelum fix pertama) | Rute yang melewati jalan yang sama dua kali, atau kembali di jalur seberang setelah putar balik, tidak "melompat" ke lintasan lain, juga pada fix pertama dan setelah jeda; pengguna yang mulai di tengah rute atau benar-benar bergerak selama jeda tetap ditemukan |
| Belokan | Tetap menjadi petunjuk sampai terlewati 5 m | Petunjuk tidak berganti sebelum pengguna berbelok |

## Data posisi (PNF-06, RI-09)

| Hal | Perlakuan |
|---|---|
| Pelacakan | Seluruhnya di perangkat. Posisi tidak dikirim selama pengguna berada di rute |
| Heatmap | Selama navigasi, `GET /v1/exposure-surface` memakai kotak rute (+±330 m), bukan tampilan kamera yang mengikuti pengguna; gerakan kamera tidak memicu permintaan. Diperbarui hanya setelah rute ulang |
| Rute ulang | Satu-satunya saat posisi keluar perangkat: sebagai `origin` di body `POST /v1/routes`, sama seperti pencarian rute biasa. Gateway dan Cache API hanya mencatat path; pemeriksaan E2E-11 memastikan log Cache API dan gateway tanpa koordinat perjalanan |
| Penyimpanan | Tidak ada: tanpa `localStorage`, tanpa riwayat, tanpa log konsol. Posisi terakhir hilang saat navigasi diakhiri |
| Pemberitahuan | Panel navigasi menulis bahwa posisi diproses di perangkat dan server hanya menerimanya saat rute dihitung ulang |
| Layar | Wake Lock API menjaga layar tetap menyala selama navigasi bila peramban mendukungnya; tanpa itu navigasi tetap berjalan |

## Keadaan yang diuji

"Unit" = Vitest (`nav-geometry.test.ts`, `navigation.test.ts`, `position.test.ts`). "Browser" = peramban terhadap
`npm run mock` dengan simulasi posisi (lihat bawah), 3 November 2026; tangkapan di [`ti-se-13/`](ti-se-13/).

| Keadaan | Hasil | Bukti |
|---|---|---|
| Mulai navigasi dari rute rekomendasi atau rute tercepat | Panel navigasi, penanda biru dengan arah, kamera mengikuti | Browser, [`01`](ti-se-13/01-navigasi-berjalan.jpg) |
| Petunjuk, sisa jarak, waktu, dan jam tiba diperbarui | Belokan berganti setelah dilewati, sisa jarak menurun | Unit, Browser |
| Rute asli Backend (fixture TI-SE-08) dilalui dari awal sampai akhir | Sisa jarak tidak pernah naik, tidak pernah keluar rute, berakhir tiba | Unit |
| Keluar rute | Pesan "Anda keluar dari rute", satu permintaan dari posisi menyimpang, rute diperbarui | Browser, [`02`](ti-se-13/02-keluar-rute.jpg); log mock: tepat 1 permintaan |
| Rute ulang gagal (timeout 12 s) | Rute lama tetap tampil, pesan galat, dicoba lagi tepat setelah 30 s dan berhasil | Browser; log mock: 1 gagal, 1 berhasil dalam ±45 s keluar rute |
| Rute ulang berturut-turut | Jeda 30 → 60 → 120 s, `Retry-After` dihormati | Unit |
| Rute ulang dibatalkan | Tetap memakai jeda 30 s | Unit |
| Putar balik, jalur seberang 15 m | GPS yang melenceng ke jalur seberang tidak melompatkan kemajuan (juga pada fix pertama dan setelah jeda 60 s), tidak memicu keluar rute | Unit |
| Tujuan di sisi jalur berangkat, rute masih putar balik | Sepeda: belum tiba sampai putar balik dilalui; jalan kaki: tiba | Unit |
| Tujuan jauh dari jalan (sepeda) | Tiba saat mendekati tujuan di luar rute dekat ujung rute | Unit |
| Fix hilang 40 s | Kemajuan menyusul posisi berikutnya, tetap di rute | Unit |
| Satu fix melenceng | Tidak dianggap keluar rute | Unit |
| Sinyal lemah (akurasi ±120 m) | Lingkar akurasi, pesan "Sinyal GPS lemah", keluar rute dan tiba tidak dinilai | Unit, Browser, [`03`](ti-se-13/03-sinyal-lemah.jpg) |
| Izin lokasi ditolak | Navigasi tidak dimulai, kembali ke hasil rute dengan pesan, kedua rute tetap utuh | Browser, [`04`](ti-se-13/04-izin-ditolak.jpg) |
| Posisi tidak tersedia / timeout | Navigasi tetap berjalan dengan pesan; rute dan tombol akhiri tetap ada | Browser |
| Peramban tanpa Geolocation API, atau halaman bukan HTTPS | Navigasi tidak dimulai, pesan menyebut penyebabnya | Unit |
| Tiba di tujuan | "Anda telah tiba", pelacakan berhenti, tombol "Selesai" kembali ke hasil | Unit, Browser, [`05`](ti-se-13/05-tiba.jpg) |
| Akhiri navigasi | Pelacakan berhenti, penanda posisi hilang, hasil rute tampil kembali | Browser |
| Peta digeser tangan | Kamera berhenti mengikuti; tombol "Ikuti posisi" (juga tombol lokasi di peta) mengembalikannya | Browser |
| Build produksi | Simulasi tidak ada di bundle | `vite build`, dicari di `dist/` |

Peta pada tangkapan `02`, `04`, dan `05` kosong karena panel peramban sedang di latar belakang saat diambil, sehingga
kanvas peta tidak digambar ulang; panel dan penanda tetap menunjukkan keadaannya.

## Simulasi posisi

Hanya pada `npm run dev`: tambahkan `?simulasi=<skenario>` ke alamat, dan `&kecepatan=<m/s>` untuk mempercepat
(bawaan 8 m/s). Modul simulasi dimuat dinamis di bawah `import.meta.env.DEV`, jadi tidak ikut build produksi.

| Skenario | Perilaku |
|---|---|
| `normal` | Bergerak mengikuti rute sampai tujuan |
| `keluar` | Menyimpang ±80 m dari rute di 40% perjalanan, lalu mengikuti rute baru |
| `lemah` | Akurasi ±120 m di antara 30% dan 50% perjalanan |
| `tolak` | Izin lokasi ditolak |
| `tanpa-gps` | Posisi tidak tersedia |

Contoh: `http://localhost:5173/?simulasi=keluar&kecepatan=25`. Untuk melihat rute ulang yang gagal, jalankan
`curl "localhost:8000/__mock/scenario?name=timeout"` setelah navigasi dimulai.

## Batasan

- **Belum diuji di lapangan.** Bukti di atas berasal dari simulasi. Uji jalan kaki atau sepeda dengan ponsel di
  jalan sungguhan, termasuk perilaku GPS di antara gedung tinggi, masih perlu dilakukan.
- **Tanpa nama jalan dan suara.** Kontrak rute tidak memuat nama jalan, dan petunjuk hanya tertulis.
- **Peta menghadap utara.** Kamera mengikuti posisi tanpa memutar peta; arah ditunjukkan oleh penanda.
- **Halaman harus terbuka.** Peramban menghentikan `watchPosition` saat halaman di latar belakang atau layar mati;
  navigasi berlanjut saat halaman dibuka lagi.
- **HTTPS.** Geolocation hanya tersedia lewat HTTPS (atau `localhost`), seperti deployment Railway.
