# Bukti verifikasi TI-DO-07

Panduan yang diverifikasi: [`data-worker-schedule.md`](data-worker-schedule.md).
Basis: cabang `TI-DO-07` dari `dev` @ `4a7140f`, 26 Oktober 2026.

**Lingkungan lokal:**
- macOS (Apple Silicon), Python 3.12;
- PostgreSQL 16 + PostGIS 3.4 (`postgis/postgis:16-3.4`) di Docker, hanya pada `127.0.0.1`;
- basis data uji terpisah `tido07`;
- portal SPKU asli untuk jalur berhasil, dan portal tiruan `scripts/spku_stub_portal.py` untuk jalur gagal.

PostgreSQL Railway tidak disentuh.

## 1. Uji otomatis

| Lingkungan | Hasil |
|---|---|
| `pytest` dengan PostGIS (`TEST_DATABASE_URL`) | 228 lulus |
| `pytest` tanpa basis data | 170 lulus, 58 dilewati (uji basis data) |

Uji baru di `src/data_worker/tests/test_worker_schedule.py` (31 kasus):

- **Status siklus:** dihitung dari status setiap langkah; status langkah yang tidak dikenal tidak diklaim sukses.
- **Lintasan SPKU gagal:** lintasan `aborted`/`failed` menjadi langkah `failed`, termasuk ketika time window masih
  berisi data dari lintasan sebelumnya.
- **Gagal sementara dan permanen:** 503 dan galat jaringan yang pulih setelah diulang; 503 yang tetap gagal setelah
  3 percobaan; 404 tidak diulang.
- **Catatan siklus:**
  - siklus berhasil dan gagal tercatat lengkap beserta status setiap langkah;
  - siklus yang tumpang tindih dicatat `skipped`;
  - SIGTERM mencatat siklus `failed` beserta langkah yang sudah selesai;
  - baris `running` dari proses yang mati ditutup oleh siklus berikutnya;
  - lintasan SPKU yang dihentikan tidak tertinggal `running`.
- **Idempotensi:** dua eksekusi untuk time window yang sama tidak menambah baris ground truth.
- **Status jadwal:** berhasil, terlambat, gagal, eksekusi ulang manual, `pending`, `stuck`, `missing`, dan jadwal
  yang tidak pernah berjalan.

Idempotensi Spatial Pollution Cache sudah diuji pada `test_spatial_edgeweight.py` (UT-OPS-02a/b dan uji
kompatibilitas cache), dan ikut lulus pada run di atas.

## 2. Skenario CLI

Seluruh skenario dijalankan dengan `python main.py ...` terhadap basis data `tido07`.
- **Skenario A dan B** memakai portal asli.
- **Skenario lain** memakai portal tiruan untuk time window 2026-09-12T22:00Z, yaitu jam yang dimuat halaman rekaman.

| # | Skenario | Perintah dan kondisi | Hasil | Kode keluar |
|---|---|---|---|---|
| A | Pemeriksaan awal | `check` | konfigurasi, impor modul, basis data: ok | 0 |
| B | Siklus terjadwal, portal asli | `cycle` pukul 15.14 UTC → window 14.00–15.00 | `ok`; 124 stasiun diurai, 117 stasiun dengan 231 pengukuran PM2.5/NO2 pada window; 130,7 detik | 0 |
| C | Gagal sementara | portal `flaky`: setiap halaman 503 lalu 200 | 5 permintaan pulih pada percobaan ke-2 (`retried_requests: 5`); langkah dan siklus `ok` | 0 |
| H | Eksekusi ulang window yang sama | `cycle --window-start 2026-09-12T22:00Z`, portal `ok` | `rows_new: 0`; jumlah baris tidak berubah (lihat bagian 3) | 0 |
| D | Gagal permanen | portal `down`: selalu 503 | daftar stasiun dan sitemap gagal setelah 3 percobaan (`failed_requests: 2`), lintasan `aborted`, langkah `failed` walaupun window masih berisi data lama; siklus `failed` | 1 |
| E | Portal kosong | portal `empty`: HTTP 200, nol observasi | lintasan `aborted` ("nol observasi; portal diduga sedang bermasalah"); siklus `failed` | 1 |
| F | Tumpang tindih | siklus kedua dimulai saat siklus pertama berjalan, `WORKER_LOCK_WAIT_SECONDS=0` | siklus kedua `skipped` ("siklus lain masih berjalan"); siklus pertama tetap `ok` | 1 / 0 |
| G | Dihentikan di tengah | `kill -TERM` 6 detik setelah siklus dimulai | siklus `failed` "dihentikan oleh sinyal SIGTERM", tanpa data separuh jadi. Run pertama memperlihatkan baris `ground_truth.run` tertinggal `running`; setelah diperbaiki (`spku/collect.py`), run ulang mencatat lintasan SPKU `failed` "dihentikan: CycleInterrupted" | 1 |
| I | Status jadwal | `status --hours 4` dalam empat keadaan | lihat bagian 4 | 0 / 3 / 1 / 0 |

**Cuplikan log C, gagal sementara:**

```
WARNING spku.fetch: http://127.0.0.1:8761/lokasi-spku gagal pada percobaan 1/3 (HTTP 503); diulang dalam 2.9 detik
WARNING spku.fetch: http://127.0.0.1:8761/lokasi-spku berhasil pada percobaan 2 setelah gagal sementara
...
INFO    data_worker.acquisition: siklus 2026-09-12T22:00Z/23:00Z: fetch_ground_truth -> ok
INFO    data_worker: siklus 2026-09-12T22:00Z/23:00Z (run 20261026T151654Z-d82de1) selesai: ok
```

**Cuplikan log D, gagal permanen:**

```
WARNING spku.fetch: http://127.0.0.1:8763/lokasi-spku gagal pada percobaan 2/3 (HTTP 503); diulang dalam 4.7 detik
ERROR   spku.fetch: http://127.0.0.1:8763/lokasi-spku tetap gagal setelah 3 percobaan: HTTP 503
ERROR   spku.collect: lintasan 20261026T151711Z-3b7ba0 dibatalkan: daftar stasiun kosong; lintasan dibatalkan tanpa menulis
WARNING data_worker.acquisition: siklus 2026-09-12T22:00Z/23:00Z: fetch_ground_truth -> failed (lintasan SPKU aborted:
        daftar stasiun kosong; lintasan dibatalkan tanpa menulis; time window masih berisi data lintasan sebelumnya)
ERROR   data_worker: siklus 2026-09-12T22:00Z/23:00Z (run 20261026T151711Z-e8643a) selesai: failed
```

**Cuplikan log F dan G, tumpang tindih dan SIGTERM:**

```
ERROR   data_worker: siklus 2026-09-12T22:00Z/23:00Z dilewati (20261026T151748Z-cb89b1): siklus lain masih memegang kunci
ERROR   data_worker: siklus 2026-09-12T22:00Z/23:00Z (run 20261026T151801Z-ca4c3c) tidak selesai: dihentikan oleh sinyal SIGTERM
```

**Isi `worker.cycle_run` setelah skenario B–G:**

| run_id | time window | trigger | status | failed_steps | note |
|---|---|---|---|---|---|
| 20261026T151424Z-bd4294 | 2026-10-26 14:00 | schedule | ok | | |
| 20261026T151654Z-d82de1 | 2026-09-12 22:00 | manual | ok | | |
| 20261026T151709Z-d6e48e | 2026-09-12 22:00 | manual | ok | | |
| 20261026T151711Z-e8643a | 2026-09-12 22:00 | manual | failed | fetch_ground_truth | |
| 20261026T151728Z-af4bae | 2026-09-12 22:00 | manual | failed | fetch_ground_truth | |
| 20261026T151744Z-3c48cd | 2026-09-12 22:00 | manual | ok | | |
| 20261026T151748Z-cb89b1 | 2026-09-12 22:00 | manual | skipped | | siklus lain masih berjalan setelah menunggu 0 detik |
| 20261026T151801Z-ca4c3c | 2026-09-12 22:00 | manual | failed | | dihentikan oleh sinyal SIGTERM |

## 3. Idempotensi

Jumlah baris pada skema ground truth uji (`observation` | `observation_ispu` | `meteo` | `station`):

| Setelah | Jumlah |
|---|---|
| C (lintasan pertama) | 1200 \| 624 \| 240 \| 3 |
| H (window yang sama dijalankan ulang) | 1200 \| 624 \| 240 \| 3 |
| D dan E (dua lintasan gagal) | 1200 \| 624 \| 240 \| 3 |

Lintasan ulang menulis 0 baris baru dan 0 revisi. Lintasan yang gagal tidak menulis apa pun. Catatan siklus
bertambah satu baris per eksekusi, sesuai tujuannya sebagai log eksekusi.

## 4. Status jadwal

**I1, jadwal aktif.** Window 14.00 dieksekusi; window sebelumnya belum pernah dijalankan pada basis data uji ini.

```
keadaan jadwal: active; putusan: healthy (kode keluar 0)
keberhasilan eksekusi terjadwal: 0.25

2026-10-26T14:00Z/15:00Z  ok       berhasil  [mulai -1 mnt]
2026-10-26T13:00Z/14:00Z  missing  tidak ada eksekusi (jadwal tidak berjalan)
2026-10-26T12:00Z/13:00Z  missing  tidak ada eksekusi (jadwal tidak berjalan)
2026-10-26T11:00Z/12:00Z  missing  tidak ada eksekusi (jadwal tidak berjalan)
```

Skenario B dijalankan manual pukul 15.14, satu menit sebelum waktu terjadwal; di Railway eksekusi dimulai oleh
cron pada 15.15.

**I2, jadwal tidak pernah berjalan** (skema catatan kosong):

```
keadaan jadwal: no_runs; putusan: schedule_missing (kode keluar 3)
```

**I3, eksekusi terjadwal terakhir gagal** (portal `down`):

```
keadaan jadwal: active; putusan: failing (kode keluar 1)
2026-10-26T14:00Z/15:00Z  failed   gagal  [mulai -1 mnt; 2 eksekusi; gagal: fetch_ground_truth]
```

**I4, setelah eksekusi ulang manual:** portal tiruan tidak berisi data untuk window ini, sehingga `degraded`.

```
keadaan jadwal: active; putusan: healthy (kode keluar 0)
2026-10-26T14:00Z/15:00Z  degraded berhasil, sebagian sumber terganggu  [mulai -1 mnt; 3 eksekusi]
```

Keterlambatan (`terlambat`, `delay_minutes`), kegagalan (`failed`, `skipped`, `stuck`), dan ketiadaan eksekusi
(`missing`, `no_runs`, `stopped`) memiliki keadaan dan kode keluar yang berbeda.

## 5. Pemenuhan acceptance criteria

| Kriteria | Bukti |
|---|---|
| Worker berjalan pada jadwal yang terdokumentasi dan menghasilkan log per eksekusi | `railway.json` (`15 * * * *`); [`data-worker-schedule.md`](data-worker-schedule.md); satu baris `worker.cycle_run` dan satu blok log per eksekusi (skenario B–G) |
| Eksekusi ulang tidak menghasilkan korupsi/duplikasi cache yang tidak diinginkan | Bagian 3; kunci siklus (skenario F); upsert cache UT-OPS-02; SIGTERM tidak meninggalkan data separuh jadi (G) |
| Kegagalan sumber atau proses terlihat pada status/log dan tidak diklaim sukses | Skenario D, E, F, G: status `failed`/`skipped`, kode keluar 1, galat bertingkat `error`; lintasan SPKU gagal tidak lagi tercatat `ok` walaupun data lama tersedia |
| Keterlambatan dan kegagalan dapat dibedakan dari tidak adanya jadwal | Bagian 4: `late`/`delay_minutes` vs `failed`/`stuck` vs `missing`/`no_runs`; kode keluar 0, 1, 3 |

## 6. Keterbatasan

- **Belum diverifikasi di Railway:** seluruh bukti di atas berasal dari lingkungan lokal. Setelah digabung dan
  di-deploy, siklus pertama di Railway membuat skema `worker`. Verifikasi lanjutan menjalankan `railway run python
  main.py status --hours 24` setelah beberapa jam berjalan.
- **Eksekusi yang dilewati Railway** (tumpang tindih di tingkat cron) tidak meninggalkan catatan dan terlihat
  sebagai `missing`; penyebabnya dapat dicek di riwayat eksekusi cron Railway.
- **EDO-04** (≥ 98% eksekusi terjadwal berhasil) memerlukan periode observasi; `scheduled_success_rate` pada
  `main.py status --hours 720` menyediakan angkanya.
