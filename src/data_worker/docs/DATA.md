# Data ground truth SPKU di PostgreSQL

Rujukan bagi siapa pun yang membaca skema `ground_truth`, terutama untuk
pembentukan dataset Spatial Downscaling Model. Ditulis 28 September 2026.

## Ringkasan yang wajib diketahui

1. **Seluruh cap waktu di PostgreSQL adalah UTC sungguhan.** Portal memberi
   label waktu WIB tanpa penanda zona; Data Worker mengonversinya. Basis data
   SQLite kolektor lama (`udara.sqlite`) **tidak** dikonversi: kolom `ts_utc`
   di sana berisi jam WIB. Jangan mencampur keduanya tanpa koreksi −7 jam.
2. **Nilai tidak pernah dibuang, hanya ditandai.** Baca kolom `qc` sebelum
   memakai nilai sebagai ground truth (lihat tabel bendera di bawah).
3. **Beberapa stasiun bermasalah secara diketahui** dan tidak seluruhnya
   tertangkap bendera `qc` (lihat "Masalah mutu yang diketahui").
4. **Satuan µg/m³** untuk keenam pencemar. Portal tidak menyatakan satuan,
   tetapi ISPU yang dipublikasikan portal sama dengan ISPU yang dihitung
   ulang dari konsentrasi dengan batas µg/m³ Permen LHK P.14/2020
   (`scripts/check_ispu_units.py`; selisih ≤ 2 poin pada 82–99% stasiun-jam
   PM2.5, PM10, NO2, SO2, CO). Konfirmasi tertulis melalui PPID tetap
   dapat dilampirkan bila tersedia.

## Tabel

| Tabel | Kunci | Isi |
|---|---|---|
| `station` | `uuid` | Metadata stasiun, `lat`/`lng`, `geom` (Point 4326, GiST), `active` |
| `station_metric` | `station_uuid, metric` | Parameter yang pernah dilaporkan tiap stasiun |
| `observation` | `station_uuid, metric, ts_utc` | **Inti dataset**: konsentrasi mentah per 30/60 menit |
| `observation_ispu` | `station_uuid, metric, ts_utc` | ISPU per jam terbitan portal (apa adanya) |
| `meteo` | `station_uuid, ts_utc` | Suhu, kelembapan, angin per stasiun (tanpa arah angin) |
| `daily_ispu` | `station_uuid, date_utc` | Agregat harian portal (lihat catatan tanggal) |
| `observation_revision` | `id` | Jejak perubahan nilai pada koordinat yang sudah tersimpan |
| `station_status` | `station_uuid, fetched_utc` | Keadaan stasiun pada setiap lintasan |
| `run`, `fetch_log` | | Satu baris per lintasan dan per permintaan HTTP (provenans) |
| `roster_event` | `id` | Stasiun muncul atau hilang dari daftar portal |
| `snapshot` | `fetched_utc` | Cuplikan beranda (JSONB), bila perintah `snapshot` dijalankan |
| `migration_log` | `id` | Catatan migrasi dari SQLite, termasuk pergeseran zona waktu |

`metric` memakai kode portal: `PM25`, `PM10`, `NO2`, `SO2`, `CO`, `O3`, serta
`AT`, `RH`, `AIR_HUMID` (meteorologi pada tiga stasiun LCS). Pada keluaran
`fetch_ground_truth()` kode ini dipetakan ke `pm25`, `no2`, dan seterusnya.

`station.initial` (mis. `DKI02`) berasal dari halaman daftar, sedangkan
`station.kode` (mis. `DKI2`) berasal dari halaman rinci. Keduanya
dipertahankan karena portal tidak konsisten. Untuk kerja spasial, pakai
koordinat, bukan nama kecamatan (metadata kecamatan kadang salah).

## Bendera kendali mutu (`qc`)

| Bendera | Arti | Diterapkan pada |
|---|---|---|
| `''` | Bersih | |
| `Z` | Nol dipakai portal sebagai penanda kosong; nilai disimpan `NULL` | suhu, kelembapan, `AT`/`RH`/`AIR_HUMID` |
| `N` | Nilai negatif | konsentrasi, angin |
| `R` | Di luar rentang fisis (mis. PM2.5 > 2000) | konsentrasi, suhu, kelembapan |
| `S` | Sensor macet: ≥ 12 nilai identik berturut-turut | konsentrasi |

Nol pada **pencemar** dianggap bacaan sah dan tidak ditandai, walau 317 baris
PM2.5 bernilai tepat 0,0 pada 13–22 September kemungkinan besar penanda
kosong. Putuskan perlakuannya pada `run_quality_control`.

## Masalah mutu yang diketahui

Dari pemeriksaan data 13–25 September 2026. Kolom terakhir menyebut apakah
filter `qc = ''` sudah menyaringnya (diperiksa pada data hasil migrasi).

| Stasiun / data | Masalah | Saran | Tersaring `qc = ''`? |
|---|---|---|---|
| DKI_PM25_40 (SDN 01 Manggarai) | PM2.5 persis 32,00 pada seluruh bacaan | Kecualikan sepenuhnya | Ya, seluruhnya `S` |
| DKI_PM25_33 (RPTRA Citra Permata) | PM2.5 macet 14,00 sejak 19 Sep 17.00 WIB | Kecualikan sejak saat itu | Hampir (420 dari 424 `S`) |
| DKI3 Jagakarsa, DKJ33 Lebak Bulus, DKI5 Kebun Jeruk | PM10 < PM2.5 (85% dari 748 pelanggaran) | Jangan pakai PM10 ketiganya | Tidak |
| DKI2 Kelapa Gading | Kode sentinel: PM2.5 999,99; angin tepat 100,0 m/s | Saring manual | **Tidak** |
| DKI_PM25_28 (SDN 10 Pondok Bambu) | Suhu macet −43,64 °C pada 19 Sep | Kecualikan baris meteo tersebut | Ya, `R` |
| `observation_ispu` untuk NO2 | Tidak berkorelasi dengan konsentrasi NO2 (ρ ≈ 0) | Jangan pakai ISPU NO2 | Tidak |
| `AIR_HUMID` dan `RH` | Nilai identik 100% | Pakai salah satu saja | Tidak |
| Delapan stasiun `DKI_PM25_*` | Rerata 4–9 µg/m³, diduga bias kalibrasi | Verifikasi terhadap stasiun `Reference` terdekat | Tidak |
| DKI14 Pulau Panggang, LCS10 Pulau Pramuka | Di Kepulauan Seribu | Di luar bbox wilayah studi | Tersaring oleh `JAKARTA_BBOX` |
| NO2 secara umum | Hanya 14 stasiun; LCS23 diam sejak 17 Sep, DKI04 sejak 27 Sep | Kepadatan spasial NO2 terbatas | Tidak relevan |

Episode anomali awal September (abu vulkanik, pembelajaran jarak jauh sejak
7 September) perlu ditandai sebelum data dipakai melatih model.

## Zona waktu: yang terjadi dan keputusannya

- Label `time` pada `rawHistory`, `ispuHistory`, dan `rawMeteoHistory` di
  halaman rinci portal adalah **WIB**. Kolektor lama (13–28 September)
  menafsirkannya sebagai UTC, sehingga seluruh cap waktu tersebut tersimpan
  tujuh jam terlalu maju di `udara.sqlite`.
- Bukti utama: nilai beranda (jamnya jelas WIB) muncul dengan nilai persis
  sama pada label halaman rinci yang bernilai jam WIB, misalnya Kelapa Gading
  21 September: 32,14, 34,6, dan 27,82 pada label 20.30, 22.00, dan 22.30.
  Secara agregat, 85,6% dari 32.642 bacaan beranda cocok hanya bila label
  dibaca sebagai WIB.
- Sampai 27 September, riwayat pada halaman rinci tertinggal sekitar tujuh
  jam dari beranda (median jeda 10,1 jam termasuk tunggu lintasan enam jam;
  sesudahnya 0,2–5,7 jam). Itulah yang membuat label WIB tampak seperti UTC
  yang segar. Penyebab di sisi portal tidak diketahui pasti. Bila perilaku
  itu kembali, time window terbaru akan kosong (`unavailable`) walau
  riwayatnya terisi belakangan.
- **Keputusan:** hanya PostgreSQL yang UTC. `udara.sqlite` dibiarkan apa
  adanya; `scripts/migrate_sqlite_to_postgres.py` menggeser −7 jam saat
  menyalin (kolom `ts_utc` pada `observation`, `observation_ispu`, `meteo`,
  `observation_revision`, dan `station_status.newest_raw_utc`). Basis data
  SQLite yang memuat tabel `tz_correction` dianggap sudah UTC dan tidak
  digeser lagi. Cap waktu buatan kolektor sendiri (`first_seen_utc`,
  `fetched_utc`, tabel `run`) selalu UTC.
- Belum pasti: apakah label menandai awal atau akhir interval 30 menit
  (beranda dan halaman rinci kadang berselisih 30 menit), dan apakah
  `daily_ispu.date_utc` (bertanda `Z` di portal) sebenarnya hari WIB.

## Contoh kueri

Rerata PM2.5 per stasiun untuk satu time window, hanya nilai bersih, di
dalam wilayah studi:

```sql
SET search_path TO ground_truth, public;

SELECT s.initial, s.lat, s.lng, AVG(o.value) AS pm25_ugm3, COUNT(*) AS n
FROM observation o
JOIN station s ON s.uuid = o.station_uuid
WHERE o.metric = 'PM25'
  AND o.ts_utc >= '2026-09-28T04:00Z' AND o.ts_utc < '2026-09-28T05:00Z'
  AND o.qc = ''
  AND ST_Intersects(s.geom, ST_MakeEnvelope(106.68, -6.38, 106.98, -6.08, 4326))
GROUP BY s.initial, s.lat, s.lng;
```

Dari Python, dengan koordinat dan bendera per pengukuran:

```python
import datetime as dt
from contracts import JAKARTA_BBOX, TimeWindow
from spku.config import Config
from spku.store import Store

window = TimeWindow.starting_at(dt.datetime(2026, 9, 28, 4, tzinfo=dt.timezone.utc))
config = Config.load()                      # membaca DATABASE_URL
with Store(config.database, config.db_schema) as store:
    rows = store.read_measurements(window.start, window.end, ["PM25", "NO2"],
                                   JAKARTA_BBOX.as_tuple())
```

`fetch_ground_truth()` mengembalikan bentuk yang sama sebagai
`GroundTruthBatch`, tetapi selalu menjalankan satu lintasan penuh ke portal
lebih dulu. Untuk membaca data historis, pakai kueri langsung seperti di atas.
