# Data Worker

Komponen fase background SIPPolusi (Dokumen Desain Tugas 3, Tabel 3.9 dan
Gambar 3.7). Setiap jam pada menit ke-15, `schedule_acquisition()` menjalankan
satu siklus akuisisi untuk time window satu jam yang baru berakhir.

Yang sudah berjalan: **`fetch_ground_truth("spku", ...)`**, yaitu pengambilan
ground truth sensor darat dari portal DLH DKI Jakarta (udara.jakarta.go.id),
yang disimpan ke PostgreSQL. Langkah `trigger_downscale_inference` (fallback
IDW sampai EdgeWeight di cache) tersedia tetapi nonaktif kecuali
`DOWNSCALE_WRITE_CACHE=1`. Langkah lain pada siklus (satelit, meteorologi,
kendali mutu, penyelarasan, write_features) masih tercatat `not_implemented`.

> **Sebelum memakai datanya, baca [`docs/DATA.md`](docs/DATA.md).** Isinya:
> arti setiap tabel dan bendera `qc`, stasiun yang diketahui bermasalah (dan
> mana yang sudah atau belum tersaring bendera), koreksi zona waktu WIB ke
> UTC, serta contoh kueri.

## Susunan

| Berkas | Isi |
|---|---|
| `main.py` | Titik masuk: `cycle` (untuk cron), `serve` (proses menetap), `spku ...` |
| `acquisition.py` | `schedule_acquisition(time_window)`: langkah berurutan, status per langkah |
| `ground_truth.py` | `fetch_ground_truth(provider, bbox, time_window)` |
| `contracts.py` | `TimeWindow`, `BBox`, `GroundTruthMeasurement`, `GroundTruthBatch` (Tabel 3.3) |
| `spku/` | Kolektor SPKU, porting dari `udara-collector` (SQLite) ke PostgreSQL |
| `spku/schema.sql` | Skema `ground_truth` (PostgreSQL 16 + PostGIS) |
| `scripts/migrate_sqlite_to_postgres.py` | Pemindahan data historis dari `udara.sqlite` |
| `docs/DATA.md` | Kamus data, mutu data, dan catatan zona waktu |
| `docs/RAILWAY.md` | Langkah deployment Railway, migrasi data historis, peralihan, catatan penggabungan `dev` |
| `database/` | Model SQLAlchemy skema cache `pollution` dan `osm`; `init_db()` dipanggil di awal setiap siklus |
| `spatial_model/` | Estimasi grid sampai `EdgeWeight` (langkah `trigger_downscale_inference`): fallback IDW, confidence score, agregasi ke ruas, Spatial Pollution Cache DB; lihat [README-nya](spatial_model/README.md) |
| `feature_matrix/` | FeatureMatrix TI-AI-02: penyelarasan GEOS-CF, Sentinel-5P, Open-Meteo, OSM, Landsat, VIIRS ke grid 100 m per time window tanpa kebocoran waktu; lihat [README-nya](feature_matrix/README.md) |
| `spatial_model/baseline/` | Baseline IDW TI-AI-04: dataset beku, evaluasi *leave-one-station-out*, analisis galat; lihat [README-nya](spatial_model/baseline/README.md) |

`fetch_ground_truth("spku")` menjalankan lintasan penuh yang sama dengan
kolektor historis (daftar stasiun, lalu 119 halaman rinci dengan riwayat 48
jam), menulisnya secara idempoten ke skema `ground_truth`, lalu membaca
pengukuran PM2.5 dan NO2 pada time window yang diminta beserta koordinat
stasiun dan bendera kendali mutu. Karena setiap lintasan membawa 48 jam
riwayat, lintasan yang gagal atau dibatalkan tetap menyisakan data time
window tersebut dari lintasan sebelumnya.

Beban ke portal: 121 permintaan per jam dengan jeda satu detik (sekitar tiga
menit), naik dari 121 permintaan per enam jam pada kolektor lama. Siklus per
jam diperlukan karena time window terbaru hanya tersedia dari lintasan
terbaru.

## Menambah langkah pada siklus

Setiap langkah adalah fungsi `(time_window, config) -> dict` yang
dikembalikan dengan kunci `status` bernilai `ok`, `unavailable`, atau
keterangan lain. Daftarkan fungsinya pada `DEFAULT_STEPS` di
`acquisition.py`, menggantikan `None` pada nama langkah yang sesuai. Galat
yang tidak tertangkap dicatat sebagai `failed` tanpa menghentikan langkah
berikutnya (UT-DW-01b), jadi konektor baru tidak perlu menangkap semua
galatnya sendiri. Kontrak parameter mengikuti Tabel 3.9, batas dan fallback
mengikuti Tabel 3.19. Contoh yang sudah ada: `_step_ground_truth`.

**Keterbatasan yang perlu diselesaikan:** penjadwal saat ini belum
meneruskan keluaran satu langkah ke langkah berikutnya; nilai kembalian
hanya dipakai untuk laporan status. Tabel 3.9 mengharapkan
`run_quality_control(raw_datasets)` menerima seluruh keluaran konektor pada
siklus yang sama, jadi `schedule_acquisition` perlu diperluas (misalnya
dengan kamus konteks per siklus yang diteruskan ke setiap langkah) ketika
langkah tersebut dibuat. Sementara itu, ground truth time window berjalan
dapat dibaca dari basis data lewat `Store.read_measurements`.

## Perbedaan dengan Dokumen Desain

Untuk ditinjau pada pull request dan diselaraskan pada revisi dokumen.

| Butir | Dokumen Desain | Implementasi | Alasan |
|---|---|---|---|
| Penyimpanan ground truth mentah | Tidak didefinisikan (hanya skema `pollution` dan `osm`) | Skema `ground_truth` pada instance yang sama | Riwayat 48 jam portal harus diakumulasi; dipakai pelatihan dan IDW |
| Timeout SPKU | 30 detik (Tabel 3.19) | 45 detik, tiga kali coba | Nilai kolektor lama; portal pernah sangat lambat (lintasan 78–122 menit) |
| Data yang disimpan | PM2.5 dan NO2 (Tabel 3.18) | Enam pencemar, ISPU per jam, meteorologi stasiun | Semua ikut pada halaman yang sama; `fetch_ground_truth` tetap mengembalikan PM2.5 dan NO2 |
| Pemicu siklus | CronJob | Cron Railway `15 * * * *` (`railway.json`) | Setara; manifes `k3s/` belum memuat Data Worker |
| Akses basis data | Satu lapisan akses | `database/` (SQLAlchemy + psycopg2) untuk skema cache, `spku/store.py` (psycopg 3, SQL langsung) untuk `ground_truth` | Kolektor diporting apa adanya agar semantik idempoten dan jejak revisi tetap teruji; dapat disatukan kemudian |
| Role basis data | `worker_writer` (Tabel 3.15) | Pengguna dari `DATABASE_URL` | Role belum dibuat oleh DevOps |
| Cuplikan beranda 30 menit | Tidak ada | Tersedia (`python main.py spku snapshot`), tidak dijadwalkan | Siklus per jam sudah cukup mendeteksi gangguan |
| Kendali mutu | `run_quality_control` membuang dan mengisi | Konektor hanya menandai (`qc`), tidak membuang | Pembuangan tetap tugas `run_quality_control` |

## Zona waktu

Seluruh cap waktu di PostgreSQL adalah UTC. Label waktu pada halaman rinci
portal ternyata WIB, bukan UTC seperti disimpulkan inventarisasi 13
September, sehingga kolektor lama menyimpan observasi, ISPU per jam, dan
meteorologi tujuh jam terlalu maju. Keputusan tim: `udara.sqlite` dibiarkan
apa adanya, dan koreksi −7 jam dilakukan oleh skrip migrasi. Bukti dan
rinciannya ada pada `docs/DATA.md` dan docstring `spku/normalize.py`; uji
regresinya `test_label_rinci_adalah_wib_selaras_dengan_last_update_bertanda_z`.

Pada `docker compose` (lokal), service ini dijalankan dengan `python main.py
serve --now` (penjadwal internal), bukan `cycle`, karena `restart:
unless-stopped` akan mengulang `cycle` setiap kali proses keluar.

## Konfigurasi

Seluruhnya lewat variabel lingkungan:

| Variabel | Wajib | Keterangan |
|---|---|---|
| `DATABASE_URL` | ya | DSN PostgreSQL, misalnya `postgresql://user:pass@host:5432/sippolusi` |
| `SPKU_DB_SCHEMA` | tidak | Skema tabel ground truth; bawaan `ground_truth` |
| `SPKU_ARCHIVE_DIR` | tidak | Arsip muatan mentah; kosong berarti tidak mengarsipkan |
| `SPKU_CONTACT` | tidak | Alamat kontak pada User-Agent; boleh kosong |
| `SPKU_DELAY_SECONDS`, `SPKU_TIMEOUT_SECONDS`, `SPKU_RETRIES` | tidak | Bawaan 1, 45, 3 |
| `DOWNSCALE_WRITE_CACHE` | tidak | `1` mengaktifkan penulisan EdgeWeight ke `pollution.edge_pollution` setiap siklus; bawaan nonaktif |
| `DOWNSCALE_KEEP_WINDOWS` | tidak | Jumlah time window complete terakhir yang dipertahankan di cache; bawaan 6 (±0,55 GB, hingga ±1,1 GB sebelum autovacuum) |
| `SPATIAL_ARTIFACT_DIR` | tidak | Direktori artefak penelusuran per run inferensi; kosong berarti tidak disimpan |

Basis data harus memiliki ekstensi PostGIS. Skema dan tabel dibuat otomatis
pada koneksi pertama (`CREATE EXTENSION IF NOT EXISTS postgis`, `CREATE SCHEMA
IF NOT EXISTS ground_truth`).

## Deployment di Railway

Langkah lengkap beserta status data saat ini: [`docs/RAILWAY.md`](docs/RAILWAY.md).

1. Tambahkan service PostgreSQL dengan PostGIS (template PostGIS, bukan
   template Postgres biasa, karena skema memakai `geometry` dan indeks GiST).
2. Tambahkan service dari repositori ini dengan **Root Directory
   `src/data_worker`**. `railway.json` di folder ini menetapkan builder
   Dockerfile, perintah `python main.py cycle`, dan **cron `15 * * * *`**.
   Bila Railway tidak membaca berkas tersebut, isi Config-as-code path dengan
   `/src/data_worker/railway.json` pada pengaturan service.
3. Isi `DATABASE_URL` dengan referensi variabel service PostGIS.
4. Sebelum cron pertama berjalan, jalankan migrasi data historis (bagian
   berikut). Setelah itu kolektor lama di Mac boleh dihentikan.

Railway tidak memaksakan batas waktu pada cron job; bila satu lintasan masih
berjalan ketika jadwal berikutnya tiba, jadwal tersebut dilewati. Penulisan
juga dikunci dengan advisory lock sehingga dua lintasan tidak pernah menulis
bersamaan. Portal perlu dipastikan dapat dijangkau dari IP Railway; bila
diblokir, lintasan berstatus `aborted` dan siklus tercatat `unavailable`.
Periksa kesehatan kapan saja dengan `python main.py spku health`.

## Migrasi dari SQLite

Urutan peralihan:

1. Buat salinan konsisten dari basis data lama (aman walau kolektor lama
   masih menulis):
   `python3 -c "import sqlite3; s=sqlite3.connect('udara.sqlite'); d=sqlite3.connect('/tmp/udara-backup.sqlite'); s.backup(d); d.close()"`
2. `DATABASE_URL=... python scripts/migrate_sqlite_to_postgres.py /tmp/udara-backup.sqlite`
3. Periksa bahwa laporan verifikasi berakhir `LOLOS` (jumlah baris dan
   checksum per tabel sama, rentang observasi bergeser tepat −7 jam).
4. Aktifkan cron Data Worker, lalu hentikan kolektor lama
   (`launchctl unload -w ~/Library/LaunchAgents/id.udara.*.plist`).

Skrip idempoten selama Data Worker belum menulis, jadi langkah 1–3 boleh
diulang sebelum cron diaktifkan. Setelah itu migrasi ulang tidak diperlukan:
setiap lintasan Data Worker mengambil riwayat 48 jam, sehingga pengukuran sejak
migrasi terisi sendiri. Skrip menolak berjalan kecuali diberi `--force`, dan
`--force` jangan dipakai karena baris tabel log SQLite yang `id`-nya sudah
terpakai dilewati tanpa galat. Rinciannya, termasuk cara mengisi ulang dari
nol, ada di [`docs/RAILWAY.md`](docs/RAILWAY.md) bagian 2 dan 3.

## Pengujian

```bash
pip install -r requirements-dev.txt
TEST_DATABASE_URL=postgresql://postgres@localhost:5432/postgres pytest tests
```

Uji penyimpanan dijalankan terhadap PostgreSQL + PostGIS sungguhan (Tabel
5.10): lewat `TEST_DATABASE_URL`, atau otomatis lewat Testcontainers
(`postgis/postgis:16-3.4`) bila Docker tersedia. Setiap uji memakai skema
baru yang dihapus setelahnya. Tanpa keduanya, uji basis data dilewati dan
sisanya tetap berjalan. `tests/test_data_worker.py` memuat UT-DW-01 dan
UT-DW-05 dari Tabel 5.8; `tests/test_spatial_baseline.py` memuat UT-SDM-08
dan UT-MTP-03 untuk baseline IDW; `tests/test_spatial_edgeweight.py` memuat
UT-SDM-01, UT-SDM-06, UT-SDM-07, UT-OPS-01, dan UT-OPS-02.
