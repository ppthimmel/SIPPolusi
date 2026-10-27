# Data Worker terjadwal (TI-DO-07)

Panduan operasional jadwal Data Worker: kapan siklus berjalan, apa yang dicatat setiap eksekusi, arti setiap
status, dan cara membedakan keterlambatan, kegagalan, serta jadwal yang tidak berjalan. Bukti verifikasi:
[`verification-TI-DO-07.md`](verification-TI-DO-07.md).

## Jadwal

| Butir | Nilai |
|---|---|
| Jadwal | `15 * * * *` (UTC), cron job Railway pada service Data Worker (`src/data_worker/railway.json`) |
| Perintah | `python main.py cycle` (`CMD` Dockerfile) |
| Time window | `[H-1, H)` UTC, yaitu jam yang baru berakhir (`TimeWindow.just_ended`); siklus pukul 05.15 memproses 04.00–05.00 |
| Waktu terjadwal | Akhir time window + 15 menit (Dokumen Desain Tabel 3.9 dan Gambar 3.7, B1) |
| Restart | `NEVER`: kontainer berhenti setelah siklus selesai |
| Lingkungan tanpa cron | `python main.py serve` menjalankan siklus pada menit ke-15 setiap jam dengan penjadwal internal |

Railway tidak memulai eksekusi cron baru selama eksekusi sebelumnya masih berjalan. Eksekusi yang dilewati
Railway tidak pernah menyala, sehingga tidak meninggalkan catatan; `main.py status` melaporkannya sebagai
time window tanpa eksekusi.

## Catatan per eksekusi

Setiap eksekusi `cycle` atau `serve` menulis satu baris pada `worker.cycle_run` (skema dapat diganti dengan
`WORKER_DB_SCHEMA`). Tabel dibuat otomatis pada siklus pertama (`CREATE ... IF NOT EXISTS`).

| Kolom | Isi |
|---|---|
| `run_id` | Identitas eksekusi, `YYYYMMDDTHHMMSSZ-xxxxxx` |
| `time_window_start`, `time_window_end` | `TimeWindow` yang diproses |
| `trigger` | `schedule` (cron), `serve` (penjadwal internal), atau `manual` (`--window-start`) |
| `scheduled_utc` | Waktu terjadwal untuk time window tersebut |
| `started_utc`, `finished_utc` | Waktu mulai dan selesai |
| `status` | `running`, `ok`, `degraded`, `failed`, atau `skipped` |
| `steps` | Status, durasi, sumber, dan alasan setiap langkah B2–B15, diperbarui setelah setiap langkah |
| `failed_steps` | Nama langkah yang gagal |
| `host`, `deployment_id` | Kontainer dan `RAILWAY_DEPLOYMENT_ID` |
| `note` | Alasan dilewati, dihentikan, atau ditutup |

Log stdout (JSON di Railway) memuat baris yang sama untuk setiap eksekusi: mulai, status setiap langkah, status
akhir, dan laporan JSON. Galat bertingkat `error`, status `degraded` atau `unavailable` bertingkat `warn`.
Lintasan SPKU tetap dicatat terpisah pada `ground_truth.run` dan `ground_truth.fetch_log`.

## Status

**Status langkah:**

| Status | Arti |
|---|---|
| `ok` | Sumber berhasil diambil dan data time window tersedia |
| `degraded` | Sumber berhasil, tetapi sebagian (mis. sebagian stasiun SPKU) tetap gagal setelah dicoba ulang |
| `unavailable` | Sumber berhasil diambil, tetapi tidak ada data untuk time window ini |
| `failed` | Galat proses, atau sumber gagal pada siklus ini (lintasan SPKU `aborted`/`failed`), **walaupun** time window masih berisi data dari lintasan sebelumnya |
| `disabled`, `not_implemented` | Langkah dinonaktifkan atau belum diimplementasikan; tidak menurunkan status siklus |

**Status siklus dan kode keluar `cycle`:**

| Status | Kondisi | Kode keluar |
|---|---|---|
| `ok` | Seluruh langkah yang diimplementasikan `ok` | 0 |
| `degraded` | Tidak ada yang `failed`, tetapi ada `degraded` atau `unavailable` | 0 |
| `failed` | Sekurang-kurangnya satu langkah `failed`, atau proses dihentikan (SIGTERM) sebelum selesai | 1 |
| `skipped` | Siklus lain masih memegang kunci setelah `WORKER_LOCK_WAIT_SECONDS` (bawaan 600 detik) | 1 |
| — | Konfigurasi tidak sah, siklus tidak dijalankan | 2 |

Kegagalan satu langkah tidak menghentikan langkah berikutnya (UT-DW-01b); kode keluar dan status mencatat
kegagalan tersebut sehingga siklus tidak pernah diklaim sukses.

## Coba ulang dan jalur gagal

- **Gagal sementara:** permintaan SPKU yang gagal (HTTP selain 200 dan 404, atau galat jaringan) dicoba ulang
  sampai `SPKU_RETRIES` kali (bawaan 3, Tabel 3.19) dengan jeda eksponensial. Setiap pengulangan dicatat sebagai
  peringatan; permintaan yang pulih dihitung pada `retried_requests` langkah `fetch_ground_truth`.
- **Gagal permanen:** permintaan yang tetap gagal setelah seluruh percobaan dicatat sebagai galat dan dihitung pada
  `failed_requests`. Stasiun yang gagal ditandai tidak tersedia (lintasan `partial`, langkah `degraded`); bila
  daftar stasiun, mayoritas halaman, atau seluruh observasi gagal, lintasan dibatalkan tanpa menulis (`aborted`)
  dan langkah menjadi `failed`. Siklus tetap melanjutkan langkah berikutnya (Tabel 3.12).
- **Dihentikan:** SIGTERM (mis. redeploy saat siklus berjalan) menutup siklus dan lintasan SPKU sebagai `failed`.
  Penulisan SPKU berada dalam satu transaksi sehingga tidak ada data separuh jadi.
- **Proses mati tanpa sinyal** (mis. kehabisan memori): baris `running` yang tertinggal ditutup sebagai `failed`
  oleh siklus berikutnya, dan `main.py status` melaporkannya `stuck` setelah 60 menit.
- **Sesi basis data yatim:** kontainer yang dihentikan paksa tidak menutup koneksinya, sehingga transaksi yang
  sedang menulis (mis. `COPY` ke `pollution.edge_cell`) tetap memegang kuncinya. Tanpa pengaturan, PostgreSQL baru
  menyadarinya setelah keepalive TCP bawaan (2 jam), dan siklus berikutnya tertahan di `init_db`. Karena itu:
  - setiap koneksi Data Worker memasang keepalive sisi server lewat `PGOPTIONS` (`db_session.py`): 60 detik tanpa
    lalu lintas, lalu 6 probe berjeda 10 detik. Sesi yatim ditutup dan transaksinya dibatalkan ±2 menit setelah
    kontainer hilang. Nilai `tcp_keepalives_*` yang sudah ada di `PGOPTIONS` tidak ditimpa;
  - `init_db` menunggu kunci tabel paling lama 30 detik per transaksi. Bila habis, kegagalannya dicatat dan siklus
    tetap berjalan dengan tabel yang sudah ada. Batas ini juga mencegah `ALTER TABLE` migrasi yang mengantre
    menahan baca Cache API.

  Memeriksa sesi yang menunggu kunci (hanya membaca):

  ```sql
  SELECT pid, state, wait_event_type, wait_event, now() - query_start AS age, left(query, 80)
  FROM pg_stat_activity WHERE datname = current_database() AND state <> 'idle' ORDER BY query_start;
  ```

Batas waktu SPKU di kode bawaannya 45 detik per permintaan (`SPKU_TIMEOUT_SECONDS`), sedangkan Tabel 3.19
menyebut 30 detik; nilai 45 detik dipertahankan karena halaman rinci portal kadang lambat.

## Eksekusi ulang dan idempotensi

Time window yang gagal dapat dijalankan ulang tanpa menunggu jadwal:

```bash
python main.py cycle --window-start 2026-10-26T14:00Z
```

Eksekusi ulang tercatat sebagai baris baru dengan `trigger = manual`. Penulisan bersifat idempoten:

- **Ground truth SPKU:** upsert dengan kunci `(station_uuid, metric, ts_utc)`; lintasan ulang menulis 0 baris
  baru bila portal tidak berubah.
- **Spatial Pollution Cache:** upsert dengan kunci `(edge_id, time_window_start)` (Gambar 3.7, B26; UT-OPS-02).
- **Kunci siklus:** advisory lock `data-worker-cycle` memastikan hanya satu siklus yang berjalan pada satu waktu,
  termasuk siklus manual yang tumpang tindih dengan jadwal.

## Memeriksa jadwal

```bash
python main.py status --hours 24          # ringkasan teks
python main.py status --hours 168 --json  # satu minggu, JSON
```

Di Railway: `railway run python main.py status` pada service Data Worker. Perintah ini hanya membaca.

| Keadaan time window | Arti |
|---|---|
| `ok`, `degraded` | Dieksekusi dan berhasil; `terlambat` bila mulai lebih dari 10 menit setelah waktu terjadwal |
| `failed`, `skipped` | Dieksekusi tetapi gagal atau dilewati karena tumpang tindih |
| `running`, `stuck` | Sedang berjalan; `stuck` bila lebih dari 60 menit (`--stuck-minutes`) |
| `pending` | Belum ada eksekusi, tetapi masih dalam toleransi 30 menit setelah waktu terjadwal (`--grace-minutes`) |
| `missing` | Tidak ada eksekusi sama sekali setelah toleransi: jadwal tidak berjalan atau dilewati Railway |

| Putusan | Kondisi | Kode keluar |
|---|---|---|
| `healthy` | Time window terakhir yang jatuh tempo `ok`, `degraded`, `running`, atau `pending` | 0 |
| `failing` | Time window terakhir `failed`, `skipped`, atau `stuck` | 1 |
| `schedule_missing` | Tidak ada eksekusi terjadwal pada rentang (`no_runs`), dua time window terakhir tanpa eksekusi terjadwal (`stopped`), atau time window terakhir yang jatuh tempo tanpa eksekusi terjadwal setelah toleransi | 3 |

Keadaan time window adalah hasil eksekusi terakhirnya, termasuk eksekusi ulang manual, tetapi keadaan jadwal dan
putusan hanya memakai eksekusi terjadwal (`schedule`/`serve`). Time window yang hanya dieksekusi manual setelah
toleransi ditandai `schedule_missed` ("tanpa eksekusi terjadwal (hanya manual)"), sehingga eksekusi manual tidak
menyamarkan cron yang tidak berjalan.

`scheduled_success_rate` adalah proporsi time window jatuh tempo yang eksekusi terjadwal pertamanya berhasil, tanpa
eksekusi ulang manual. Ini ukuran EDO-04 (target ≥ 98%).

## Pengaturan

| Variabel | Bawaan | Fungsi |
|---|---|---|
| `WORKER_DB_SCHEMA` | `worker` | Skema tabel `cycle_run` |
| `WORKER_LOCK_WAIT_SECONDS` | `600` | Lama menunggu kunci siklus sebelum dicatat `skipped` |
| `SPKU_RETRIES`, `SPKU_TIMEOUT_SECONDS` | `3`, `45` | Percobaan dan batas waktu per permintaan portal |
| `SPKU_BASE_URL` | portal asli | Diganti hanya untuk uji terhadap `scripts/spku_stub_portal.py` |

## Penerapan di Railway

- **Tanpa perubahan konfigurasi cron:** jadwal dan perintah tetap dari `railway.json`.
- **Migrasi basis data:** siklus pertama setelah deploy membuat skema `worker` dan tabel `cycle_run` pada PostgreSQL
  Railway. Pembuatan ini idempoten dan tidak menyentuh skema lain.
- **Pemantauan:** jalankan `python main.py status` secara berkala, atau dari pemeriksaan kesehatan TI-DO-08; kode
  keluar 1 dan 3 menandai kegagalan dan jadwal yang tidak berjalan.
