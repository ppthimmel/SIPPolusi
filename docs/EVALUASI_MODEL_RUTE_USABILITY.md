# Evaluasi model rute pada uji kebergunaan: perubahan dari Dokumen Desain

Memperbarui Subbab 5.5 Dokumen Desain ("Tugas 3 - Kelompok 1 - Dokumen Desain Penelitian Terapan")
untuk evaluasi model rute yang digabung dengan uji kebergunaan. Dokumen ini hanya mencatat apa yang
berubah dan cara menjalankannya; rancangan lain pada Subbab 5.5 tetap berlaku.

## Perubahan dari Dokumen Desain

| Butir | Dokumen Desain (Subbab 5.5) | Sekarang |
|---|---|---|
| Cara membandingkan | Tiga konfigurasi dijalankan pada aplikasi | Peserta uji kebergunaan menerima **tangkapan layar yang sudah dibuat** untuk tiap konfigurasi, lalu mendeskripsikannya di Google Forms |
| Model *downscaling* | C0: IDW; C1 dan C2: ST-GNN | Model [PR #115](https://github.com/ppthimmel/SIPPolusi/pull/115) (XGBoost dengan koreksi residu IDW untuk NO2; PM2.5 tetap ST-GNN) **pada semua konfigurasi** |
| Yang berbeda antarkonfigurasi | Model *downscaling* dan pencari rute | **Hanya pencari rute**: A* berbobot (baseline) atau MORL |
| Pencari rute baseline | A* rute tercepat | A* berbobot pada fungsi tujuan yang sama dengan MORL (`weighted`), supaya perbedaan hasil berasal dari *policy*, bukan dari tujuan optimasi |
| Label pada tangkapan layar | Nama konfigurasi | Label netral (mis. Rute A, Rute B); nama model tidak muncul |

Akibatnya konfigurasi dengan IDW (C0) dan ST-GNN + A* (C1) tidak lagi dijalankan. Perbandingan menjadi dua
konfigurasi dengan data polusi yang sama persis:

| Konfigurasi | Data polusi | Pencari rute | `ROUTE_SOLVER_PROVIDER` |
|---|---|---|---|
| Baseline | model PR #115 | A* berbobot: α·T/T_ref + β·E/E_ref | `weighted` |
| MORL | model PR #115 | *policy* MORL | `morl` |

T_ref dan E_ref adalah waktu tempuh dan paparan rute tercepat, acuan yang sama dengan *policy* MORL
(`fastest_reference`). Rute pembanding ("rute tercepat") sama pada keduanya.

## Perubahan pada sistem

- **Penyedia `weighted`** ([PR #120](https://github.com/ppthimmel/SIPPolusi/pull/120),
  [`solver/weighted.py`](../src/backend/solver/weighted.py)). Menggunakan `route_model.weighted_astar`,
  mengikuti slider "Prioritas perjalanan" seperti MORL, dan dapat dibatalkan saat batas waktu. Memakai
  `route_source: policy` dan `preference_applied: true` agar antarmuka tidak membedakannya dari MORL
  (evaluasi buta). Pembedanya ada di `model_version` (`weighted-astar-1`) dan `metadata.route_model`.
- **Pengganti model lewat backend.** Konfigurasi dipilih di server dengan `ROUTE_SOLVER_PROVIDER`, bukan di
  frontend, sehingga tidak ada kontrol pemilih yang bisa terlihat oleh peserta.
- **Simulasi lokasi di frontend yang dideploy.** Sementara, build frontend memakai `VITE_GPS_SPOOF=on`
  ([`deploy-gcp.yml`](../.github/workflows/deploy-gcp.yml)), sehingga `?lokasi=monas&simulasi=normal`
  bekerja di Cloud Run dan titik awal bisa dibuat identik pada setiap tangkapan layar.

## Prosedur pengambilan tangkapan layar

1. Gabungkan PR #120 ke `dev` dan tunggu *deploy* GCP selesai. Setiap *deploy* mengganti semua variabel
   lingkungan backend dan mengembalikan penyedia ke `morl`.
2. **Bekukan jam polusi.** Jeda `data-worker-hourly` (Cloud Scheduler) agar seluruh tangkapan layar memakai
   data jam yang sama; bila tidak, perbedaan rute bisa berasal dari data yang berganti.
3. **Konfigurasi baseline.** Ganti penyedia, tunggu revisi baru siap:

   ```
   gcloud run services update cache-api --region asia-southeast2 \
     --update-env-vars ROUTE_SOLVER_PROVIDER=weighted
   ```

   `--update-env-vars` hanya mengubah variabel yang disebut; jangan pakai `--set-env-vars`, yang menghapus
   variabel lain.
4. **Konfigurasi MORL.** Kembalikan, dan matikan *fallback* agar hasil cadangan tidak terpotret sebagai MORL:

   ```
   gcloud run services update cache-api --region asia-southeast2 \
     --update-env-vars ROUTE_SOLVER_PROVIDER=morl,ROUTE_SOLVER_FALLBACK=none
   ```

5. Pada tiap konfigurasi, buka asal-tujuan yang sama (`?lokasi=…&simulasi=…`), prioritas perjalanan yang
   sama, dan ambil tangkapan layar dengan ukuran jendela yang sama. Potong atau tutupi keterangan versi
   model sebelum dimasukkan ke Google Forms; beri label netral.
6. Pastikan tiap tangkapan layar tercatat konfigurasinya di lembar kerja peneliti (bukan di gambar). Di log
   backend, baris `route_inference` memuat `solver` dan `model_version` untuk setiap permintaan.

## Setelah evaluasi selesai

- Hapus baris `VITE_GPS_SPOOF=on` (dan komentar `TEMPORARY`) dari `deploy-gcp.yml`, lalu gabungkan lagi.
  Selama baris itu ada, pemalsuan lokasi aktif untuk semua pengunjung.
- Aktifkan kembali `data-worker-hourly`.
- Kembalikan `ROUTE_SOLVER_PROVIDER=morl` dan `ROUTE_SOLVER_FALLBACK=baseline` (deploy berikutnya juga
  melakukannya).

## Catatan keterbatasan

- `weighted` mensyaratkan `travel_time_s > 0` pada setiap ruas; data nyata aman karena basis data memaksa
  `length_m > 0` ([`solver/README.md`](../src/backend/solver/README.md)).
- Penyedia `mock` mengoptimalkan fungsi tujuan yang sama dan bisa dipakai bila `weighted` belum tersedia,
  tetapi `weighted` yang dipakai untuk evaluasi.
