# Mendaftarkan model ke registry Railway (manual, sementara)

Registry MLflow di Railway hanya dapat dijangkau lewat *private networking* (tanpa domain publik dan tanpa
autentikasi), sehingga `python -m spatial_model.training register` tidak dapat dijalankan dari laptop atau Colab.
Prosedur ini mengunggah paket ke *bucket* lalu menjalankan `register_from_bucket.py` di Console `model_registry`.
Otomatisasinya dilacak di #91.

## Langkah

1. **Buat paket dan periksa secara lokal.** Bundel spasial dari `export_bundle` mendapat `package.json` lewat
   `python -m spatial_model.training package <bundle>`. Paket model rute dibuat dengan
   `route_model.morl.packaging.package_policy_set`. Lalu jalankan
   `python scripts/verify_model_packages.py spatial --bundle <bundle> --target pm25` (atau `route`).
   Muat model spasial membutuhkan Python 3.12 dan `torch-geometric`.
2. **Unggah foldernya ke *bucket*** `minio` (environment *development*), misalnya ke
   `packaged/spatial/pm25/<model_version>/` atau `packaged/route/<model_version>/`. Cara termudah: tab **Files**
   pada *bucket*. Jangan menempelkan kunci S3 di *chat* atau repositori. Unggah folder apa adanya.
3. **Bangunkan registry** (ia *sleeping* bila tidak ada lalu lintas; Console dan SSH tidak membangunkannya). Di Console
   layanan `cache-api`:
   `curl -m 90 -sS -o /dev/null -w "%{http_code}\n" http://modelregistry.railway.internal:5000/health`
   (jawaban `200`, bisa butuh 10–30 detik).
4. **Buat perintah satu baris** dari mesin Anda:
   ```
   base64 -w0 scripts/railway/register_from_bucket.py
   ```
   lalu di Console `model_registry` tempel (ganti `<base64>`):
   ```
   REGISTER_PREFIX=packaged/spatial/pm25/<model_version>/ env GIT_PYTHON_REFRESH=quiet python -c "import base64;exec(base64.b64decode('<base64>'))"
   ```
5. **Hasil yang diharapkan:** `downloaded N files`, `verified: ...`, `registered: <nama> version <n> aliases candidate`,
   dan `RESOLVED ... RESULT: PASS`.

## Catatan

- Alias bawaan hanya `candidate`. Alias `production` adalah keputusan tim; tambahkan
  `REGISTER_ALIASES=candidate,production` di depan perintah hanya bila memang diputuskan.
- Paket yang sama (hash `package.json` sama) tidak didaftarkan dua kali. Hash yang tidak cocok, berkas yang tidak
  terdaftar, atau fold LOSO menghentikan proses sebelum ada versi yang dibuat.
- Skrip melepas kredensial S3 miliknya sebelum mendaftar, jadi pendaftaran melewati *artifact proxy* registry.
- Model yang dikenali: `spatial-downscaling-pm25`, `spatial-downscaling-no2`, dan `morl-route-policy`.
- Registry tidak memakai autentikasi. Jangan beri domain publik.
- Pengujian lokal skrip ini (tanpa *bucket*): `REGISTER_LOCAL_DIR=<folder paket>` dengan `MLFLOW_TRACKING_URI` ke
  penyimpanan berkas lokal dan `MLFLOW_ALLOW_FILE_STORE=true`.
