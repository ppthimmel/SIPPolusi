# Benchmark field model spasial — run v2

Pada 13 November 2026, [protokol v2](../benchmark-spatial-protocol.md) membandingkan PIC A dan weighted Dijkstra pada 50 OD × 3 preferensi, dengan field keluaran model PM2.5 dan NO₂ terdaftar dari replay TI-AI-11. Proses selesai exit code 0. Pasangan sama dengan run v1; kode dan input dibekukan pada commit `acb9018` sebelum run.

| Ukuran | PIC A | Weighted Dijkstra |
|---|---:|---:|
| Rute berhasil / permintaan | 150/150 | 150/150 |
| Rute invalid | 0 | 0 |
| Median | 202,4 ms | 13,1 ms |
| p95 | 473,5 ms | 50,8 ms |
| Maksimum | 1.781,1 ms | 64,0 ms |

Pada α=0,5, 33/50 hash rute PIC A sama dengan optimum; rata-rata selisih objektif 0,091% dan maksimum 2,092%. Sembilan rute lebih lambat sekaligus lebih terpapar daripada rute tercepat. Median pengurangan paparan adalah 0%, sehingga target 4% tidak tercapai pada sampel ini. Arah preferensi terpenuhi 47/50 pasangan (94%), tetapi hanya 12/50 (24%) berubah rute dalam arah yang dimaksud; rute yang sama dihitung terpenuhi. Pada run ini Dijkstra lebih cepat dan merupakan optimum untuk objektif yang sama; hasil ini tidak membuktikan manfaat praktis PIC A.

[Manifest](manifest.json) merekam hash model, graf, field, versi perangkat lunak, 50 pasangan dan sampling; [hasil per permintaan](scenarios.jsonl.gz), [ringkasan waktu](summary.json) dan [ringkasan kualitas](quality-summary.json) mencakup seluruh kasus. Kegagalan dihitung dalam denominator; tidak ada yang terjadi di run ini. `python scripts/summarize_ti_ai12.py docs/experiments/TI-AI-12/run-v2/scenarios.jsonl.gz` menghasilkan ulang ringkasan kualitas.

Sumber field adalah satu window replay, bukan sampel live baru atau evaluasi buta. PM2.5 memakai paket 1.0 dengan status kompatibilitas minor `flagged`; kedua polutan tidak fallback. Sebanyak 1.177 ruas graf yang tidak memiliki bobot diisi median oleh `RouteEnv`; jumlah yang dilalui tiap rute tidak direkam. Pengukuran memakai satu thread, graf penuh, tanpa timeout, pemuatan, jaringan atau API; tidak dapat digeneralisasi menjadi latensi end-to-end. **Sebagian run v2 berlangsung bersamaan dengan uji gateway**, sehingga latensinya dapat terpengaruh beban lain; run v3 mengulang protokol tanpa permintaan gateway bersamaan. Dua run berbeda field juga berjalan pada waktu berbeda sehingga perbandingan latensinya antar-field tidak terkontrol. Hasil per skenario hanya menyimpan hash rute ringkas. Field, lineage dan kedua input v1/v2 tersedia di [`inputs/`](../inputs/); [bukti gateway](../gateway-v1/REPORT.md) menggunakan replay yang sama pada jalur layanan.

Untuk mengulang, checkout `acb9018`, dekompresi `inputs/spatial-replay-20261113.csv.gz` menjadi CSV sementara, lalu jalankan `python scripts/benchmark_ti_ai12_spatial.py --weights <CSV> --out <direktori-baru> --revision acb9018 --image-id ce0b1db66e99` pada image Data Worker `sha256:ce0b1db66e998cdd73cf0a90dfb138b028ce2c922740d0d8e34a13b8fe152496` dengan `PYTHONPATH` ke root repositori. Script memeriksa hash CSV dan checkpoint. Waktu tidak akan identik pada mesin atau beban berbeda; jangan menimpa run ini.
