# Mendaftarkan model ke registry Railway

Prosedurnya kini ada di
[`src/model_registry/README.md`](../../src/model_registry/README.md#mendaftarkan-model-dari-bucket-railway-ti-do-10b):
*job* Railway `model-register` menjalankan `python -m spatial_model.training register-from-bucket` di dalam jaringan
privat, membaca paket lewat *artifact proxy* registry, tanpa kunci S3 dan tanpa menempelkan *script* (TI-DO-10b, #91).

## Catatan: prosedur lama (3–9 November 2026)

Sebelum TI-DO-10b, `scripts/railway/register_from_bucket.py` disalin sebagai base64 dan ditempel ke Console
`model_registry`. Skrip itu memegang kunci S3 milik registry untuk mengunduh paket dari *bucket* (di luar `artifacts/`,
misalnya `packaged/route/<versi>/`), lalu melepas kunci itu sebelum mendaftar lewat *proxy*. Dengan cara itu terdaftar
`morl-route-policy` 1 dan 2, `spatial-downscaling-pm25` 1, dan `spatial-downscaling-no2` 1
([bukti](../../docs/experiments/TI-AI-11/railway_registry_evidence.md),
[scalarized](../../docs/experiments/TI-AI-11/scalarized-serving.md)). Skrip itu dihapus; logika validasinya kini
satu dengan perintah di atas, yang juga dipakai job lokal `register_models`. Versi lamanya ada di riwayat git.

## Lihat juga

- [`src/model_registry/README.md`](../../src/model_registry/README.md): layanan registry dan pendaftaran
- [`docs/devops/verification-TI-DO-10b-register-from-bucket.md`](../../docs/devops/verification-TI-DO-10b-register-from-bucket.md): bukti TI-DO-10b
- [`docs/devops/local-railway-parity.md`](../../docs/devops/local-railway-parity.md): padanan lokal: job `register_models`
