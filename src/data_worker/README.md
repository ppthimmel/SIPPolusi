# Data Worker

Komponen fase background SIPPolusi (Dokumen Desain Tugas 3, Tabel 3.9 dan
Gambar 3.7). Setiap jam pada menit ke-15, `schedule_acquisition()` menjalankan
satu siklus akuisisi untuk time window satu jam yang baru berakhir.

Yang sudah berjalan:

- **`fetch_ground_truth("spku", ...)`:** ground truth sensor darat dari portal DLH DKI Jakarta
  (udara.jakarta.go.id), disimpan ke PostgreSQL.
- **Konektor sumber sekunder dan FeatureMatrix (TI-SE-02):** Open-Meteo, GEOS-CF `ana`/`fcst`,
  Landsat 8/9, VIIRS VNP46A2, dan Sentinel-5P NO2, lalu kendali mutu, penyelarasan ke grid 100 m, dan
  `write_features`. Data disimpan ke skema `features`; lihat [`feature_store/README.md`](feature_store/README.md).
- **`trigger_downscale_inference`:** fallback IDW sampai EdgeWeight di cache; tersedia tetapi nonaktif kecuali
  `DOWNSCALE_WRITE_CACHE=1`.

> **Sebelum memakai datanya, baca [`docs/DATA.md`](docs/DATA.md).** Isinya:
> arti setiap tabel dan bendera `qc`, stasiun yang diketahui bermasalah (dan
> mana yang sudah atau belum tersaring bendera), koreksi zona waktu WIB ke
> UTC, serta contoh kueri.

## Susunan

| Berkas | Isi |
|---|---|
| `main.py` | Titik masuk: `cycle` (untuk cron), `serve` (proses menetap), `status` (pemeriksaan jadwal), `check`, `spku ...` |
| `acquisition.py` | `schedule_acquisition(time_window)`: langkah berurutan, status per langkah |
| `worker_runs.py` | Catatan eksekusi siklus (`worker.cycle_run`), kunci siklus, status jadwal; lihat [`docs/devops/data-worker-schedule.md`](../../docs/devops/data-worker-schedule.md) |
| `connectors/` | Konektor sumber sekunder (Open-Meteo, GEOS-CF, Landsat, VIIRS, Sentinel-5P): HTTP dengan timeout dan coba ulang, parser, QA |
| `feature_store/` | Skema `features`, pembaca FeatureMatrix PostgreSQL, langkah siklus TI-SE-02, `python main.py features ...`; lihat [README-nya](feature_store/README.md) |
| `ground_truth.py` | `fetch_ground_truth(provider, bbox, time_window)` |
| `contracts.py` | `TimeWindow`, `BBox`, `GroundTruthMeasurement`, `GroundTruthBatch` (Tabel 3.3) |
| `spku/` | Kolektor SPKU, porting dari `udara-collector` (SQLite) ke PostgreSQL |
| `spku/schema.sql` | Skema `ground_truth` (PostgreSQL 16 + PostGIS) |
| `scripts/migrate_sqlite_to_postgres.py` | Pemindahan data historis dari `udara.sqlite` |
| `scripts/spku_stub_portal.py` | Portal SPKU tiruan (mode `ok`, `flaky`, `down`, `empty`) untuk uji jalur gagal lewat `SPKU_BASE_URL` |
| `docs/DATA.md` | Kamus data, mutu data, dan catatan zona waktu |
| `docs/RAILWAY.md` | Langkah deployment Railway, migrasi data historis, peralihan, catatan penggabungan `dev` |
| `database/` | Model SQLAlchemy skema cache `pollution` dan `osm`; `init_db()` dipanggil di awal setiap siklus |
| `spatial_model/` | Estimasi grid sampai `EdgeWeight` (langkah `trigger_downscale_inference`): fallback IDW, confidence score, agregasi ke ruas, Spatial Pollution Cache DB; lihat [README-nya](spatial_model/README.md) |
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
keterangan lain. Langkah yang diberi penanda `feature_store.steps.uses_context`
dipanggil `(time_window, config, context)`: `context` adalah kamus per siklus
untuk meneruskan keluaran ke langkah berikutnya (mis. FeatureMatrix dari
`align_spatiotemporal` ke `write_features`). Daftarkan fungsinya pada `DEFAULT_STEPS` di
`acquisition.py`, menggantikan `None` pada nama langkah yang sesuai. Galat
yang tidak tertangkap dicatat sebagai `failed` tanpa menghentikan langkah
berikutnya (UT-DW-01b), jadi konektor baru tidak perlu menangkap semua
galatnya sendiri. Kontrak parameter mengikuti Tabel 3.9, batas dan fallback
mengikuti Tabel 3.19. Contoh yang sudah ada: `_step_ground_truth`.

## Perbedaan dengan Dokumen Desain

Untuk ditinjau pada pull request dan diselaraskan pada revisi dokumen.

| Butir | Dokumen Desain | Implementasi | Alasan |
|---|---|---|---|
| Penyimpanan ground truth mentah | Tidak didefinisikan (hanya skema `pollution` dan `osm`) | Skema `ground_truth` pada instance yang sama | Riwayat 48 jam portal harus diakumulasi; dipakai pelatihan dan IDW |
| Timeout SPKU | 30 detik (Tabel 3.19) | 45 detik, tiga kali coba | Nilai kolektor lama; portal pernah sangat lambat (lintasan 78–122 menit) |
| Data yang disimpan | PM2.5 dan NO2 (Tabel 3.18) | Enam pencemar, ISPU per jam, meteorologi stasiun | Semua ikut pada halaman yang sama; `fetch_ground_truth` tetap mengembalikan PM2.5 dan NO2 |
| Pemicu siklus | CronJob | Cron Railway `15 * * * *` (`railway.json`) | Setara; deployment memakai Railway, bukan K3s |
| Akses basis data | Satu lapisan akses | `database/` (SQLAlchemy + psycopg2) untuk skema cache, `spku/store.py` (psycopg 3, SQL langsung) untuk `ground_truth` | Kolektor diporting apa adanya agar semantik idempoten dan jejak revisi tetap teruji; dapat disatukan kemudian |
| Role basis data | `worker_writer` (Tabel 3.15) | Pengguna dari `DATABASE_URL` | Role belum dibuat oleh DevOps |
| Cuplikan beranda 30 menit | Tidak ada | Tersedia (`python main.py spku snapshot`), tidak dijadwalkan | Siklus per jam sudah cukup mendeteksi gangguan |
| Kendali mutu | `run_quality_control` membuang dan mengisi | Konektor SPKU hanya menandai (`qc`). Konektor sumber sekunder membuang nilai di luar rentang fisik dan duplikat saat transformasi; `run_quality_control` menandai sumber yang hilang atau basi `available=false`. Missing data SPKU tidak diinterpolasi | Data latih TI-AI-02 tidak memakai imputasi; nilai hilang tetap NaN dengan penandanya |
| Sentinel-5P, Landsat | Copernicus Data Space Ecosystem; Earth Engine atau EarthExplorer (Tabel 3.18) | Microsoft Planetary Computer (STAC, token SAS anonim) | Data latih berasal dari sumber ini; tanpa akun |
| Feature dan Dataset Store | Parquet + `manifest.json` di object storage setiap jam (Tabel 3.11) | Manifest dan `feature_version` di `features.feature_window`; Parquet utuh bila `FEATURE_STORE_DIR` diatur | Matriks ±196 ribu baris per jam dapat dibangun ulang persis dari tabel sumber |

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
| `SPKU_BASE_URL` | tidak | Alamat portal; diganti hanya untuk uji terhadap portal tiruan |
| `WORKER_DB_SCHEMA` | tidak | Skema catatan eksekusi siklus (`cycle_run`); bawaan `worker` |
| `WORKER_LOCK_WAIT_SECONDS` | tidak | Lama menunggu kunci siklus bila siklus lain masih berjalan, sebelum dicatat `skipped`; bawaan 600 |
| `EARTHDATA_TOKEN` | tidak | Token Earthdata Login untuk VIIRS VNP46A2 (LAADS DAAC); kosong berarti VIIRS `disabled` |
| `FEATURES_SOURCES` | tidak | Sumber sekunder yang aktif: `all` (bawaan), `none`, atau daftar dipisah koma |
| `FEATURES_DB_SCHEMA` | tidak | Skema data sumber sekunder dan manifest FeatureMatrix; bawaan `features` |
| `FEATURE_STORE_DIR` | tidak | Bila diatur, Parquet dan `manifest.json` FeatureMatrix per time window ditulis ke sini |
| `DOWNSCALE_WRITE_CACHE` | tidak | `1` mengaktifkan penulisan EdgeWeight ke `pollution.edge_pollution` setiap siklus; bawaan nonaktif |
| `DOWNSCALE_KEEP_WINDOWS` | tidak | Jumlah time window complete terakhir yang dipertahankan di cache; bawaan 6 (±0,55 GB, hingga ±1,1 GB sebelum autovacuum) |
| `SPATIAL_ARTIFACT_DIR` | tidak | Direktori artefak penelusuran per run inferensi; kosong berarti tidak disimpan |
| `LOG_LEVEL` | tidak | Bawaan `INFO` |
| `LOG_FORMAT` | tidak | `json` (bawaan di Railway, agar level log terbaca) atau `text` (bawaan lokal) |

Basis data harus memiliki ekstensi PostGIS. Skema dan tabel dibuat otomatis
pada koneksi pertama (`CREATE EXTENSION IF NOT EXISTS postgis`, `CREATE SCHEMA
IF NOT EXISTS ground_truth`).

## Deployment di Railway

Langkah lengkap beserta status data saat ini: [`docs/RAILWAY.md`](docs/RAILWAY.md).

1. Tambahkan service PostgreSQL dengan PostGIS (template PostGIS, bukan
   template Postgres biasa, karena skema memakai `geometry` dan indeks GiST).
2. Tambahkan service dari repositori ini dengan **Root Directory
   `src/data_worker`**. `railway.json` di folder ini menetapkan builder
   Dockerfile, restart `NEVER`, dan **cron `15 * * * *`**; perintahnya
   `python main.py cycle` dari `CMD` Dockerfile.
   Bila Railway tidak membaca berkas tersebut, isi Config-as-code path dengan
   `/src/data_worker/railway.json` pada pengaturan service.
3. Isi `DATABASE_URL` dengan referensi variabel service PostGIS.
4. Sebelum cron pertama berjalan, jalankan migrasi data historis (bagian
   berikut). Setelah itu kolektor lama di Mac boleh dihentikan.

Railway tidak memaksakan batas waktu pada cron job; bila satu lintasan masih
berjalan ketika jadwal berikutnya tiba, jadwal tersebut dilewati. Setiap
siklus juga memegang kunci siklus (advisory lock) sehingga siklus manual dan
terjadwal tidak pernah tumpang tindih, dan penulisan SPKU dikunci terpisah.
Portal perlu dipastikan dapat dijangkau dari IP Railway; bila diblokir,
lintasan berstatus `aborted`, langkah `fetch_ground_truth` tercatat `failed`,
dan siklus keluar dengan kode 1, walaupun time window masih berisi data dari
lintasan sebelumnya.

Setiap siklus dicatat pada tabel `worker.cycle_run` (identitas run,
`TimeWindow`, pemicu, waktu terjadwal, status per langkah, alasan kegagalan).
`python main.py status` membandingkan catatan itu dengan jadwal dan
membedakan siklus yang berhasil, terlambat, gagal, macet, atau tidak pernah
dijalankan. Kesehatan data SPKU: `python main.py spku health`. Rincian:
[`docs/devops/data-worker-schedule.md`](../../docs/devops/data-worker-schedule.md).

Sebelum mengaktifkan cron atau saat siklus gagal, jalankan `python main.py
check`: konfigurasi, impor modul, serta koneksi dan ketersediaan PostGIS,
tanpa menulis apa pun. `cycle` dan `serve` menjalankan pemeriksaan konfigurasi
yang sama dan berhenti dengan kode keluar 2 sebelum menyentuh portal bila
gagal. Kode keluar dan contoh pesan: [`docs/devops/build-runtime.md`](../../docs/devops/build-runtime.md).

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
UT-SDM-01, UT-SDM-06, UT-SDM-07, UT-OPS-01, dan UT-OPS-02;
`tests/test_connectors.py` dan `tests/test_feature_store.py` memuat uji konektor TI-SE-02 dengan respons
rekaman (`tests/fixtures/sources`) dan uji penyimpanan `features`. Uji tidak menghubungi sumber sekunder
sungguhan (`FEATURES_SOURCES=none` pada `conftest.py`).
