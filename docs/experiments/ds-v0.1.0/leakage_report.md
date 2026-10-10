# Laporan kebocoran ds-v0.1.0

Status: **lolos** (8/8 pemeriksaan lolos).

| # | Pemeriksaan | Status | Pelanggaran | Rincian |
|---|---|---|---|---|
| 1 | Tidak ada kunci (stasiun, time window) ganda | lolos | 0 | labels.parquet: 0; lintas berkas split per target: 0 |
| 2 | Tidak ada grup stasiun lintas split | lolos | 0 | grup dengan >1 split stasiun: 0; grup lintas berkas split (protokol split tetap): 0; fold LOSO yang tidak menahan seluruh stasiun grupnya: 0 |
| 3 | Jarak antarblok temporal ≥ max(L, 24 jam) = 24 jam | lolos | 0 | batas konfigurasi: {'train->val': 25.0, 'val->test': 25.0}; teramati pada berkas split: {'train->val': 25.0, 'val->test': 25.0} |
| 4 | Tidak ada observasi identik lintas split | lolos | 0 | kunci (target, stasiun, time window) di >1 split: 0; pasangan stasiun berimpit (sel sama atau < 300 m) beda split: 0; pasangan stasiun beda split dengan nilai per jam identik: 0 |
| 5 | Fitur berbasis sensor tidak memakai stasiun uji | lolos | 0 | node dengan dist_nearest_sensor_m ≠ jarak ke stasiun train: 0; nilai fold LOSO ≠ jarak tanpa grup yang ditahan: 0; jarak minimum dari sel stasiun val/uji ke sensor: 520 m |
| 6 | Scaler dan fitur statis hanya dari data train / data tersedia | lolos | 0 | jam fit terakhir 2026-09-24 00:00:00+00:00 (batas 2026-09-24 00:00:00+00:00); node fit 738 dari 738 node train; baris target PM2.5 18748 = split train 18748; target NO2 per fold tanpa grup yang ditahan; tidak ada komposit statis yang di-fit (Landsat/VIIRS as-of), nilai dengan produced_at > waktu inferensi: 0 |
| 7 | Seluruh fitur GEOS-CF (dan cuaca) memenuhi waktu tersedia <= waktu inferensi | lolos | 0 | 473.976 baris fitur diperiksa; pelanggaran GEOS-CF 0, cuaca 0 |
| 8 | Jendela lag L = 24 time window tidak melewati batas split | lolos | 0 | val: jam masukan paling awal 2026-09-24 02:00:00+00:00 > τ terakhir train 2026-09-24 00:00:00+00:00 (0 sampel melanggar); test: jam masukan paling awal 2026-09-28 02:00:00+00:00 > τ terakhir val 2026-09-28 00:00:00+00:00 (0 sampel melanggar) |
