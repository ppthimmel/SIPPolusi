# Deployment Data Worker ke Railway dan migrasi data historis

Panduan satu kali untuk memindahkan data ground truth SPKU dari kolektor lama
(`udara-collector`, SQLite di Mac) ke PostgreSQL di Railway, lalu menyalakan
Data Worker per jam. Ditulis 30 September 2026. Latar belakang skema dan zona
waktu ada di [`DATA.md`](DATA.md); ringkasan komponen ada di
[`../README.md`](../README.md).

## Keadaan saat ini

| Butir | Status |
|---|---|
| Kolektor lama (Mac, launchd) | Masih berjalan: lintasan penuh tiap 6 jam, cuplikan beranda tiap 30 menit |
| Data lokal `udara.sqlite` (30 Sep) | 125.444 observasi, 70.344 ISPU per jam, 79.880 meteo, 119 stasiun, sejak 13 Sep 2026 |
| PostgreSQL Railway | Belum ada data |
| Gladi migrasi (PostGIS 16 lokal) | `LOLOS`: jumlah baris dan checksum sama, rentang observasi 13 Sep 00:26Z s.d. 30 Sep 06:40Z (UTC) |
| Siklus Data Worker pada data hasil migrasi | Berhasil; 209 pengukuran PM2.5/NO2 untuk 30 Sep 05:00-06:00Z; skema `pollution` dan `osm` ikut dibuat |

## 1. Siapkan service di Railway

1. **Basis data**: tambahkan service dari **template PostGIS** (bukan template
   Postgres biasa). Skema `ground_truth` memakai `geometry` dan indeks GiST;
   skema `pollution` dan `osm` juga memakai PostGIS.
2. **Data Worker**: tambahkan service dari repositori ini dengan
   **Root Directory `src/data_worker`**. `railway.json` menetapkan builder
   Dockerfile, perintah `python main.py cycle`, restart `NEVER`, dan cron
   `15 * * * *`. Bila Railway tidak membacanya, isi *Config-as-code path*
   dengan `/src/data_worker/railway.json`.
3. Variabel service Data Worker:

   | Variabel | Nilai |
   |---|---|
   | `DATABASE_URL` | Referensi `${{PostGIS.DATABASE_URL}}` (alamat internal) |
   | `SPKU_DB_SCHEMA` | `ground_truth` (bawaan, boleh dikosongkan) |
   | `SPKU_CONTACT` | Alamat kontak tim pada User-Agent (opsional) |

   Variabel MLflow dan MinIO mengikuti `.env.example`.
4. **Jangan aktifkan cron dulu** sampai langkah 2 selesai, agar lintasan pertama
   Data Worker tidak mendahului data historis (skrip migrasi menolak berjalan
   bila PostgreSQL sudah memuat lintasan yang tidak ada di SQLite).

## 2. Migrasi data historis (dijalankan di Mac)

Skrip harus dijalankan dari Mac karena `udara.sqlite` ada di sana. Pakai
**`DATABASE_PUBLIC_URL`** dari tab Variables service PostGIS; alamat
`*.railway.internal` hanya dapat dijangkau dari dalam Railway.

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
- idempoten: menjalankannya ulang hanya menambah atau memperbarui baris;
- mencatat hasilnya pada tabel `ground_truth.migration_log`.

Periksa ulang tanpa menulis: tambahkan `--verify-only`.

## 3. Peralihan

1. Aktifkan cron service Data Worker. Tunggu satu siklus (menit ke-15) dan
   periksa log: langkah `fetch_ground_truth` berstatus `ok`. Bila berstatus
   `unavailable` dengan lintasan `aborted` atau `failed`, kemungkinan portal
   memblokir IP Railway; laporkan ke tim sebelum melanjutkan.
2. Jalankan ulang perintah migrasi di atas **dengan `--force`** (salinan
   SQLite baru) untuk memindahkan data yang masuk sejak migrasi pertama.
   `--force` diperlukan karena PostgreSQL kini sudah memuat lintasan Data
   Worker; baris yang tumpang tindih memiliki kunci yang sama sehingga hanya
   diperbarui.
3. Hentikan kolektor lama di Mac:
   `launchctl unload -w ~/Library/LaunchAgents/id.udara.*.plist`.
   `udara.sqlite` dibiarkan sebagai arsip (tetap berlabel WIB).
4. Pantau kesehatan kapan saja: `railway run python main.py spku health`
   (atau `python main.py spku health` dengan `DATABASE_URL` publik).

Jangan menjalankan kolektor lama dan Data Worker ke basis data yang sama
secara bersamaan; kolektor lama menulis ke SQLite, jadi hal ini hanya terjadi
bila migrasi `--force` dijalankan berulang setelah peralihan.

## 4. Catatan penggabungan dengan cabang `dev` (30 September)

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

- Portal belum diuji dapat dijangkau dari IP Railway (langkah 3.1).
- Dokumen proyek `dataset-final-dan-akuisisi.md` menyebut pergeseran waktu
  baru terjadi sejak 27 September, sedangkan pemeriksaan di `DATA.md`
  (termasuk contoh Kelapa Gading 21 September) menunjukkan label WIB pada
  seluruh periode. Skrip migrasi mengikuti temuan kedua; selaraskan sebelum
  data dipakai untuk pelatihan.
