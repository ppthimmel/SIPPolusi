# Stack lokal yang setara dengan Railway development

`docker-compose.yml` dibuat agar berperilaku seperti Railway *development* (proyek `humble-endurance`) pada
hal-hal yang memengaruhi jalur data dan model. Dokumen ini mencatat apa yang sudah sama, apa yang sengaja
berbeda, dan cara menyiapkan stack baru sampai rute MORL keluar dari *gateway*.

## Yang sama dengan Railway

| Aspek | Railway development | Lokal |
|---|---|---|
| Basis data | `postgis/postgis:18-3.6`, `PGDATA=/var/lib/postgresql/data/pgdata` | sama (volume `postgres18_data`) |
| Registry | MLflow dengan *artifact proxy* (`MLFLOW_ARTIFACT_ROOT=mlflow-artifacts:/`, `MLFLOW_ARTIFACTS_DESTINATION`) | sama; hanya `model_registry` memegang kunci *object storage* |
| Klien registry | Data Worker dan Cache API hanya punya `MLFLOW_TRACKING_URI` | sama (kunci S3 dihapus dari contoh env) |
| Model rute | Cache API `ROUTE_SOLVER_PROVIDER=morl`, anggaran 40.000 ms, *fallback* 20.000 ms, *gateway* 43.000 ms | sama (`src/backend/osm/.env.example`) |
| Data Worker | fitur dari *feature store* langsung, `DOWNSCALE_WRITE_CACHE=1`, simpan 2 window, 4 *thread* | sama (`src/data_worker/.env.example`) |
| *Gateway* dan Frontend | `GATEWAY_UPSTREAM_TIMEOUT_S=43`, *timeout* Frontend 45.000 ms (saat *build*) | sama |
| Pencarian lokasi | `API_GEOCODING_URL=https://photon.komoot.io` | sama (boleh dikosongkan agar teks pencarian tidak keluar) |
| Graf jalan | `jakarta-20261005`, `edge_id` 1–373.352 | sama, diisi `seed_graph` dengan `edge_id` yang sama |

## Yang sengaja berbeda

- **Versi kontrak paket model rute.** Paket lokal dibangun dari kode saat ini dengan `CONTRACT_VERSION` 1.1.0, sehingga
  dimuat sebagai `compatible`. `morl-route-policy:1` di Railway dibangun untuk 1.0.0 dan dimuat sebagai `flagged`
  (versi minor berbeda, tetap dipakai). Isi *policy*-nya sama.
- ***Object storage*.** Lokal memakai kontainer `minio` (URL *path-style*, *bucket* `mlflow`). Railway memakai
  Storage Bucket (`https://t3.storageapi.dev`, URL *virtual-host*, nama *bucket* buatan Railway). Karena klien hanya
  bicara dengan registry, perbedaan ini tidak terlihat oleh layanan lain.
- **Pemicu Data Worker.** Railway menjalankan `python main.py cycle` dari *cron* `15 * * * *`. Lokal memakai
  `python main.py serve --now` (penjadwal di dalam proses, jadwal yang sama), karena `restart: unless-stopped` akan
  mengulang siklus terus-menerus.
- **Jaringan.** Railway memakai IPv6 dan nama `*.railway.internal`; lokal IPv4 dan nama layanan compose. Layanan
  yang hanya mendengarkan IPv4 akan berjalan di lokal tetapi tidak di Railway.
- ***Observability*.** Railway punya satu layanan `observability`; lokal `prometheus`, `pushgateway`, dan `grafana`
  terpisah.
- **Layanan `backend`.** Lokal tetap menjalankan kontainer `backend`. Di Railway layanan itu tidak punya *deployment*
  yang berjalan, dan *gateway* hanya meneruskan ke Cache API. Status ini akan dibahas dengan tim sebelum diubah.
- **Data.** Basis data lokal mulai kosong. Graf jalan diisi `seed_graph`; data stasiun diambil siklus Data Worker
  pertama dari portal SPKU (butuh internet). Data sumber sekunder (satelit, cuaca) juga butuh internet, dan VIIRS
  butuh `EARTHDATA_TOKEN`.
- **Model spasial.** Bundel PM2.5 dan NO2 tidak ada di git. Tanpa bundel, Data Worker memakai IDW per polutan dan
  mencatat alasannya, sama seperti di Railway bila model tidak bisa dimuat.

## Menyiapkan stack baru

1. **Env.** Salin setiap `.env.example` menjadi `.env` di foldernya, termasuk yang baru
   `src/backend/osm/.env.example` (Cache API). Ganti `change-me` dengan nilai yang sama di semua berkas
   (`src/postgres`, `src/minio`, `src/model_registry`, dan `DATABASE_URL` di layanan lain).
2. **Mulai stack.** `docker compose up -d --build`. Volume basis data baru bernama `postgres18_data`; volume lama
   `postgres_data` (PostgreSQL 16) tidak dipakai dan tidak dihapus.
3. **Graf jalan.** `docker compose --profile tools run --rm seed_graph`. Menulis schema dan graf `jakarta-20261005`
   lewat `osm_cache.write_road_graph` (sekitar beberapa menit). Aman dijalankan ulang.
4. **Model.** `docker compose --profile tools run --rm register_models`. Membangun paket model rute dari set TI-AI-09
   yang di-commit (`package_route`), lalu mendaftarkannya dan setiap folder di `./.local-models/` dengan alias
   `candidate` dan `production`, lewat *artifact proxy*. Paket yang sudah terdaftar dilewati.
5. **Model spasial (opsional).** Taruh bundel di `./.local-models/<nama>/` (misalnya diunduh dari *bucket* Railway,
   `packaged/spatial/pm25/...` sudah berisi `package.json`). Bundel tanpa `package.json` dipaketkan dulu:
   `docker compose run --rm -v ./.local-models:/models data_worker python -m spatial_model.training package /models/<nama>`,
   lalu jalankan langkah 4 lagi.
6. **Tunggu Cache API memuat *policy* (tanpa *restart*).** Cache API yang mulai sebelum langkah 4 mendapat HTTP 400
   untuk alias `production` dan mencatat `route_model_load outcome=waiting ... reason=not_registered`. Selama itu
   rute memakai fallback A* (`model_unavailable`). Cache API memeriksa registry lagi setiap
   `ROUTE_SOLVER_MODEL_RECHECK_S` (60 detik di `docker-compose.yml`), jadi paling lambat semenit setelah
   `register_models` log berisi `route_model_load outcome=ok model=morl-route-policy:1 ...`. Cek dengan
   `docker compose logs cache_api | grep route_model_load`. `docker compose restart cache_api` hanya opsional, untuk
   memuat lebih cepat. Di Railway berlaku hal yang sama dengan jeda bawaan 900 detik, misalnya bila registry
   di-*reset* atau alias dihapus lalu dibuat lagi. Kesalahan lain (`action=stop`) tetap perlu perbaikan di registry
   lalu *restart*.
7. **Isi *cache* polusi.** Tunggu siklus pertama Data Worker selesai (`serve --now` menjalankannya saat mulai), atau
   jalankan satu siklus: `docker compose run --rm data_worker python main.py cycle`. Sebelum ada time window
   berstatus `complete`, `POST /v1/routes` menjawab 503 `cache_unavailable` (`cache_empty`).
8. **Cek.** `curl http://localhost:8081/healthz`, lalu minta rute lewat Frontend (`http://localhost:8080`) atau
   `POST http://localhost:8081/v1/routes`. Rute dari MORL bertanda `route_source` model dan versi `morl-route-policy`;
   bila model tidak termuat, Cache API memakai *fallback* A* dan mencatat alasannya.

Untuk meniru *private networking* Railway, tambahkan `-f docker-compose.private.yml`
([private-networking.md](private-networking.md)). Alat pada langkah 3–5 tidak membutuhkan *port* host.

## Lihat juga

- [`scripts/railway/README.md`](../../scripts/railway/README.md): prosedur pendaftaran model di Railway
- [`src/model_registry/README.md`](../../src/model_registry/README.md): registry model
- [`docs/devops/build-runtime.md`](build-runtime.md): build, health check, dan logging per layanan
- [`docs/software/technical-runbook.md`](../software/technical-runbook.md): runbook operasional
- [`src/backend/osm/README.md`](../../src/backend/osm/README.md): Cache API
- [`src/backend/solver/README.md`](../../src/backend/solver/README.md#penyedia-morl-ti-se-07b): penyedia `morl` yang memuat policy dari registry
