# Pengulangan field model spasial tanpa uji gateway bersamaan

Run v3 memakai [protokol v2](../benchmark-spatial-protocol.md), kode commit `acb9018`, field dan 50 OD yang sama dengan [run v2](../run-v2/REPORT.md). Run selesai exit code 0 dan tidak ada permintaan gateway yang sengaja dijalankan bersamaan; beban host lain tidak dikendalikan. Pada 150 permintaan kedua metode berhasil dan seluruh rute valid. Median/p95 PIC A 194,4/517,4 ms dan Dijkstra 13,3/48,5 ms. Waktu tidak mencakup API, pemuatan, atau jaringan dan tidak memakai timeout.

Semua 150 hasil rute dan metrik nonwaktu cocok dengan v2; pada α=0,5 PIC A cocok dengan optimum pada 33/50 pasangan, rata-rata selisih objektif 0,091%, dan median pengurangan paparan terhadap rute tercepat 0%. Ringkasan kualitas, metode perhitungan, keterbatasan satu window serta cara mengulang dijelaskan pada [laporan v2](../run-v2/REPORT.md); gunakan direktori keluaran baru dan jangan menimpa v2 atau v3. Keluaran per skenario dan manifest berada di folder ini.

**Koreksi ringkasan setelah review:** p95 dihitung ulang dari hasil per skenario dengan *nearest rank* (`ceil(0,95 × n)`), bukan indeks pada kode run semula. Input dan hasil per skenario tidak diubah. Untuk memeriksa ringkasan waktu ini pada checkout saat ini, jalankan `python scripts/summarize_ti_ai12_latency.py docs/experiments/TI-AI-12/run-v3/scenarios.jsonl.gz`.
