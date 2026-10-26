# Uji integrasi end-to-end (TI-SE-10)

Harness yang menjalankan pipeline lengkap pada data terkontrol:

```
Data Worker → model downscaling → Spatial Pollution / OSM Cache DB → Cache API → API Gateway → Frontend
```

Laporan dan bukti eksekusi: [docs/ti-se-10/LAPORAN.md](../../docs/ti-se-10/LAPORAN.md).

## Menjalankan

Dari root repo (butuh Docker dan Python 3.9+; runner hanya memakai pustaka standar):

```bash
docker compose -f tests/e2e/docker-compose.yml --profile tools build
python3 tests/e2e/run_e2e.py      # ±3 menit; exit 0 bila semua pemeriksaan lulus
python3 tests/e2e/report.py       # docs/ti-se-10/MATRIKS.md dan docs/ti-se-10/hasil/
docker compose -f tests/e2e/docker-compose.yml down -v
```

Selama stack berjalan:
- frontend: http://127.0.0.1:18090
- gateway: http://127.0.0.1:18080
- Cache API: http://127.0.0.1:18001
- stub Route Model: http://127.0.0.1:18100 (`POST /__mode?name=slow|error|mislabel|no_route|valid`)
- basis data: `127.0.0.1:55433`, user/sandi `sipp`/`sipp` (lokal saja)

## Isi

| Berkas | Fungsi |
|---|---|
| `docker-compose.yml` | Stack dari checkout ini: PostGIS 18-3.6, Data Worker (one-shot), Cache API, stub Route Model, gateway, frontend. Worker hanya di jaringan internal (tanpa internet), jadi portal SPKU sengaja tidak terjangkau |
| `fixture/` | Dataset terkontrol beserta `manifest.json` (SHA-256, `dataset_version`) |
| `fixture/export_fixture.py` | Ekspor read-only dari basis data sumber (`SOURCE_DATABASE_URL`, tidak pernah dicetak) |
| `fixture/load_fixture.py` | Memeriksa checksum, membuat skema dari kode pemiliknya, lalu memuat data (jalan di image Data Worker) |
| `route_model_stub.py` | Stub Route Model (kontrak IRouteSolve) dengan mode yang dapat diganti saat berjalan |
| `run_e2e.py` | Matriks skenario E2E-01 sampai E2E-11; menulis `out/results.json`, `out/versions.json`, `out/correlation.log`, dan laporan Worker |
| `report.py` | Membuat `docs/ti-se-10/MATRIKS.md` dan menyalin bukti ke `docs/ti-se-10/hasil/` |

`out/` (termasuk artefak parquet Worker ±23 MB) tidak di-commit.

## Skenario

| ID | Skenario | Yang dibuktikan |
|---|---|---|
| E2E-01 | Cache kosong | 503 `cache_empty`, tanpa nilai yang dikarang |
| E2E-02 | Sukses Worker → frontend | Window ditulis Worker dipakai API dengan versi yang konsisten; korelasi `X-Request-ID`; frontend meneruskan ke gateway |
| E2E-03 | Window baru dan idempotensi | Window terbaru langsung dipakai; ETag heatmap berubah; Worker tidak menulis ulang |
| E2E-04 | Sumber data tidak tersedia | Worker gagal tanpa menulis window; API tetap memakai window terakhir |
| E2E-05 | Window sedang ditulis | Window `writing` tidak terbaca |
| E2E-06 | Inferensi gagal, fallback aktif | Lambat, galat, label salah, no_route, layanan mati → fallback bertanda |
| E2E-07 | Inferensi timeout, fallback mati | 503 dalam batas gateway; no_route model → 503 |
| E2E-08 | API dan basis data gagal | 502/504 JSON dari gateway; 503 `database_unavailable`; pulih otomatis |
| E2E-09 | Versi graf tidak cocok | 503 `cache_empty`, bukan paparan nol |
| E2E-10 | Kontrak API publik | Koleksi `public_api.smoke` (17 kasus) lewat gateway |
| E2E-11 | Log aman | Tanpa DSN, kata sandi, atau koordinat |

Pemeriksaan `no_false_success` dijalankan pada setiap respons rute: respons 200 wajib punya
paparan > 0, dan setiap kegagalan wajib berupa galat terstruktur non-200.
