# Statistik split

## Blok temporal (awal time window, UTC)

| Blok | Mulai | Akhir (eksklusif) |
|---|---|---|
| train | 2026-09-13T00:00:00Z | 2026-10-28T00:00:00Z |
| val | 2026-10-29T00:00:00Z | 2026-11-05T00:00:00Z |
| test | 2026-11-06T00:00:00Z | 2026-11-19T10:00:00Z |

## Split stasiun (per grup)

| Split | Grup | Stasiun | Reference | Sensor | Stasiun PM2.5 | Stasiun NO2 |
|---|---|---|---|---|---|---|
| train | 77 | 79 | 15 | 64 | 79 | 12 |
| val | 16 | 16 | 3 | 13 | 16 | 1 |
| test | 16 | 16 | 3 | 13 | 16 | 2 |

Kota administrasi per split stasiun:

| Kota | train | val | test |
|---|---|---|---|
| KOTA ADM. JAKARTA BARAT | 15 | 3 | 2 |
| KOTA ADM. JAKARTA PUSAT | 7 | 2 | 2 |
| KOTA ADM. JAKARTA SELATAN | 19 | 4 | 3 |
| KOTA ADM. JAKARTA TIMUR | 22 | 4 | 5 |
| KOTA ADM. JAKARTA UTARA | 15 | 3 | 3 |
| KOTA JAKARTA PUSAT | 1 | 0 | 0 |
| KOTA JAKARTA SELATAN | 0 | 0 | 1 |

## PM2.5

| Split | Protokol | Stasiun-jam | Stasiun | Grup | Jam | Rerata | Simpangan baku | Median | Maks |
|---|---|---|---|---|---|---|---|---|---|
| train | fixed_split | 69.938 | 79 | 77 | 1080 | 34,0 | 25,8 | 26,9 | 305,7 |
| val | fixed_split | 2.428 | 16 | 16 | 168 | 31,6 | 24,5 | 25,6 | 150,0 |
| test | fixed_split | 2.517 | 15 | 15 | 199 | 42,3 | 28,1 | 38,0 | 176,3 |

Fold LOSO PM2.5: 109 fold (satu per grup), 111 stasiun, 138.157 label stasiun-jam pada seluruh periode.

## NO2

| Split | Protokol | Stasiun-jam | Stasiun | Grup | Jam | Rerata | Simpangan baku | Median | Maks |
|---|---|---|---|---|---|---|---|---|---|
| train | temporal_block_loso | 13.232 | 15 | 15 | 1079 | 53,5 | 49,3 | 34,9 | 308,4 |
| val | temporal_block_loso | 2.005 | 14 | 14 | 168 | 58,6 | 50,3 | 41,3 | 282,2 |
| test | temporal_block_loso | 2.354 | 14 | 14 | 199 | 56,6 | 49,9 | 39,7 | 299,2 |

Fold LOSO NO2: 15 fold (satu per grup), 15 stasiun, 18.207 label stasiun-jam pada seluruh periode.

## Label di luar split tetap PM2.5

63.274 label PM2.5 valid tidak masuk split tetap karena berada pada jeda antarblok atau pada kombinasi stasiun × blok yang berbeda (mis. stasiun uji pada blok train). Label ini tetap dipakai oleh fold LOSO.
