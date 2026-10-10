# FeatureMatrix (TI-AI-02)

Penyelarasan sumber heterogen ke grid 100 m dan time window satu jam yang sama
(Dokumen Desain Tabel 3.3, Tabel 3.8 `build_feature_matrix`, ABD-01, ABD-02).
FeatureMatrix adalah tabel **sel × fitur untuk satu time window**: Xmacro,
Xmet, Xroad, Xland, Xactivity, jarak ke sensor darat terdekat, dan penanda
ketersediaan setiap sumber. Seluruh sumber dibaca dari keluaran preprocessing
TI-AI-01 di `src/data_worker/dataset_processed`; tidak ada preprocessing ulang.
Bukti verifikasi: [`docs/experiments/TI-AI-02`](../../../docs/experiments/TI-AI-02/README.md).

## Menjalankan

Dari `src/data_worker`:

```bash
python -m feature_matrix spec          # data dictionary → feature_matrix/feature_spec.json
python -m feature_matrix build --start 2026-09-13T00:00Z --end 2026-09-30T23:00Z --cells stations --out <dir>
python -m feature_matrix build --start 2026-09-28T04:00Z --cells all --out <dir>
```

Keluaran:
- `features.parquet`;
- `audit.parquet` (data yang dipakai per time window beserta waktu tersedianya);
- `quality_report.{json,md}`;
- `manifest.json` (SHA-256 seluruh berkas masukan dan keluaran, argumen, commit).

Dari kode:

```python
from feature_matrix.build import build_feature_matrix
fm, audit = build_feature_matrix(time_windows, cells=grid_ids, sensors=stations_xy)
```

## Susunan

| Berkas | Isi |
|---|---|
| `spec.py` | Grid kanonik dan data dictionary (`FEATURES`), sumber tunggal `feature_spec.json` |
| `feature_spec.json` | Data dictionary: grup, satuan, tipe, sumber, produk, resolusi asli, metode penyelarasan, aturan ketersediaan |
| `sources.py` | Pembaca per sumber dan penyelarasan ke grid kanonik |
| `build.py` | `build_feature_matrix` |
| `quality.py` | Laporan pemeriksaan kualitas |

## Grid dan time window

- **Grid kanonik:**
  - EPSG:32748, sel 100 m, 445 × 445 sel, bbox 106,6–107,0 BT / 6,4–6,0 LS;
  - `grid_id = gy × 445 + gx` dihitung dari sisi selatan;
  - grid ini sama dengan grid sumber TI-AI-01 (lookup GEOS-CF dan Landsat), dan identik dengan
    `GridSpec.cell_index`;
  - grid TI-AI-04/05 (`JAKARTA_BBOX`, 334 × 334) termuat di dalamnya dengan kisi yang sejajar;
  - kolom `in_study_bbox` menandai sel wilayah studi.
- **Time window:** satu jam, berlabel awal jam (UTC). Waktu inferensi τ = `time_window_start` + 1 jam.

## Aturan ketersediaan (tanpa kebocoran waktu)

| Sumber | Kelompok | Aturan |
|---|---|---|
| GEOS-CF v2 `ana` | Xmacro | *As-of:* valid time terbaru dengan `available_at_utc <= τ`. Latensi publikasi 6,3–62,7 jam, sehingga nilai jam t tidak pernah tersedia pada jam t; umur dicatat di `geoscf_age_h`, dan tidak tersedia bila umur > 72 jam. Nilai PM2.5 mentah (± 4,7× SPKU, ada lompatan level April); normalisasi dilakukan pada TI-AI-03 dan hanya di-fit pada data pelatihan |
| Sentinel-5P NO2 | Xmacro | Per sel 5 km: pengamatan terbaru dengan `produced_at <= τ` (QA ≥ 0,75 sudah diterapkan preprocessing); tidak tersedia bila umur > 72 jam |
| Open-Meteo | Xmet | Nilai pada jam t; diasumsikan tersedia pada τ. Hanya satu titik (−6,2; 106,85), sehingga seragam di seluruh sel |
| OpenStreetMap | Xroad | Statis dari graf `jakarta-20261005`. Ruas dipotong pada batas sel (`spatial_model.aggregate.edge_cell_pieces`). Tidak tersedia bila simpul terdekat > 500 m (laut atau di luar DKI) |
| Landsat 8/9 | Xland | Median scene dengan `observed_at` dalam [cutoff − 90 hari, cutoff) dan `produced_at <= cutoff` |
| VIIRS VNP46A2 | Xactivity | Median harian dalam [cutoff − 90 hari, cutoff) dengan `produced_at <= cutoff`. Sel dipetakan lewat indeks tile, karena kolom `longitude`/`latitude` sumber adalah sudut barat laut sel |
| SPKU | Xsensor | Jarak ke stasiun terdekat dari himpunan `sensors` yang diberikan. Pada TI-AI-03 himpunan ini adalah stasiun non-uji per fold |

**Batas waktu fitur statis (cutoff).** Bawaannya cutoff adalah awal hari UTC dari τ, sehingga tidak pernah
melewati waktu inferensi. `static_cutoff` sesudah τ hanya diterima bila `strict_static=False` (kebijakan
snapshot TI-AI-03: komposit dibekukan pada akhir periode pelatihan), dan selalu dicatat di audit.

**Missingness.** Nilai yang tidak tersedia dibiarkan kosong (NaN), dan setiap sumber memiliki kolom
`*_available`. Tidak ada imputasi. Laporan kualitas memeriksa bahwa nilai kosong dan penanda selalu
konsisten.

## Keterbatasan

- **Geometri kanyon jalan belum ada**, karena memerlukan data bangunan.
- **Xmet berasal dari satu titik**, tanpa variasi spasial.
- **Ramalan GEOS-CF (`fcst`) belum diarsipkan**, sehingga suku latar selalu berumur ≥ 6 jam.
- **Ketersediaan Open-Meteo diasumsikan**, karena waktu publikasi Historical Forecast tidak dicatat sumber.
