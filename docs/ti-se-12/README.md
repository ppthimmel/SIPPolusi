# TI-SE-12: Pencarian lokasi pada input asal dan tujuan

Pengguna dapat menentukan asal dan tujuan dengan nama tempat atau alamat. Klik peta dan koordinat
`lat, lon` tetap tersedia sebagai alternatif, dan titik hasil klik tampil sebagai nama tempat.

```
Frontend → POST /v1/geocode/search, /reverse → API Gateway (rate limit) → Cache API → server Photon
```

| Bagian | Berkas |
|---|---|
| Endpoint dan klien penyedia | [`public_api/geocoding.py`](../../src/backend/public_api/geocoding.py), [`router.py`](../../src/backend/public_api/router.py) |
| Batas laju dan path | [`src/gateway/templates/default.conf.template`](../../src/gateway/templates/default.conf.template) |
| Kolom pencarian | [`place-search.ts`](../../src/frontend/src/place-search.ts), [`place-combobox.ts`](../../src/frontend/src/place-combobox.ts) |

## Penyedia: API Photon

Kode memakai API [Photon](https://github.com/komoot/photon), sehingga server publik dan server sendiri
hanya berbeda pada satu variabel, `API_GEOCODING_URL`, di layanan Cache API. Selama variabel itu kosong,
tidak ada data yang keluar dan pencarian menjawab `503 geocoding_unavailable`.

| Penyedia | Autocomplete | Syarat | Keputusan |
|---|---|---|---|
| Nominatim publik | **Dilarang** kebijakannya | Maksimal 1 permintaan per detik per aplikasi | Tidak dapat dipakai |
| Photon publik (`https://photon.komoot.io`) | Didukung | Tanpa kunci. Tanpa batas angka: pemakaian berlebihan dibatasi atau diblokir, tanpa jaminan ketersediaan | Dapat dipakai untuk tahap sekarang |
| Photon sendiri | Didukung | Java 21+. Data Indonesia berupa dump JSON ±177 MB yang harus diimpor | Pilihan terbaik untuk privasi; belum dicoba |
| Layanan berbayar | Didukung | Kunci API dan biaya | Tidak perlu saat ini |

Kebijakan dibaca 3 November 2026 dan dapat berubah:
[Nominatim](https://operations.osmfoundation.org/policies/nominatim/),
[Photon](https://photon.komoot.io/),
[unduhan data Photon Indonesia](https://download1.graphhopper.com/public/asia/indonesia/).

### Cara memenuhi kebijakan penggunaan

- **Atribusi.** Data tempat © OpenStreetMap contributors, ODbL. Tertulis di formulir dan di peta.
- **Identitas.** Permintaan ke penyedia membawa `User-Agent` SIPPolusi.
- **Beban.** Frontend menunggu 300 ms setelah ketikan terakhir, mulai dari 3 karakter, dan membatalkan
  permintaan lama. Gateway membatasi 60 per menit per alamat IP (burst 10) dan 5 per detik untuk seluruh
  pengguna (burst 10), karena semua pengguna sampai ke penyedia dari satu alamat. Backend memakai satu klien
  HTTP dengan koneksi yang dipertahankan 30 detik, sehingga jabat tangan TLS tidak berulang: satu panggilan ke
  server publik turun dari ±1.100 ms menjadi ±300 ms, kecuali panggilan pertama.
- **Panggilan kedua.** Pencarian tanpa hasil yang memuat semua kata memanggil penyedia sekali lagi tanpa bbox
  (lihat "Pembatasan ke wilayah studi"). Salah ketik jadi menggandakan lalu lintas ke penyedia dan mengirim teks
  yang sama dua kali; batas 5 per detik tetap berlaku di depan keduanya.
- **Pengalihan.** Bila server publik membatasi atau berubah, cukup ganti `API_GEOCODING_URL`.

## Data lokasi (PNF-06, RI-09)

| Hal | Perlakuan |
|---|---|
| Baris permintaan | Endpoint memakai POST, sehingga teks dan koordinat berada di body. Access log, proxy, dan error log hanya mencatat path. `GET` ditolak gateway dengan 405 |
| Log Cache API | Hanya templat rute, status, dan durasi. Logger `httpx` dinaikkan ke `WARNING`, karena pada level `INFO` ia mencatat URL penyedia yang memuat teks pencarian |
| Pesan galat | Teks tetap; tidak memuat isi permintaan maupun URL penyedia |
| Penyimpanan | Tidak ada cache di server, `Cache-Control: no-store`, dan tidak ada penyimpanan di peramban |
| Alamat pengguna | Tidak diteruskan ke penyedia; penyedia hanya melihat alamat server |
| Titik di luar wilayah | Ditolak 422 tanpa diteruskan ke penyedia |

**Yang perlu diketahui:** dengan server publik, teks pencarian dan titik yang dipilih (klik peta, geser penanda,
atau "Lokasi saya") diterima pihak ketiga, di luar kendali SIPPolusi, dan berlaku kebijakan mereka. Pengguna diberi
tahu lewat catatan yang selalu tampil di formulir, di bawah kedua kolom, karena klik peta tidak pernah membuka daftar
saran. Photon sendiri menghilangkan pengiriman ini.

## Pembatasan ke wilayah studi

Hasil dibatasi ke bbox `OSM_STUDY_AREA_BBOX` lewat parameter `bbox` dan diperiksa ulang di backend, sehingga
memilih saran tidak berakhir dengan `outside_study_area`.

Photon mencocokkan secara longgar: teks acak tetap mendapat hasil, dan `gedung sate bandung` di dalam bbox
menghasilkan gedung lain di Jakarta. Karena itu setiap kata pencarian harus mengawali kata pada nama atau alamat
tempat agar dianggap cocok. Bila tidak ada yang cocok, satu panggilan tambahan tanpa bbox memeriksa apakah tempat
itu ada di luar wilayah:

| Hasil di wilayah studi | Tempat yang cocok di luar wilayah | Jawaban |
|---|---|---|
| Memuat semua kata | Tidak diperiksa | Hasil tersebut |
| Hanya mirip | Ada | Kosong, `outside_study_area: true` |
| Hanya mirip | Tidak ada | Hasil yang mirip, berguna untuk salah ketik |
| Tidak ada | Tidak ada | Kosong, `outside_study_area: false` |

Batasan yang diketahui:

- Nama kota di luar wilayah yang juga muncul pada tempat di Jakarta, misalnya `bandung` dan tempat bernama
  "Warung Bandung", menampilkan tempat di Jakarta tersebut.
- Wilayah studi berupa bbox, sehingga tempat di Depok, Tangerang, dan Bekasi di dalam bbox ikut ditawarkan
  (`ui depok`). Ini sama dengan wilayah yang dilayani rute saat ini.

## Urutan saran

Urutan bawaan Photon mengutamakan kemiripan nama, bukan tempat yang dimaksud. Untuk `monas`, ia memberi tiga halte
dan lima taman sebelum monumennya di urutan ke-9. Karena itu backend meminta 50 kandidat, lalu:

1. **Mengurutkan** menurut jenis OpenStreetMap: tujuan utama (`historic`, `tourism`, `place`, `aeroway`, stasiun,
   mal, stadion, rumah sakit, universitas), lalu tempat lain, lalu tempat kecil (halte, peron, parkir, simpang tol,
   sungai). Dalam tiap kelompok, yang memuat semua kata didahulukan.
2. **Membuang** hasil longgar dari jenis lain bila sudah ada yang cocok penuh.
3. **Menggabungkan** hasil dengan nama dan area yang sama, misalnya stasiun dan perhentiannya, atau ruas jalan
   sekecamatan.
4. **Memotong** ke 5 saran.

Pengecualian: kueri berawalan `jalan`, `jl`, atau `jln` mendahulukan jalan, dan kueri dengan kata `halte`, `busway`,
atau `transjakarta` tidak menurunkan halte. Halte tetap dapat ditemukan lewat namanya bila tidak ada yang lebih baik.

Contoh dari 21 kueri yang diperiksa terhadap jawaban server publik, 3 November 2026:

| Kueri | Sebelum | Sesudah |
|---|---|---|
| `monas` | Monas 1, Monas 2, Monas 3, Monas Barat, Monas Timur | **Monumen Nasional**, Monas Barat, Monas Timur, Monas Selatan, Monas Utara |
| `stasiun manggarai` | Plaza Stasiun Manggarai, Timur Stasiun Manggarai, Stasiun LRT Manggarai, Manggarai | **Manggarai**, Stasiun LRT Manggarai, Plaza Stasiun Manggarai, Timur Stasiun Manggarai |
| `ragunan` | Ragunan dan empat halte | Ragunan, **Kebun Binatang Ragunan**, Ragunan Park & Ride, Ragunan Zoo Animal Welfare Center, Taman Anggrek Ragunan |
| `gelora bung karno` | Empat halte, lalu stadion | **Stadion Utama Gelora Bung Karno**, Istana Olahraga, Lapangan Tenis, Stadion Madya, Hutan Kota |
| `plaza indonesia` | Halte Plaza Indonesia lebih dulu | **Plaza Indonesia** (mal), Plaza Senayan, Plaza Semanggi, Lippo Plaza Kramatjati, Ratu Plaza |
| `jalan sudirman` | Stasiun lebih dulu, ruas berulang | **Jalan Jenderal Sudirman** per kecamatan |
| `halte monas` | | Halte Monumen Nasional |

Batasan aturan ini:

- Peringkat bergantung pada tag OpenStreetMap, jadi tempat yang tagnya tidak lazim bisa turun.
- Kata yang masih diketik dapat memberi hasil yang kurang tepat: `mona` memberi "MOJA" lebih dulu, dan monumen baru
  muncul pada `monas`.
- Kecocokan juga dihitung dari alamat: `kota tua` masih menampilkan "SMA Negeri 5 Bekasi" di urutan terakhir karena
  beralamat di Bojong Tua, Kota Bekasi.
- Singkatan bergantung pada data: `rs cipto` tidak menemukan RSCM, sedangkan `rumah sakit cipto` menemukannya.

## Keadaan yang diuji

"Unit" = Vitest atau pytest. "Browser" = kolom asli di peramban, terhadap mock (`npm run mock`, skenario `geo_*`) dan
terhadap stack Docker dengan server Photon publik (`E2E_GEOCODING_URL=https://photon.komoot.io`).
"E2E" = E2E-12 pada stack Docker dengan `tests/e2e/photon_stub.py` (73/73 pemeriksaan matriks lulus, 3 November 2026).

| Keadaan | Hasil | Diuji |
|---|---|---|
| Ketik nama, pilih saran dengan klik atau Enter | Titik terisi dan peta menuju ke sana; tombol Cari rute aktif tanpa mengetik koordinat | Browser, [01](01-saran-tempat.jpg) |
| Klik peta | Koordinat tampil dulu, lalu diganti nama tempat | Browser, E2E, [02](02-nama-dan-klik-peta.jpg) |
| Pemberitahuan pengiriman data | Selalu tampil di formulir, sebelum ada ketikan atau klik; menyebut teks, klik peta, dan lokasi | Unit, Browser, [02](02-nama-dan-klik-peta.jpg) |
| Tempat umum dengan Photon sungguhan | Tujuan utama di atas halte; lihat "Urutan saran" | Unit, Browser, [01](01-saran-tempat.jpg) |
| Hasil rute | Kepala hasil memakai nama: "Monumen Nasional → Monas Timur" | Browser, [03](03-hasil-rute-dengan-nama.jpg) |
| Tempat hanya ada di luar wilayah | Pesan "berada di luar wilayah studi" | Unit, Browser, E2E, [04](04-di-luar-wilayah.jpg) |
| Tidak ditemukan | Pesan dengan saran peta dan koordinat | Unit, Browser, E2E, [05](05-tidak-ditemukan.jpg) |
| Penyedia mati, timeout, atau jawaban rusak | 503; pesan "sedang tidak tersedia"; form tetap utuh; rute tetap dilayani | Unit, Browser, E2E, [06](06-layanan-tidak-tersedia.jpg) |
| `API_GEOCODING_URL` kosong | 503 `not_configured`; tidak ada panggilan keluar | Unit |
| 429 dari gateway | Pesan "terlalu banyak pencarian" | Unit, Browser |
| 502 berbadan HTML, 200 tidak sesuai kontrak | Pesan "sedang tidak tersedia"; isi server tidak ditampilkan | Unit, Browser |
| Ketikan beruntun | Satu permintaan setelah ketikan terakhir; jawaban lama diabaikan | Unit |
| Mengetik saat nama titik belum datang | Nama yang terlambat tidak menimpa ketikan | Unit, Browser |
| Gagal mendapat nama titik | Koordinat tetap tampil; rute tetap dapat dicari | Unit, Browser |
| Ketik `lat, lon` saat layanan mati | Titik terisi tanpa memanggil pencarian | Browser |
| Teks tanpa memilih saran, lalu pindah kolom | Daftar tertutup; pesan di kolom; tombol nonaktif | Browser |
| Keyboard | Panah memilih, Enter mengambil, Escape menutup; `aria-activedescendant` mengikuti | Browser |
| Tampilan ponsel (375 × 812) | Kolom yang diketik dan daftar saran terlihat bersamaan; tidak melebar keluar layar; target sentuh ≥ 44 px | Browser, [07](07-ponsel.jpg) |
| Isi log | Tanpa teks pencarian dan koordinat, termasuk saat penyedia gagal | Unit, E2E |
| Metode, ukuran body, dan batas laju di gateway | 405, 413, 429 dengan `Retry-After`; batas bersama seluruh pengguna | `src/gateway/tests/run.sh` |

Respons nyata melalui kode backend ke Photon publik disimpan di
[`fixtures/ti-se-12`](../../src/frontend/src/fixtures/ti-se-12/README.md) dan dibaca `src/contract.test.ts`.

## Yang belum dibuktikan

- **Rekaman alur** belum dibuat; tangkapan layar di atas mencakup alur utama dan keadaan galat.
- **Railway** belum diatur: `API_GEOCODING_URL` harus diisi di layanan Cache API agar pencarian aktif.
- **Photon sendiri** belum dicoba.
- **Pembaca layar sungguhan** belum dicoba; yang dipenuhi adalah pola ARIA combobox dan `role="status"`.
- **Ponsel sungguhan dengan papan ketik terbuka** belum dicoba; tampilan ponsel diuji dengan emulasi tanpa papan ketik.

## Lihat juga

- [`src/backend/public_api/README.md`](../../src/backend/public_api/README.md): kontrak endpoint geocode
- [`src/gateway/README.md`](../../src/gateway/README.md): batas laju gateway
- [`docs/devops/verification-TI-DO-05-gateway.md`](../devops/verification-TI-DO-05-gateway.md): bukti gateway
- [`docs/ti-se-09/README.md`](../ti-se-09/README.md): verifikasi frontend
