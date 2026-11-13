# TI-AI-12: audit awal dan pekerjaan evaluasi final

**Status: evaluasi parsial; belum selesai.** [Benchmark v1](run-v1/REPORT.md) menambahkan run berpasangan PIC A dan weighted Dijkstra pada 50 OD baru dan satu field IDW yang sudah pernah diperiksa. Bukti ini tidak membuktikan keunggulan model atau menggantikan evaluasi pada field model spasial dan kondisi operasi yang lebih luas. TI-AI-11 telah menyediakan paket dan bukti reload PIC A, PM2.5, dan NO2 dalam kontainer baru ([verifikasi](../TI-AI-11/container-verification.md)).

## Bukti yang sudah tersedia

Perbandingan [TI-AI-11](../TI-AI-11/morl-comparison/README.md) memakai 200 pasangan OD utama, 11 preferensi, graf dan medan yang sama untuk PIC A, PIC B, regret prior dan weighted Dijkstra. Manifest merekam commit, konfigurasi, hash checkpoint, graf, medan dan sampling; hasil per permintaan, ringkasan, keputusan dan kasus gagal tersedia di folder itu. PIC A adalah checkpoint scalarized terpilih; pada α=0,5, 141 dari 200 rute utama identik dengan weighted Dijkstra. Hasil utama PIC A: keberhasilan 99,95%, pemenuhan preferensi 88,5%, dan median perubahan paparan 0%. Klasifikasinya tetap `conditional_candidate` karena target pengurangan paparan tidak tercapai.

Jalankan `python scripts/audit_ti_ai12_evidence.py` dari root untuk menghitung ulang [audit](evidence-audit.json) dari rekaman yang ada dan memverifikasi identitas input dengan SHA-256. Hitungan kegagalan di audit adalah **rekaman per permintaan/preferensi**, bukan jumlah decode unik: PIC B menggunakan ulang hasil satu decode untuk beberapa preferensi. Hash rute yang dibandingkan adalah hash ringkas yang tersimpan, bukan geometri lengkap. Rincian statistik dan denominasi decode ada di laporan sumber.

## Batasan yang menghalangi penutupan

- Skenario test sudah diperiksa sebelum pemilihan model. Hasil adalah eksploratif; tidak boleh disebut evaluasi buta independen. Medan utama berasal dari IDW ds-v0.3.0, bukan keluaran model spasial terdaftar; medan perturbasi adalah uji sintetis.
- Pada perbandingan TI-AI-11, runtime weighted Dijkstra tidak direkam. [Benchmark v1](run-v1/REPORT.md) mengukur kedua metode pada input dan mesin yang sama, tetapi tidak mengukur API end-to-end.
- Medan asli merupakan artefak terbangkit yang tidak ada di Git. Manifest mencatat hash dan kode pembangunnya, tetapi pengulangan memerlukan regenerasi dan pemeriksaan identitas; perbedaan versi writer dapat mengubah byte parquet. Hasil per skenario tidak menyimpan geometri lengkap.
- Model spasial NO2 serving fit tidak memiliki evaluasi tersendiri; evaluasi berasal dari checkpoint LOSO terpisah. Hasil PM2.5 split tetap bersifat eksploratif karena test pernah dilihat. Lihat [protokol spasial](../../PROTOKOL_EVALUASI.md) dan [bukti registry](../TI-AI-11/railway_registry_evidence.md).

## Sebelum run final

1. Tetapkan dan catat protokol, kriteria keputusan, populasi, sampel, timeout, perlakuan kegagalan dan metode pembanding **sebelum** melihat hasil. Gunakan data held-out yang independensinya terhadap pelatihan, validasi dan pemilihan model dapat dibuktikan; bila tidak tersedia, laporkan hasil sebagai eksploratif, bukan blind.
2. Bekukan dengan hash dataset dan field, graf, paket PIC A dan model spasial, konfigurasi, skenario, kode serta lingkungan. Perubahan input atau model setelah run dimulai harus menghasilkan versi run baru. Catat data yang dikecualikan dan alasannya.
3. Jalankan PIC A dan baseline weighted Dijkstra serta implementasi weighted A* proyek pada OD, bobot, batas dan perangkat yang sama. Ukur latensi dengan batas pengukuran yang sama, termasuk pencarian dan persiapan yang relevan; laporkan distribusi, kegagalan, fallback, sumber daya dan biaya pemuatan secara terpisah. Jangan gunakan hasil sukses saja sebagai denominator.
4. Simpan log, hasil mentah per skenario, metrik agregat, versi dan checksum artefak serta instruksi reproduksi. Jelaskan bila data atau lingkungan tidak dapat disediakan. Laporkan ancaman validitas, termasuk perbedaan antara replay dan operasi nyata.

Kriteria TI-AI-12 baru dapat dinilai terhadap paket bukti run final tersebut. Hasil negatif tetap merupakan hasil yang sah; jangan mengubah ambang atau menyembunyikan kasus gagal agar model tampak memenuhi target.
