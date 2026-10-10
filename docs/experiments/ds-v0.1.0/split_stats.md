# Statistik split

## Blok temporal (awal time window, UTC)

| Blok | Mulai | Akhir (eksklusif) |
|---|---|---|
| train | 2026-09-13T00:00:00Z | 2026-09-24T00:00:00Z |
| val | 2026-09-25T00:00:00Z | 2026-09-28T00:00:00Z |
| test | 2026-09-29T00:00:00Z | 2026-09-30T23:00:00Z |

## Split stasiun (per grup)

| Split | Grup | Stasiun | Reference | Sensor | Stasiun PM2.5 | Stasiun NO2 |
|---|---|---|---|---|---|---|
| train | 81 | 83 | 12 | 71 | 83 | 11 |
| val | 17 | 17 | 2 | 15 | 17 | 3 |
| test | 17 | 17 | 2 | 15 | 17 | 0 |

Kota administrasi per split stasiun:

| Kota | train | val | test |
|---|---|---|---|
| KOTA ADM. JAKARTA BARAT | 14 | 3 | 3 |
| KOTA ADM. JAKARTA PUSAT | 9 | 2 | 1 |
| KOTA ADM. JAKARTA SELATAN | 17 | 4 | 4 |
| KOTA ADM. JAKARTA TIMUR | 23 | 5 | 5 |
| KOTA ADM. JAKARTA UTARA | 20 | 3 | 4 |

## PM2.5

| Split | Protokol | Stasiun-jam | Stasiun | Grup | Jam | Rerata | Simpangan baku | Median | Maks |
|---|---|---|---|---|---|---|---|---|---|
| train | fixed_split | 18.748 | 77 | 75 | 264 | 32,3 | 23,4 | 26,4 | 305,7 |
| val | fixed_split | 1.008 | 14 | 14 | 72 | 35,4 | 19,8 | 30,5 | 130,5 |
| test | fixed_split | 756 | 17 | 17 | 47 | 24,6 | 16,2 | 21,3 | 109,2 |

Fold LOSO PM2.5: 115 fold (satu per grup), 117 stasiun, 43.207 label stasiun-jam pada seluruh periode.

## NO2

| Split | Protokol | Stasiun-jam | Stasiun | Grup | Jam | Rerata | Simpangan baku | Median | Maks |
|---|---|---|---|---|---|---|---|---|---|
| train | temporal_block_loso | 3.326 | 14 | 14 | 263 | 53,9 | 50,9 | 33,5 | 308,4 |
| val | temporal_block_loso | 853 | 13 | 13 | 72 | 58,3 | 53,3 | 37,1 | 234,5 |
| test | temporal_block_loso | 604 | 13 | 13 | 47 | 54,7 | 50,7 | 34,3 | 236,8 |

Fold LOSO NO2: 14 fold (satu per grup), 14 stasiun, 5.372 label stasiun-jam pada seluruh periode.

## Label di luar split tetap PM2.5

22.695 label PM2.5 valid tidak masuk split tetap karena berada pada jeda antarblok atau pada kombinasi stasiun × blok yang berbeda (mis. stasiun uji pada blok train). Label ini tetap dipakai oleh fold LOSO.
