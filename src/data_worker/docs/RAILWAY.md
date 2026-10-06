# Deployment Data Worker ke Railway dan migrasi data historis

Panduan satu kali untuk memindahkan data ground truth SPKU dari kolektor lama
(`udara-collector`, SQLite di Mac) ke PostgreSQL di Railway, lalu menyalakan
Data Worker per jam. Ditulis 30 September 2026, diperbarui 6 Oktober 2026.
Latar belakang skema dan zona waktu ada di [`DATA.md`](DATA.md); ringkasan
komponen ada di [`../README.md`](../README.md).

## Keadaan saat ini (6 Oktober)

| Butir | Status |
|---|---|
| Data Worker (Railway, project `humble-endurance`, environment `development`) | Berjalan per jam sejak 30 Sep 14:15Z. Sampai 6 Okt 05:15Z tidak ada jam yang terlewat; satu lintasan `aborted` (2 Okt 04:17Z, daftar stasiun kosong dari portal) |
| Sumber deployment | Cabang `feat/data-worker-spku`; dipindah ke `dev` setelah pull request digabung (bagian 4) |
| PostgreSQL Railway (service `database`) | Hasil migrasi 30 Sep 13:50Z (`LOLOS`, 127.221 observasi) ditambah lintasan Data Worker; observasi sejak 13 Sep 00:26Z |
| Kolektor lama (Mac, launchd) | Sengaja tetap berjalan sebagai cadangan; menulis ke `udara.sqlite` saja, tidak ke PostgreSQL |
| Portal dari IP Railway | Dapat dijangkau; lintasan pertama 119 stasiun tanpa kegagalan |

## 1. Siapkan service di Railway

1. **Basis data**: tambahkan service dari **template PostGIS** (bukan template
   Postgres biasa). Skema `ground_truth` memakai `geometry` dan indeks GiST;
   skema `pollution` dan `osm` juga memakai PostGIS.
2. **Data Worker**: tambahkan service dari repositori ini dengan
   **Root Directory `src/data_worker`**. `railway.json` menetapkan builder
   Dockerfile, perintah `python main.py cycle`, restart `NEVER`, dan cron
   `15 * * * *`. Bila Railway tidak membacanya, isi *Config-as-code path*
   dengan `/src/data_worker/railway.json`.

   Pada deployment pertama (`railway up`, 30 Sep) nilai cron dan restart dari
   `railway.json` tidak diterapkan (cron kosong, restart `ON_FAILURE`), sehingga
   keduanya diisi manual di Settings → Deploy. Periksa setelah setiap
   perubahan sumber deployment: `railway status --json` harus menunjukkan
   `cronSchedule` `15 * * * *` dan `nextCronRunAt` terisi. Config-as-code
   (`railway.json`) berstatus usang dan hanya didukung sampai 1 Desember 2026;
   rencanakan pindah ke `.railway/railway.ts` (`railway config migrate`).
3. Variabel service Data Worker:

   | Variabel | Nilai |
   |---|---|
   | `DATABASE_URL` | Referensi `${{database.DATABASE_URL}}` (alamat internal; `database` adalah nama service PostGIS) |
   | `SPKU_DB_SCHEMA` | `ground_truth` (bawaan, boleh dikosongkan) |
   | `SPKU_CONTACT` | Alamat kontak tim pada User-Agent (opsional) |

   Variabel MLflow dan MinIO mengikuti `.env.example`.
4. **Jangan aktifkan cron dulu** sampai langkah 2 selesai, agar lintasan pertama
   Data Worker tidak mendahului data historis (skrip migrasi menolak berjalan
   bila PostgreSQL sudah memuat lintasan yang tidak ada di SQLite).

## 2. Migrasi data historis (dijalankan di Mac)

Skrip harus dijalankan dari Mac karena `udara.sqlite` ada di sana. Pakai
**`DATABASE_PUBLIC_URL`** dari tab Variables service `database`; alamat
`*.railway.internal` hanya dapat dijangkau dari dalam Railway. Variabel itu
baru muncul setelah TCP Proxy diaktifkan (`database` → Settings → Networking).
Jangan menempelkan DSN ke terminal atau berkas; teruskan lewat substitusi
perintah, misalnya
`DATABASE_URL="$(railway variables --service database --kv | sed -n 's/^DATABASE_PUBLIC_URL=//p')"`.

```bash
cd ~/Documents/ITB/S2/Semester\ 1/Proyek\ Penelitian\ Terapan
python3 -m pip install "psycopg[binary]>=3.1"

# Salinan konsisten; aman walau kolektor lama sedang menulis
python3 -c "import sqlite3; s=sqlite3.connect('Scrapper/udara-collector/data/udara.sqlite'); d=sqlite3.connect('/tmp/udara-backup.sqlite'); s.backup(d); d.close()"

cd Repository/SIPPolusi/src/data_worker
DATABASE_URL='<DATABASE_PUBLIC_URL>' \
  python3 scripts/migrate_sqlite_to_postgres.py /tmp/udara-backup.sqlite
```

Keluaran harus berakhir dengan `verifikasi: LOLOS` (kode keluar 0). Skrip:

- menulis dalam satu transaksi; bila gagal, tidak ada yang tersimpan;
- membuat ekstensi PostGIS, skema `ground_truth`, dan tabelnya bila belum ada;
- menggeser −7 jam kolom `ts_utc` pada `observation`, `observation_ispu`,
  `meteo`, `observation_revision`, serta `station_status.newest_raw_utc`
  (label portal adalah WIB; lihat `DATA.md`), cap waktu lain tidak digeser;
- idempoten selama PostgreSQL hanya berisi data dari SQLite yang sama:
  menjalankannya ulang hanya menambah atau memperbarui baris;
- mencatat hasilnya pada tabel `ground_truth.migration_log`.

Periksa ulang tanpa menulis: tambahkan `--verify-only`.

**Jangan memakai `--force` setelah Data Worker menulis.** Tabel log
`fetch_log`, `roster_event`, dan `observation_revision` berkunci `id`, dan
skrip melewati baris SQLite yang `id`-nya sudah terpakai (`ON CONFLICT (id) DO
NOTHING`). Lintasan Data Worker memakai `id` yang sama dengan kolektor lama,
jadi baris SQLite tersebut hilang tanpa galat; jumlah baris bisa tampak sama
padahal isinya milik Data Worker. Verifikasi juga selalu `GAGAL` karena
`run` dan `station_status` memuat lintasan yang tidak ada di SQLite. Hal ini
terjadi pada 30 Sep (120 baris `fetch_log` dan 118 baris `roster_event`
terlewati) dan diselesaikan dengan mengosongkan skema lalu migrasi ulang tanpa
`--force`:

```sql
-- Hanya bila PostgreSQL boleh diisi ulang dari SQLite. Hentikan dulu dari
-- menit ke-15 (lintasan berjalan) dan ambil kunci yang sama dengan kolektor.
BEGIN;
SELECT pg_advisory_xact_lock(hashtext('spku-write:ground_truth'));
TRUNCATE ground_truth.station, ground_truth.station_metric, ground_truth.observation,
         ground_truth.observation_ispu, ground_truth.meteo, ground_truth.daily_ispu,
         ground_truth.station_status, ground_truth.run, ground_truth.snapshot,
         ground_truth.observation_revision, ground_truth.fetch_log,
         ground_truth.roster_event, ground_truth.migration_log
  RESTART IDENTITY CASCADE;
COMMIT;
```

Pengosongan ini juga menghapus lintasan Data Worker sejak migrasi. Pengukuran
48 jam terakhir terisi kembali pada lintasan berikutnya; yang lebih lama hanya
kembali bila ada di SQLite.

## 3. Peralihan

1. Aktifkan cron service Data Worker. Tunggu satu siklus (menit ke-15) dan
   periksa log: langkah `fetch_ground_truth` berstatus `ok`. Bila berstatus
   `unavailable` dengan lintasan `aborted` atau `failed`, kemungkinan portal
   memblokir IP Railway; laporkan ke tim sebelum melanjutkan. (Selesai 30 Sep.)
2. **Migrasi ulang tidak diperlukan.** Setiap lintasan Data Worker mengambil
   riwayat 48 jam seluruh stasiun, jadi pengukuran (`observation`,
   `observation_ispu`, `meteo`, `daily_ispu`) sejak migrasi terisi sendiri.
   Yang tidak ikut pindah hanya catatan lintasan kolektor lama pada masa
   tumpang tindih (`run`, `fetch_log`, `snapshot`, `station_status`), dan Data
   Worker mencatat lintasannya sendiri pada tabel yang sama. Migrasi ulang
   hanya berguna bila Data Worker tidak dapat menjangkau portal lebih dari 48
   jam sementara kolektor lama tetap berhasil; lakukan dengan pengosongan dan
   migrasi tanpa `--force` (bagian 2), bukan dengan `--force`.
3. Kolektor lama boleh dihentikan kapan saja setelah langkah 1:
   `launchctl unload -w ~/Library/LaunchAgents/id.udara.*.plist`.
   `udara.sqlite` dibiarkan sebagai arsip (tetap berlabel WIB). Untuk sementara
   kolektor ini sengaja dibiarkan berjalan sebagai cadangan; keduanya tidak
   saling mengganggu karena kolektor lama hanya menulis ke SQLite. Bebannya ke
   portal 121 permintaan per enam jam, di samping 121 per jam dari Data Worker.
4. Pantau kesehatan kapan saja: `railway run python main.py spku health`
   (atau `python main.py spku health` dengan `DATABASE_URL` publik). Pemeriksaan
   cepat lewat SQL: jam tanpa lintasan `full` sejak peralihan, dan
   `max(ts_utc)` pada `observation` (normalnya kurang dari dua jam di belakang
   `now()`).

## 4. Sumber deployment

Service Data Worker semula di-deploy dari cabang `feat/data-worker-spku`.
Setelah pull request ke `dev` digabung:

1. Ubah data_worker → Settings → Source → Branch menjadi `dev`, di luar menit
   ke-15 sampai ke-20. Perubahan ini memicu deployment yang menjalankan satu
   siklus; aman karena penulisan dikunci dan barisnya idempoten.
2. Periksa deployment baru: cabang `dev`, commit hasil penggabungan, cron
   `15 * * * *`, restart `NEVER`. Pengaturan yang diisi manual di dashboard
   mengalahkan `railway.json`; perubahan `railway.json` di `dev` tidak berlaku
   selama nilai dashboard masih terisi.
3. Hapus `feat/data-worker-spku` hanya setelah langkah 2.

Sejak itu, setiap penggabungan ke `dev` yang menyentuh `src/data_worker/**`
(`watchPatterns`) men-deploy ulang Data Worker.

## 5. Catatan penggabungan dengan cabang `dev` (30 September)

- `requirements.txt` memuat dependensi kedua cabang: SQLAlchemy, psycopg2,
  GeoAlchemy2, dan torch-geometric dari `dev`; requests dan psycopg 3 untuk
  konektor SPKU. Dockerfile memasang torch versi CPU lebih dulu.
- `init_db()` dari `dev` (skema `pollution`, `osm`, `mlflow`) dipanggil di awal
  setiap `python main.py cycle`; kegagalannya dicatat tanpa menghentikan
  siklus. Skema `ground_truth` dibuat terpisah oleh `spku.store.Store`.
- Kerangka inferensi `spatial_model/` dari `dev` dijalankan sebagai langkah
  `trigger_downscale_inference` pada `acquisition.py`, termasuk fallback IDW,
  menggantikan job tiga jaman pada `main.py` lama. Statusnya
  `not_implemented` sampai inferensinya diisi.
- Pada `docker-compose.yml` (lokal), service `data_worker` dijalankan dengan
  `python main.py serve --now`. Perintah bawaan image (`cycle`) keluar setelah
  satu siklus, dan `restart: unless-stopped` akan mengulangnya terus sehingga
  portal disapu setiap beberapa menit.
- Di Railway tidak ada `restart` berulang: cron `railway.json` menjalankan
  `cycle` sekali per jam.

## Hal yang belum pasti

- Portal sesekali mengembalikan daftar stasiun kosong (2 Okt 04:17Z di
  Railway; 25 dan 29 Sep pada kolektor lama). Lintasan dibatalkan tanpa
  menulis dan jam tersebut terisi oleh lintasan berikutnya; perlu dipantau
  bila terjadi berturut-turut.
- Skrip migrasi belum dapat menggabungkan tabel log dengan aman setelah Data
  Worker menulis (lihat bagian 2). Perbaikannya: cocokkan baris log berdasarkan
  isinya, bukan `id`, lalu beri `id` baru dan urutkan ulang menurut waktu.
- Dokumen proyek `dataset-final-dan-akuisisi.md` menyebut pergeseran waktu
  baru terjadi sejak 27 September, sedangkan pemeriksaan di `DATA.md`
  (termasuk contoh Kelapa Gading 21 September) menunjukkan label WIB pada
  seluruh periode. Skrip migrasi mengikuti temuan kedua; selaraskan sebelum
  data dipakai untuk pelatihan.
