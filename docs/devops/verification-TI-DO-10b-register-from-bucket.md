# Bukti verifikasi TI-DO-10b

Prosedur yang diverifikasi: [pendaftaran model dari *bucket*](../../src/model_registry/README.md#mendaftarkan-model-dari-bucket-railway-ti-do-10b)
(`python -m spatial_model.training register-from-bucket` / `railway-job`, #91).
Basis: cabang `TI-DO-10b` dari `dev` @ `57542c7`, 11 November 2026.

**Lingkungan lokal** (setara lapisan registry di Railway, tanpa Railway):

- kontainer `python:3.11-slim` (Linux, seperti image Data Worker dan CI) dengan `mlflow==2.17.2`,
  `sqlalchemy<2.1`, `boto3`, PyTorch 2.15 CPU (hanya untuk mengemas set rute);
- server MLflow dengan `--serve-artifacts --default-artifact-root mlflow-artifacts:/
  --artifacts-destination s3://e2ebucket/artifacts`, *backend* SQLite. Hanya proses server yang memegang kunci S3;
- MinIO (`bitnamilegacy/minio`) sebagai *bucket*, dengan kredensial sekali pakai;
- unggahan meniru tab **Files** Railway, termasuk objek 0 bita `folder/` untuk setiap folder.

Klien (`railway-job`) dijalankan hanya dengan `MLFLOW_TRACKING_URI` dan `REGISTER_PREFIX`/`PROMOTE`; jumlah variabel
`AWS_*` di lingkungannya 0.

## 1. Uji otomatis

`src/data_worker/tests/test_register_from_bucket.py`, terhadap server MLflow sungguhan dengan `--serve-artifacts`
(folder lokal sebagai tujuan artefak): **18 lulus**. `tests/test_model_package.py`: 9 lulus.

| Kriteria | Uji |
|---|---|
| Paket rute dan bundel spasial terdaftar, tag `package_sha256` / `bundle_sha256` benar, hanya alias `candidate` | `test_route_package_registers_once_with_candidate_only`, `test_spatial_bundle_is_packaged_registered_and_not_duplicated` |
| Dijalankan dua kali: satu versi | kedua uji di atas |
| Hash berubah, berkas tidak terdaftar, `package.json` hilang: tidak ada versi | `test_damaged_route_package_registers_nothing` (3 kasus) |
| Kredensial pada paket, fold LOSO, awalan kosong: ditolak | `test_credentials_and_folds_are_refused` |
| Gagal setelah versi dibuat: versi dihapus | `test_failure_after_the_version_exists_rolls_it_back` |
| Variabel S3 dibuang dari klien | `test_client_drops_object_storage_keys` |
| Promosi hanya untuk `candidate` saat ini; `PROMOTE` yang tertinggal tidak memindahkan `production` | `test_promote_takes_only_the_current_candidate` |
| Awalan tidak sah, *placeholder* folder, instruksi *job* | `test_bad_prefixes`, `test_folder_placeholders_from_the_files_tab_are_skipped`, `test_railway_job_needs_exactly_one_instruction` |

Uji ujung-ke-ujung (bagian 2) menemukan satu cacat yang tidak tertangkap uji dengan folder lokal: lewat *proxy*,
MLflow tidak mengirim `file_size` untuk objek 0 bita, sehingga *placeholder* folder terbaca `None`, bukan `0`, lalu
diunduh dan gagal (HTTP 500). Diperbaiki, dan uji *placeholder* kini memakai `None`.

## 2. Ujung-ke-ujung lewat MinIO

### Bundel PM2.5

Bundel ST-GNN PM2.5 **sintetis** (bobot acak 200 KB; tidak ada bundel PM2.5 asli di mesin ini), tanpa `package.json`,
diunggah ke `artifacts/incoming/spatial/pm25/e2e-synthetic/`.

```
$ REGISTER_PREFIX=incoming/spatial/pm25/e2e-synthetic/ python -m spatial_model.training railway-job
waiting for the registry at http://localhost:5001 (up to 120 s)
downloaded 4 files from mlflow-artifacts:/incoming/spatial/pm25/e2e-synthetic
verified: spatial-downscaling-pm25 ds-v0.3.0-8487e52c6e8c bundle_sha256 8487e52c6e8c9fbc... package_sha256 d5a155416f75e107... 4 files + package.json (package.json written here)
registered: spatial-downscaling-pm25 version 1 aliases candidate
RESOLVED spatial-downscaling-pm25:1: all 4 files + package.json match.  RESULT: PASS
{"status": "registered", "name": "spatial-downscaling-pm25", "version": "1", "run_id": "2b95658d...", "aliases": ["candidate"], "model_version": "ds-v0.3.0-8487e52c6e8c", "bundle_sha256": "8487e52c6e8c9fbc1b761da2a5eb7e0d09127a01ffab3f2bc4070ff1dd273d92", "package_sha256": "d5a155416f75e1073e82d4255394caacef7341c7be2e0917a7eca0aee7bf8295"}

$ # dijalankan lagi
verified: spatial-downscaling-pm25 ds-v0.3.0-8487e52c6e8c bundle_sha256 8487e52c6e8c9fbc... package_sha256 eedf60e7295373e1... 4 files + package.json (package.json written here)
already registered: spatial-downscaling-pm25 version 1 holds this package; nothing registered
{"status": "already_registered", "name": "spatial-downscaling-pm25", "version": "1", "aliases": ["candidate"], "bundle_sha256": "8487e52c...", "package_sha256": "<hash versi 1>"}
```

Pada percobaan kedua `package.json` yang ditulis ulang punya hash lain (`created_at` berbeda); versi lama dikenali
dari `bundle_sha256`, dan keluaran JSON melaporkan hash milik versi yang sudah terdaftar.

### Paket rute

Set TI-AI-09 yang ada di repositori (`docs/experiments/TI-AI-09/set`), dikemas dengan `scripts/local/package_route.py`
(23 berkas + `package.json`), diunggah ke `artifacts/incoming/route/20261102T145533Z-multi-policy-bike-v1-201e7d41/`.

```
$ REGISTER_PREFIX=incoming/route/20261102T145533Z-multi-policy-bike-v1-201e7d41/ python -m spatial_model.training railway-job
downloaded 24 files from mlflow-artifacts:/incoming/route/20261102T145533Z-multi-policy-bike-v1-201e7d41
verified: morl-route-policy 20261102T145533Z-multi-policy-bike-v1-201e7d41 package_sha256 7774d98c5939af17... 23 files + package.json
registered: morl-route-policy version 1 aliases candidate
RESOLVED morl-route-policy:1: all 23 files + package.json match.  RESULT: PASS
{"status": "registered", "name": "morl-route-policy", "version": "1", "run_id": "58cbc2cd...", "aliases": ["candidate"], "model_version": "20261102T145533Z-multi-policy-bike-v1-201e7d41", "package_sha256": "7774d98c5939af17c425960378dee513116f4454b4f4f918989496e8331b38d0"}

$ # dijalankan lagi
already registered: morl-route-policy version 1 holds this package; nothing registered
```

`downloaded 24 files` menghitung `package.json`; *placeholder* folder `policies/` dan folder awalan dilewati.

`package_sha256` berbeda dari versi 1 di Railway (`4ad8f8a9...`, 3 November) karena paket ini dibuat dengan kode
`dev` saat ini; isinya set yang sama.

### Paket rusak di *bucket*

| Paket yang diunggah | Keluaran | Kode keluar | Versi baru |
|---|---|---|---|
| paket rute, `evaluation.json` diubah | `FAILED: Package refused: Package file changed: evaluation.json` | 1 | tidak ada |
| paket rute + `notes.txt` | `FAILED: Package refused: Package holds files its manifest does not list: notes.txt` | 1 | tidak ada |
| bundel PM2.5 + `config.json` berisi `aws_secret_access_key` | `FAILED: Package refused: Package would carry credentials or sensitive files: config.json: secret_assignment` | 1 | tidak ada |

### Promosi

```
$ PROMOTE=route:1 python -m spatial_model.training railway-job
promoting morl-route-policy version 1 (production was None)
promoted: morl-route-policy:1 is production

$ PROMOTE=route:1 ...            # variabel tertinggal, deploy ulang
morl-route-policy version 1 is already production; nothing changed

$ PROMOTE=route:1 REGISTER_PREFIX=x/ ...
railway-job: FAILED: REGISTER_PREFIX and PROMOTE are both set; set one per run      (kode keluar 1)
```

### Daftar versi di registry

Setelah semua langkah di atas (setiap paket dua kali, tiga paket rusak, promosi):

```
morl-route-policy aliases {'candidate': '1', 'production': '1'}
  version 1 {'package_sha256': '7774d98c5939af17...'} mlflow-artifacts:/2/91c1c476.../artifacts/package
spatial-downscaling-pm25 aliases {'candidate': '1'}
  version 1 {'bundle_sha256': '8487e52c6e8c9fbc...', 'package_sha256': 'e7ec0def0d596ee3...'} mlflow-artifacts:/1/8556a58c.../artifacts/bundle
```

Satu versi per model. Sumber versi berada di `mlflow-artifacts:/`, yaitu diunggah lewat *proxy*. Transkrip
pendaftaran di atas berasal dari registry kedua yang masih kosong (*port* 5001, *bucket* yang sama), dijalankan
setelah teks keluaran dirapikan; hasilnya sama: satu versi per model, alias `candidate`.

## 3. Belum dicakup

- **Railway *development*.** Service `model-register` belum dibuat dan perintah belum dijalankan di Railway; bukti
  di atas adalah lokal. Yang perlu dicatat setelah dijalankan: log deploy untuk bundel PM2.5 asli dan paket rute,
  serta daftar versi registry.
- **Job lokal `register_models`** (docker compose) masih memakai `scripts/railway/register_from_bucket.py`; logika
  validasinya belum disatukan dengan perintah ini.
- **Rollback `production`** ke versi yang bukan `candidate` tidak dilayani `railway-job`.
- **Langkah CI opsional** (`scripts/verify_model_packages.py` pada setiap perubahan) belum ditambahkan.
