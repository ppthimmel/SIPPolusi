# Cache sumber antar-tile

`SourceReader._prepare()` sebelumnya membuka ulang seluruh sumber saat tuple grid
berubah. Cache satu permintaan cocok untuk ekspor berulang pada node yang sama,
tetapi tidak untuk serving PM2.5/NO₂ yang memakai banyak tile berbeda. Angka
968 merujuk pada potensi permintaan untuk dua model × 484 tile; setiap cache miss
membuka **46 Parquet** pada dataset saat ini, bukan satu berkas.

Reader sekarang memuat snapshot sumber sekali per instance. Landsat/VIIRS disimpan
dengan ID kategorikal dan indeks sel terurut; pemilihan tile mencari batas rentang
kode sel dengan binary search. Sumber kasar, lookup, manifest VIIRS, dan event
Sentinel-5P dipakai kembali. Hanya kolom Landsat yang diperlukan yang dimuat.
Pemrosesan dan keluaran fitur tetap per tile, dengan satu cache permintaan terakhir;
jumlah tile tidak menambah tabel sumber yang ditahan di memori.

Worker sudah memakai satu `GridInputs`/reader untuk kedua model dan membuat reader
baru per run. Tidak diperlukan perubahan pada serving, model, atau ukuran tile.
Sumber/configuration reader harus tetap selama satu run; instance baru memuat
pembaruan berkas. Reader mutable tidak ditujukan untuk berbagi antar-thread.
Cache sumber baru dipublikasikan setelah seluruh pemuatan berhasil; kegagalan
dapat dicoba kembali tanpa snapshot parsial.

## Verifikasi

Baseline diambil dari implementasi pada `f3e0b78` sebelum perubahan. Benchmark
memakai sumber ds-v0.3.0 asli, empat tile tersebar pada domain aktif, bentuk halo
dan interior, serta 76 sel bersama untuk meniru tambahan grid sensor. Ada delapan
permintaan berbeda dengan riwayat 24 jam, 28 September 2026 13:00 sampai sebelum
29 September 2026 13:00 UTC. Ini pengukuran pembentukan fitur, bukan waktu inferensi
model atau keseluruhan 484 tile. Sel halo di luar domain aktif dikeluarkan.

Seluruh frame dibandingkan dengan baseline memakai `assert_frame_equal`, termasuk
urutan, dtype, nilai hilang, dan timestamp provenans. SHA-256 atas hash baris pandas
juga sama untuk seluruh permintaan. Hasil pengukuran disimpan dalam
[`source_reader_cache_verification.json`](source_reader_cache_verification.json).
Waktu merupakan satu run lokal dengan filesystem cache sistem operasi tidak
direset; gunakan sebagai indikasi, bukan jaminan latensi produksi. Cache memiliki
biaya awal dan tambahan RAM, sehingga permintaan tunggal dapat lebih lambat.

| Ukuran | Sebelum | Sesudah |
|---|---:|---:|
| Pembacaan Parquet, 8 permintaan | 368 | 46 |
| Permintaan pertama | 2,37 s | 11,96 s |
| Rentang 7 permintaan berikutnya | 1,45–1,64 s | 0,16–0,29 s |
| Total 8 permintaan | 13,23 s | 13,54 s |
| RSS proses setelah benchmark | 840 MiB | 1.193 MiB |

Rata-rata permintaan setelah pemuatan awal sekitar **6,9× lebih cepat**. Delapan
permintaan masih belum mengamortisasi biaya awal; total benchmark ini sedikit lebih
lambat. Tabel sumber diperkirakan 364 MiB, dengan puncak RSS proses 1.480 MiB saat
pemuatan. RAM cache tidak bertumbuh mengikuti jumlah tile, tetapi kapasitas proses
tetap perlu mencukupi untuk pemuatan awal. Tidak ada proyeksi waktu keseluruhan
Worker yang diklaim dari sampel ini.

Uji regresi mencakup satu pembacaan per berkas antar-tile, urutan dan aliasing frame,
kedua aturan VIIRS, validasi grid sebelum I/O, snapshot/reload, retry pemuatan,
sel sumber yang hilang, dan validasi manifest VIIRS hanya pada sel yang diminta.
Uji FeatureMatrix yang ada memeriksa join as-of dengan perhitungan brute force.
Suite Worker dengan PostgreSQL/PostGIS lokal: **236 passed, 0 skipped**. Uji cache
pada kode akhir: **8 passed**. Review indeks juga membandingkan 60 seleksi dengan
hasil pemilihan `.loc` sebelumnya tanpa perbedaan.

## Reproduksi

Dari root repo, aktifkan environment Worker dan set `PYTHONPATH=src/data_worker`.
Ambil baseline pada revisi sebelum perubahan, lalu bandingkan pada revisi baru:

```powershell
python scripts/benchmark_source_reader.py --output .cache/source-reader-before
python scripts/benchmark_source_reader.py --output .cache/source-reader-after --compare .cache/source-reader-before
python -m pytest src/data_worker/tests/test_source_reader_cache.py src/data_worker/tests/test_feature_matrix.py -q
```

Benchmark membutuhkan `psutil` untuk RSS. Frame Parquet dan laporan per berkas
berada di `.cache/`, tidak di-commit. Pengujian database memakai
`TEST_DATABASE_URL` yang menunjuk PostgreSQL/PostGIS lokal dan skema uji sementara.
