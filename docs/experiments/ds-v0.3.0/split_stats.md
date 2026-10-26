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
| train | 73 | 75 | 12 | 63 | 75 | 9 |
| val | 15 | 15 | 2 | 13 | 15 | 2 |
| test | 15 | 15 | 2 | 13 | 15 | 3 |

Kota administrasi per split stasiun:

| Kota | train | val | test |
|---|---|---|---|
| KOTA ADM. JAKARTA BARAT | 14 | 3 | 3 |
| KOTA ADM. JAKARTA PUSAT | 8 | 1 | 1 |
| KOTA ADM. JAKARTA SELATAN | 14 | 4 | 3 |
| KOTA ADM. JAKARTA TIMUR | 22 | 5 | 5 |
| KOTA ADM. JAKARTA UTARA | 17 | 2 | 3 |

## PM2.5

| Split | Protokol | Stasiun-jam | Stasiun | Grup | Jam | Rerata | Simpangan baku | Median | Maks |
|---|---|---|---|---|---|---|---|---|---|
| train | fixed_split | 16.818 | 69 | 67 | 264 | 31,4 | 23,9 | 25,3 | 305,7 |
| val | fixed_split | 1.008 | 14 | 14 | 72 | 35,0 | 23,1 | 28,5 | 143,0 |
| test | fixed_split | 689 | 15 | 15 | 47 | 36,3 | 20,2 | 33,8 | 106,5 |

Fold LOSO PM2.5: 103 fold (satu per grup), 105 stasiun, 38.211 label stasiun-jam pada seluruh periode.

## NO2

| Split | Protokol | Stasiun-jam | Stasiun | Grup | Jam | Rerata | Simpangan baku | Median | Maks |
|---|---|---|---|---|---|---|---|---|---|
| train | temporal_block_loso | 3.326 | 14 | 14 | 263 | 53,9 | 50,9 | 33,5 | 308,4 |
| val | temporal_block_loso | 853 | 13 | 13 | 72 | 58,3 | 53,3 | 37,1 | 234,5 |
| test | temporal_block_loso | 604 | 13 | 13 | 47 | 54,7 | 50,7 | 34,3 | 236,8 |

Fold LOSO NO2: 14 fold (satu per grup), 14 stasiun, 5.372 label stasiun-jam pada seluruh periode.

## Label di luar split tetap PM2.5

19.696 label PM2.5 valid tidak masuk split tetap karena berada pada jeda antarblok atau pada kombinasi stasiun × blok yang berbeda (mis. stasiun uji pada blok train). Label ini tetap dipakai oleh fold LOSO.
