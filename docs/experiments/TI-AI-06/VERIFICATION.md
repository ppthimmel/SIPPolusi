# TI-AI-06: verifikasi fungsi waktu dan paparan

Rumus, satuan, penanganan data hilang, dan contoh perhitungan manual terdapat
di [README.md](README.md). Perbandingan algoritma
terdapat di [notebook](../../../notebooks/weighted_astar_verification.ipynb).

## Pengujian 2 November 2026

Pada Python 3.11 di Docker, jalankan dari root repositori dengan dependensi uji
backend terpasang:

```sh
PYTHONPATH=src/backend:. python -m pytest src/backend/solver/tests src/backend/planner/tests \
  src/backend/public_api/tests src/backend/tests/test_baseline.py -q -p no:cacheprovider -rs
```

Hasil tanpa database pengujian: **401 lulus, 15 dilewati**. Kelima belas tes
tersebut memerlukan `TEST_DATABASE_URL`. Tes database dijalankan terpisah pada
PostGIS lokal sementara menggunakan `src/backend/solver/tests/test_flow.py`,
`src/backend/planner/tests/test_flow.py`, `src/backend/public_api/tests/test_examples.py`,
dan `src/backend/public_api/tests/test_surface_db.py`: **20 lulus** (termasuk
tes lain dalam file yang sama). Database sementara dihapus setelah pengujian.

Tes mencakup contoh dua rute yang dihitung manual, perubahan α/β, nilai acuan
nol, data ruas yang tidak valid, pembatalan, kontrak planner, dan pembulatan
waktu yang konsisten pada versi Python yang berbeda. Pengujian acak memeriksa
biaya minimum hasil solver yang dioptimalkan terhadap rumus tervalidasi.

## Perbandingan waktu solver mock

Perbandingan berikut menjalankan **seluruh `_routes`**, termasuk pencarian
acuan dan validasi, pada grid sintetis 250×250 (62.500 simpul, 124.500 ruas).
Angka adalah median lima pengulangan pada satu mesin Docker, 2 November 2026:

| Implementasi | Median |
| --- | ---: |
| Rumus lama tanpa validasi semua ruas (rekonstruksi) | 0,677 s |
| Versi PR sebelum optimasi, validasi tiap pemeriksaan ruas | 1,032 s |
| Versi PR setelah optimasi, validasi tiap ruas sekali | 0,812 s |

Ketiganya menghasilkan urutan ruas yang sama pada contoh ini. Optimasi sekitar
21% lebih cepat daripada versi PR sebelumnya, tetapi masih sekitar 20% lebih
lambat daripada rumus lama tanpa validasi semua ruas. Ini bukan pengukuran
latensi API atau jaminan peningkatan pada setiap graf. Jalankan ulang dengan:

```sh
PYTHONPATH=src/backend python scripts/benchmark_mock_solver.py --size 250 --repeats 5
```

## Tindak lanjut MORL

Nilai acuan paparan nol memakai pembagi 1,0. Lingkungan pelatihan TI-AI-08 saat
ini juga menerapkan aturan tersebut; kesesuaian ilmiah dan konsistensi saat
evaluasi tetap perlu ditinjau. Untuk perbandingan adil, MORL dan pembanding
deterministik harus berbagi graf, bobot, dan rute/nilai acuan yang sama,
terutama jika beberapa rute tercepat dengan paparan berbeda memiliki waktu seri.

## Lihat juga

- [`src/backend/solver/VERIFICATION_TI-SE-06.md`](../../../src/backend/solver/VERIFICATION_TI-SE-06.md): verifikasi orkestrasi solver
- [`docs/experiments/TI-AI-08/README.md`](../TI-AI-08/README.md): lingkungan MORL yang memakai fungsi biaya ini
