# TI-SE-01 — kontrak data lintas komponen

Tanggal: 2026-10-27. Status: disetujui pengguna di chat.

## Tujuan dan kriteria selesai

Lengkapi acceptance criteria issue TI-SE-01: Point, RouteRequest, RoadGraph,
EdgeWeight, TimeWindow, dan keluaran rute memiliki definisi serialisasi,
versi kontrak, aturan validasi, contoh valid/tidak valid, dan bukti uji.
Kontrak mengikuti perilaku Bab III yang sudah dirujuk kode dan README;
ketidaksesuaian yang ditemukan harus dilaporkan, bukan diasumsikan sesuai.

## Pendekatan

Rekomendasi implementer: pertahankan tipe domain dan endpoint yang ada,
tetapkan kontrak wire/cache secara eksplisit, dan uji pemetaan antarrepresentasi.
Definisi Python tidak harus identik jika representasi pertukaran dan aturannya jelas.
Alternatif memindahkan semua tipe ke satu paket bersama memerlukan perubahan
import serta packaging image backend dan Data Worker; itu lebih luas dari kebutuhan.
Dokumentasi saja tidak cukup karena belum menjamin validasi pada batas layanan.
Pendekatan ini disetujui pengguna di chat.

## Perubahan yang direncanakan

- Dokumen kontrak terpusat dan skema JSON yang dapat diperiksa mesin untuk
  bentuk pertukaran yang benar-benar digunakan, dengan versi 1.0.0.
  Versi kontrak dibedakan dari versi graf, model, dan versi aplikasi OpenAPI.
- Dokumentasikan koordinat WGS 84, objek lon/lat dan array [lon, lat], satuan
  meter/detik/menit serta ug/m3, UTC ISO 8601, window tepat satu jam,
  batas numerik, field wajib/nullable/default, dan kebijakan nilai hilang.
- Pertahankan RouteRequest publik yang sudah digunakan frontend. Tetapkan
  mode walk/bike, pollutant default pm25, alpha/beta dalam [0,1], jumlah 1
  dengan toleransi 1e-6; jangan menambah envelope wajib pada request lama.
- Dokumentasikan RoadGraph dan EdgeWeight sesuai bentuk API/cache yang ada,
  termasuk identitas edge dan graph_version serta metadata window/batch.
  Buat pemetaan eksplisit untuk field metadata yang ditambahkan writer.
- Tambahkan validasi yang belum ada pada batas penulisan cache dan batas API
  terkait, mengikuti kebijakan data hilang yang dipakai pipeline saat ini.
  NaN internal yang mewakili data hilang tidak boleh diserialisasi sebagai NaN JSON.
- Hubungkan dokumentasi keluaran rute ke OpenAPI/GeoJSON yang sudah ada;
  sertakan contoh untuk seluruh tipe dan alasan penolakan contoh tidak valid.

## Verifikasi

Tulis uji regresi sebelum mengubah perilaku. Periksa payload tidak lengkap,
koordinat tidak valid/nonfinite, moda tidak didukung, preferensi tidak valid,
window tanpa zona waktu/tidak sejajar jam/durasi salah, bobot tidak valid,
dan pelestarian null dibanding nol. Uji kompatibilitas skema dengan keluaran
serializer aktual dan perjalanan writer/reader tanpa mengganti satuan.

Jalankan suite relevan tanpa DB dan integrasi PostGIS lokal bila tersedia;
catat perintah, jumlah hasil, skip, dan keterbatasan secara terpisah dalam
bukti verifikasi TI-SE-01. Uji yang menggantung tidak dihitung lulus.
Perbarui Graphify setelah perubahan kode dan tinjau diff untuk perubahan tak terkait.

## Batas ruang lingkup

Tidak mengubah algoritme rute, model AI, UI, atau format publik secara breaking.
Tidak menutup issue atau mengirim komentar GitHub sebelum hasil diverifikasi.
Pilihan teknis rutin dan detail pemetaan diselesaikan implementer mengikuti kode
aktual; perubahan yang menuntut migrasi data atau kontrak publik dibahas kembali.
