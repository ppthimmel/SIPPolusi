# Frontend Enhancement (issue #113)

Perbaikan UI/UX antarmuka rute ([issue #113](https://github.com/ppthimmel/SIPPolusi/issues/113)): copywriting,
penyajian status pemrosesan, tampilan, dan responsivitas mobile. Branch `frontend-enchancement`, commit `647338e`.

Dokumen ini mencatat setiap perubahan beserta alasannya. Kontrak API tidak berubah (`POST /v1/routes`,
`GET /v1/exposure-surface`, `/v1/geocode/*`); `contract.test.ts` tetap lulus, dan Backend tidak disentuh.

**Prinsip yang dipakai**

1. **Satu hal penting per layar.** Yang tidak dibutuhkan untuk mengambil keputusan rute disembunyikan atau
   dilipat, bukan dihapus dari aplikasi.
2. **Bahasa sehari-hari.** Pesan menyebut apa yang terjadi dan apa yang bisa dilakukan, tanpa istilah teknis
   atau kode server.
3. **Pola yang sudah dikenal.** Tata letak daftar rute, tombol hapus, dan pemilih moda mengikuti aplikasi peta
   yang umum dipakai, supaya pengguna tidak perlu belajar ulang.
4. **Aksesibilitas tidak dikorbankan.** Teks yang dihilangkan dari layar tetap tersedia untuk pembaca layar
   bila ia satu-satunya petunjuk; target sentuh mengikuti WCAG.

---

## 1. Dua bahasa: Indonesia dan Inggris

**Perubahan.** Tombol **ID | EN** di kepala panel. Semua teks antarmuka tersedia dalam dua bahasa, termasuk label
untuk pembaca layar, pesan galat, petunjuk navigasi, dan legenda. Pilihan disimpan di peramban (`localStorage`)
dan layar langsung digambar ulang tanpa memuat ulang; rute, hasil, dan navigasi yang sedang berjalan tetap.
Format angka dan jam ikut bahasa: `34,8` / `12.00 WIB` (ID) dan `34.8` / `12:00 WIB` (EN).

**Alasan.** Pengguna Jakarta tidak semuanya berbahasa Indonesia (pekerja dan pelajar asing, wisatawan), dan
dokumen penelitian ditinjau juga dalam bahasa Inggris. Bahasa Indonesia tetap bawaan.

**Batasan.** Teks yang dikirim Backend (pesan galat server, `metadata.disclaimer`) tampil apa adanya. Pesan galat
yang tampil ke pengguna kini ditulis Frontend sendiri (lihat bagian 5), jadi hal ini jarang terlihat.

**Berkas.** [`i18n.ts`](../../src/frontend/src/i18n.ts) (fungsi `tr("indo", "english")`, atribut `data-en` di
`index.html`), pemanggilan `tr()` di semua modul tampilan.

## 2. Tampilan umum

| Perubahan | Alasan |
|---|---|
| Font Roboto untuk seluruh antarmuka; warna kertas hangat dan hijau tua dipertahankan | Satu keluarga huruf yang mudah dibaca di layar kecil. Palet lama sudah menjadi identitas aplikasi dan hijau dekat dengan makna "udara bersih" |
| Pemilih moda (Jalan kaki / Sepeda) menjadi tombol pil di atas kolom asal-tujuan | Moda menentukan rute; meletakkannya paling atas seperti panel arah pada aplikasi peta membuat urutan isian alami: moda → dari → ke |
| Simbol titik di dalam kolom: lingkaran gelap untuk asal, pin merah tua untuk tujuan; label "Asal/Tujuan" hanya untuk pembaca layar | Warna dan bentuknya mengikuti penanda di peta (titik gelap, pin merah tua), jadi kolom dan penanda terhubung tanpa teks tambahan |
| Judul "Mau ke mana?" dan teks pengantar disembunyikan dari layar (tetap ada untuk pembaca layar sebagai judul utama halaman) | Panel lebih pendek; isian langsung terlihat tanpa menggulir, terutama di ponsel |
| Penanda asal dan tujuan di peta serta garis rute memakai warna palet yang sama | Konsisten antara panel, legenda, dan peta |

**Berkas.** [`style.css`](../../src/frontend/src/style.css), [`index.html`](../../src/frontend/index.html).

## 3. Isian titik asal dan tujuan

| Perubahan | Alasan |
|---|---|
| Tombol **×** di dalam kolom, muncul hanya bila kolom berisi | Menghapus isian tanpa memilih-hapus teks satu per satu, terutama di ponsel |
| Tombol × berukuran sentuh 44 × 44 px; ikon tetap kecil | WCAG 2.5.5. Ukuran sebelumnya (36 px) lolos batas minimum AA tetapi mudah meleset |
| Tombol × punya nama untuk pembaca layar ("Hapus titik asal"), bisa dicapai dengan Tab, dan mengembalikan kursor ke kolom | Mengikuti pola tombol hapus yang aksesibel (Scott O'Hara, eBay, Atlassian) |
| **Escape** mengosongkan kolom bila daftar saran tertutup; bila terbuka, Escape hanya menutup daftar | Pola combobox WAI-ARIA: Escape menutup popup dulu, mengosongkan isian sebagai langkah opsional berikutnya |
| Galat tidak lagi berkedip saat titik dipilih lewat klik peta | Klik peta memindahkan fokus dari kolom sesaat sebelum titik terisi, sehingga kolom sempat dinilai "kosong". Pindah ke peta atau ke tombol milik kolom itu kini tidak dihitung sebagai meninggalkan kolom; pindah ke kolom lain tetap memeriksa |
| Placeholder "Cari tempat atau klik peta"; petunjuk "ketik koordinat lat, lon" dihapus dari semua pesan | Hampir tidak ada pengguna yang mengetik koordinat. Mengetik koordinat tetap berfungsi, hanya tidak diiklankan |
| Petunjuk "Lengkapi titik asal dan tujuan…" di bawah tombol dihapus | Tombol **Cari rute** sudah nonaktif sampai kedua titik valid, dan galat tampil di kolom masing-masing; petunjuk itu mengulang informasi yang sama |
| Petunjuk "Klik peta untuk memilih titik asal." disembunyikan dari layar, tetap dibacakan pembaca layar | Pengguna yang melihat memahami dari kursor dan penanda; pengguna pembaca layar tidak punya isyarat lain |

**Berkas.** [`main.ts`](../../src/frontend/src/main.ts) (bagian form), [`validation.ts`](../../src/frontend/src/validation.ts),
[`place-combobox.ts`](../../src/frontend/src/place-combobox.ts).

## 4. Status pemrosesan

| Perubahan | Alasan |
|---|---|
| Saat mencari, tombol **Cari rute** menjadi nonaktif dan berisi spinner; baris "Menghitung rute… maksimal 12 detik" dihapus | Status tampil di tempat pengguna baru saja menekan, dan tombol yang nonaktif mencegah pencarian ganda. Batas 12 detik adalah detail teknis yang tidak membantu keputusan |
| "Mencari rute…" tetap diumumkan ke pembaca layar lewat region `aria-live` | Spinner saja tidak terbaca oleh pembaca layar |
| Tombol **Batalkan** tetap ada selama pencarian | Pengguna bisa berhenti bila salah memilih titik |

## 5. Pesan galat

**Perubahan.** Galat dibagi dua:

- **Masalah di sisi sistem** (server mati, waktu habis, data kualitas udara belum tersedia, respons tak terbaca,
  5xx): satu pesan umum, "**Rute belum dapat dicari.** Terjadi kendala saat mencari rute. Coba lagi beberapa saat lagi."
- **Masalah yang bisa diperbaiki pengguna:** hanya langkahnya, misalnya "Geser titik tujuan ke dekat jalan yang
  bisa dilalui", "Pilih titik di dalam wilayah Jakarta", "Coba pindahkan titik atau ganti moda perjalanan".
  Terlalu banyak pencarian: "Tunggu 30 detik, lalu coba lagi."

ID permintaan dan tombol **Coba lagi** dihapus dari kotak galat. Kotak galat bergaya panel abu-abu dengan ikon
berwarna, bukan kotak merah muda.

**Alasan.** Pengguna tidak bisa berbuat apa pun dengan perbedaan "502" dan "504" atau dengan ID permintaan; yang
mereka perlu tahu hanyalah apakah harus mengubah isian atau menunggu. Pesan server (mis. "berjarak lebih dari
200 m dari ruas") tidak ditampilkan karena teknis dan hanya berbahasa Indonesia. Tombol **Cari rute** sudah
berfungsi sebagai "coba lagi". ID permintaan tetap tercatat di log server dan tetap tersedia di objek galat
untuk keperluan pengembangan.

**Berkas.** [`errors.ts`](../../src/frontend/src/errors.ts) dan [`errors.test.ts`](../../src/frontend/src/errors.test.ts).

## 6. Hasil rute

| Perubahan | Alasan |
|---|---|
| Rute tampil sebagai **daftar**: ikon moda, "via Jl. …", waktu tempuh di kanan dan jarak di bawahnya | Pola daftar rute pada aplikasi peta: yang dibandingkan pengguna pertama kali adalah waktu dan jalan yang dilewati |
| Baris yang dipilih diberi garis di kiri, waktunya hijau, dan memuat tombol **Mulai navigasi**; memilih baris lain menonjolkan rute itu di peta (yang lain dipudarkan) | Pilihan pengguna terlihat di panel dan di peta sekaligus |
| Nama jalan "via …" diambil dari reverse geocoding titik tengah setiap rute (endpoint `/v1/geocode/reverse` yang sudah dipakai form, satu permintaan per rute); bila tidak ada nama jalan, baris memakai nama rute | Kontrak rute tidak memuat nama jalan. Satu titik per rute cukup untuk label dan tidak menambah beban berarti |
| Judul "Rute ditemukan" dan chip "Data kualitas udara 22.00 WIB" dihapus dari layar (judul tetap untuk pembaca layar dan fokus) | Daftar rute sudah menyatakan bahwa rute ditemukan; jam data tetap ada di rincian dan legenda |
| Pemberitahuan "Memakai metode cadangan" dan kotak "Sama" (rute rekomendasi = rute tercepat) dihapus | Keduanya menjelaskan proses internal, bukan pilihan bagi pengguna. Bila hanya ada satu rute, daftar cukup satu baris |
| Chip keyakinan ("Keyakinan Rendah · 0,29") dan "Metode cadangan" dihapus dari baris rute | Angka keyakinan tanpa konteks membingungkan dan membuat baris padat. Keyakinan tetap terlihat di peta (area bergaris) dan di kartu ruas pada heatmap |
| Kotak selisih paparan ("−27,5% paparan polutan dibanding rute tercepat") tetap, di atas daftar, bila kedua rute berbeda | Ini nilai utama aplikasi: berapa paparan yang dihindari dengan rute ini |

**Daftar paling banyak berisi dua rute.** Kontrak API hanya mengembalikan rute rendah paparan dan rute tercepat.
Untuk jalan kaki, model rute belum tersedia (model yang terdaftar hanya untuk sepeda), sehingga Backend memakai
rute tercepat dan daftar berisi satu baris. Alternatif lebih dari dua memerlukan perubahan Backend.

**Berkas.** [`results-view.ts`](../../src/frontend/src/results-view.ts), [`route-names.ts`](../../src/frontend/src/route-names.ts),
`selectRoute` di [`map-view.ts`](../../src/frontend/src/map-view.ts).

## 7. Rincian "Sumber data dan versi"

| Perubahan | Alasan |
|---|---|
| Jam data kualitas udara dalam format lengkap: "23 November 2026, 22.00 WIB (GMT+7)" | Format tanggal yang umum; zona waktu dinyatakan dengan selisih GMT agar tidak ambigu bagi pengguna di luar WIB |
| Versi data jalan sebagai tanggal: `jakarta-20261005` → "5 Oktober 2026" | Pengguna tidak perlu membaca kode versi |
| Baris versi model downscaling, ID permintaan, ruas tanpa estimasi, versi model rute, sumber estimasi, sumber rute, dan paragraf penjelasan dihapus | Informasi teknis untuk tim, bukan pengguna; semuanya tetap ada di respons API dan log |
| Baris hak cipta: "© 2026 SIPPolusi · Data jalan © kontributor OpenStreetMap", dengan tautan ke halaman hak cipta OSM | Kewajiban atribusi lisensi ODbL OpenStreetMap |

**Berkas.** `detailSection` di [`results-view.ts`](../../src/frontend/src/results-view.ts), `fmtDateTimeWib` dan
`fmtGraphVersionDate` di [`format.ts`](../../src/frontend/src/format.ts).

## 8. Legenda heatmap

**Perubahan.** Urutan baru dari atas: judul "Kualitas udara" dengan jam data di kanan (kuning bila lebih dari
3 jam) → pemilih lapisan (Gabungan / PM2.5 / NO2) → baris status, hanya bila ada masalah → skala warna dengan
satu baris keterangan ("Indeks gabungan · 1,0 = pedoman WHO") → kunci peta dalam satu baris ("Tanpa estimasi",
"Keyakinan rendah") → "Tentang indeks", penjelasan rumus yang dilipat.

**Skala dibuat linear.** Sebelumnya angka 0, 0,5, 1,0, 2,0, 3,0 berjarak sama, sehingga ujung rendah tampak
lebih lebar dari kenyataan. Kini angka berada di posisi sebenarnya (0,5 di 14%, 1,0 di 28%, 2,0 di 57%, 3,0 di
85% lebar), warna diletakkan pada nilainya, nama kelas berada di tengah rentangnya dengan garis pemisah di batas
kelas, dan "Sangat tinggi" (di atas 3,0, tanpa batas atas) mendapat blok sendiri di ujung.

**Alasan.** Legenda lama memuat paragraf penjelasan dan status yang selalu tampil, sehingga skala, hal yang paling
sering dilihat, tenggelam. Peta mewarnai nilai secara linear (`interpolate linear` di MapLibre), jadi skala yang
tidak linear membuat warna di legenda dan di peta berarti nilai yang berbeda.

**Berkas.** [`legend.ts`](../../src/frontend/src/legend.ts).

## 9. Mobile

| Perubahan | Alasan |
|---|---|
| Panel bawah (bottom sheet) bersudut 28 px dengan pegangan; isian langsung terlihat setelah judul dan petunjuk dihilangkan | Ruang vertikal di ponsel terbatas; peta tetap terlihat di atas panel |
| Target sentuh tombol: × 44 px, tombol peta 44 px, tombol utama 44 px | WCAG 2.5.5 |
| Legenda di ponsel tetap terlipat menjadi satu tombol "Legenda peta" | Legenda tidak menutupi rute |
| Diperiksa pada lebar 375 × 812 px | Lebar ponsel umum |

## Yang dihapus dan menyentuh kebutuhan desain

Dua teks dihapus atas permintaan, padahal kode sebelumnya menandainya sebagai pemenuhan kebutuhan pada Bab III:

| Teks | Kebutuhan | Keadaan sekarang |
|---|---|---|
| Disclaimer di kaki panel ("Nilai paparan adalah estimasi… bukan rekomendasi medis") | PNF-07: disclaimer selalu terlihat | Disclaimer dari Backend (`metadata.disclaimer`) tidak lagi ditampilkan setelah rincian diringkas (bagian 7). **Perlu keputusan tim:** bila PNF-07 masih berlaku, tambahkan satu baris pendek, mis. di bawah daftar rute |
| Catatan privasi pencarian tempat ("Nama yang diketik … dikirim ke layanan pencarian lokasi …") | PNF-06: memberi tahu apa yang dikirim ke penyedia geocoding | Tidak ada di layar. Tes yang mewajibkannya (`config.test.ts`) ikut dihapus. **Perlu keputusan tim:** bila PNF-06 masih berlaku, kembalikan sebagai satu baris atau tautan "Privasi" |

## Alat bantu pengembangan

`?lokasi=<tempat|lat,lon>`, hanya pada `npm run dev`: peramban melaporkan titik tetap di Jakarta sebagai posisi
perangkat, untuk menguji "Lokasi saya", "Gunakan lokasi saya sebagai asal", dan navigasi dari luar Jakarta.
Tempat: `monas`, `bundaran-hi`, `sudirman`, `blok-m`, `kota`. Gabungkan dengan `?simulasi=normal` agar posisi
bergerak sepanjang rute. Rinciannya di [`docs/ti-se-13`](../ti-se-13/README.md#lokasi-tetap-di-jakarta);
kodenya [`dev-location.ts`](../../src/frontend/src/dev-location.ts), tidak ikut build produksi.

## Verifikasi

- `npm test` di `src/frontend`: **196 tes lulus**, termasuk tes baru untuk bahasa
  ([`i18n.test.ts`](../../src/frontend/src/i18n.test.ts)), nama jalan
  ([`route-names.test.ts`](../../src/frontend/src/route-names.test.ts)), format tanggal
  ([`format.test.ts`](../../src/frontend/src/format.test.ts)), pesan galat umum
  ([`errors.test.ts`](../../src/frontend/src/errors.test.ts)), dan lokasi pengembangan
  ([`dev-location.test.ts`](../../src/frontend/src/dev-location.test.ts)).
  `contract.test.ts` tidak diubah dan lulus.
- `npm run build`: berhasil. Peringatan ukuran chunk > 500 kB sudah ada sebelumnya (MapLibre).
- Peramban, pada stack lokal lengkap (Docker Compose dengan graf jalan, model rute dan model spasial terdaftar,
  dan cache polusi terisi): pencarian rute jalan kaki dan sepeda, pemilihan baris rute, heatmap dan legenda,
  pergantian bahasa saat hasil tampil, tombol × dan Escape, galat saat cache kosong, navigasi dengan
  `?lokasi=monas`. Desktop dan 375 × 812 px.

**Belum diuji:** navigasi di jalan sungguhan dengan GPS ponsel, dan pembaca layar sungguhan (NVDA/VoiceOver);
yang diperiksa hanyalah atribut ARIA dan teks yang dihasilkan.
