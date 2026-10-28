# Respons asli API publik (TI-SE-08)

Salinan `src/backend/public_api/examples/` (respons rute diperbarui untuk `metadata.estimation`, TI-SE-10 D-07):
respons `POST /v1/routes` dan `GET /v1/exposure-surface` pada data yang ditulis Data Worker
ke PostGIS lokal. `src/contract.test.ts` memastikan parser frontend membaca respons ini.
Salin ulang bila kontrak backend berubah.
