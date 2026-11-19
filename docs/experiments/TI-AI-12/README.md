# TI-AI-12: hasil dan artefak evaluasi akhir

Evaluasi ini membekukan model rute scalarized PIC A, graf, skenario, konfigurasi dan dua field, serta menyimpan hasil per permintaan dan batasannya. TI-AI-11 menyediakan [paket dan bukti reload](../TI-AI-11/container-verification.md) model rute dan spasial. Hasil tetap bersifat eksploratif: tidak menunjukkan bahwa MORL lebih unggul atau mengurangi paparan secara umum.

## Hasil dan keputusan

| Bukti | Cakupan dan hasil |
|---|---|
| [Run v1](run-v1/REPORT.md) | 50 OD × 3 preferensi pada satu field IDW historis; 150/150 rute PIC A valid. |
| [Run v2](run-v2/REPORT.md) dan [v3](run-v3/REPORT.md) | OD dan preferensi yang sama pada satu field keluaran PM2.5 dan NO₂ terdaftar. Kedua metode menghasilkan 150/150 rute valid. Seluruh keluaran nonwaktu cocok di kedua run. Pada α=0,5, PIC A cocok dengan optimum weighted Dijkstra pada 33/50 pasangan; median pengurangan paparan terhadap rute tercepat 0%, di bawah target 4%. Pemenuhan preferensi 47/50, dengan 12/50 benar-benar bergeser dalam arah yang dimaksud. |
| [Run v3](run-v3/REPORT.md) | Acuan latensi, tanpa permintaan gateway yang sengaja berjalan bersamaan: median/p95 PIC A 194,4/517,4 ms dan Dijkstra 13,3/48,5 ms. Pengukuran lokal satu thread ini tidak mencakup pemuatan, jaringan atau API. |
| [Gateway v1](gateway-v1/REPORT.md) | Sembilan permintaan sepeda berhasil memakai PIC A tanpa fallback; permintaan jalan kaki memakai baseline dengan fallback bertanda, dan preferensi tidak valid ditolak HTTP 400. Window replay ditandai kedaluwarsa. |

PIC A tetap **`conditional_candidate`**, sesuai [keputusan perbandingan TI-AI-11](../TI-AI-11/morl-comparison/decision.json): target pengurangan paparan tidak terpenuhi. Pada v2 dan v3, Dijkstra optimal untuk objektif yang sama dan lebih cepat pada lingkungan uji ini. Hasil tidak mendukung klaim keunggulan praktis PIC A. Weighted A* proyek dan penggunaan sumber daya per metode belum diukur; keduanya merupakan celah yang diketahui, bukan hasil yang diklaim dalam evaluasi ini.

## Artefak dan reproduksi

[Protokol v1](benchmark-protocol.md) dan [protokol field spasial](benchmark-spatial-protocol.md) dicatat sebelum run. Manifest setiap run memuat commit, image, hash checkpoint, graf dan field, konfigurasi sampling serta pasangan OD. Field IDW dan field spasial beserta lineage dibekukan di [`inputs/`](inputs/); kedua CSV terkompresi menambah sekitar **4,2 MB** ke Git sebagai input audit. Berkas asli parquet dan image tidak disimpan di Git; batas pengulangan dicatat pada laporan masing-masing.

Setiap folder run menyimpan `scenarios.jsonl.gz`, `summary.json`, `quality-summary.json` dan manifest. Jalankan `python scripts/summarize_ti_ai12.py docs/experiments/TI-AI-12/run-v3/scenarios.jsonl.gz` untuk menghitung ulang ringkasan kualitas. Kasus gagal dihitung dalam denominator; tidak ada kegagalan pada v1–v3. Kegagalan dan perlakuannya dari evaluasi terdahulu tetap tersedia pada [TI-AI-10](../TI-AI-10/README.md) dan [perbandingan TI-AI-11](../TI-AI-11/morl-comparison/README.md). Gateway mencatat fallback dan penolakan input secara terpisah.

**Koreksi p95 setelah review:** ringkasan waktu v1–v3 dihitung ulang dari waktu per skenario menggunakan *nearest rank*, yaitu nilai urutan `ceil(0,95 × n)`. Ringkasan awal memilih urutan ke-142 dari 150, sedangkan definisi ini memakai urutan ke-143. Data per skenario, input, manifest dan commit yang menjalankan eksperimen tidak diubah; skrip benchmark terkini memakai definisi yang sama. Koreksi ini tidak mengubah kesimpulan.

## Ancaman validitas dan batas pengulangan

- Field dan populasi test sudah pernah diperiksa sebelum pemilihan model; hasil ini bukan evaluasi buta independen. Hanya 50 OD sepeda, tiga preferensi dan satu window per field diuji; titik akhir berada di blok test tetapi ruasnya dapat melintasi blok lain.
- Field spasial berasal dari satu replay data yang dikumpulkan sebelumnya. PM2.5 dan NO₂ dipakai tanpa fallback; paket PM2.5 ditandai akibat perbedaan versi minor kontrak. Model NO₂ *serving fit* tidak memiliki evaluasi akurasi sendiri; evaluasi LOSO memakai checkpoint terpisah. Split test PM2.5 pernah dilihat saat pengembangan.
- Sebanyak 1.177 ruas graf yang tidak memiliki bobot diisi median pada benchmark; jumlah ruas imputasi yang dilalui tiap rute tidak direkam. Hasil menyimpan hash rute ringkas, bukan geometri lengkap.
- Gateway diuji dari cache yang sudah tersedia, bukan dari satu siklus akuisisi Worker live tanpa putus. Uji layanan hanya memakai satu OD, window yang sudah kedaluwarsa, serta kasus fallback dan validasi terbatas; tidak membuktikan ketahanan upstream atau performa produksi.
- Sebagian run v2 dan uji gateway berlangsung bersamaan; v3 dipakai untuk pelaporan latensi tanpa permintaan gateway yang sengaja berjalan bersamaan. Beban host tidak sepenuhnya dikendalikan; timeout, weighted A* dan sumber daya belum diukur.
