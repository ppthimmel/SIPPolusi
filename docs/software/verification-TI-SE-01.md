# Verifikasi TI-SE-01 — kontrak data bersama

Tanggal: **27 Oktober 2026**. Branch `TI-SE-01`, baseline sebelum perubahan
`1f7692e`. Implementasi dan bukti ini belum di-commit pada saat verifikasi.

## Artefak dan acceptance criteria

| Kriteria issue | Bukti |
|---|---|
| Keenam tipe, contoh dan aturan validasi | [Kontrak terpusat](data-contracts-TI-SE-01.md), [skema v1.0.0](../../src/backend/public_api/data-contracts-v1.schema.json), [corpus valid/tidak valid](../../src/backend/public_api/examples/data-contracts-v1.json) |
| RouteRequest lengkap, satuan tidak ambigu | RouteRequestIn/PreferenceIn, PointModel, validasi wilayah; [OpenAPI](../../src/backend/public_api/openapi.json) dan kontrak terpusat |
| RoadGraph/EdgeWeight dapat dipertukarkan | Pemetaan worker/domain/wire/cache terdokumentasi; uji nyata writer → PostGIS → backend → serializer/skema |
| Masukan tidak lengkap, koordinat salah, moda unsupported, TimeWindow invalid | Uji validasi planner/API, corpus skema dan uji TimeWindow/backend/worker |
| Validasi otomatis batas layanan | Pydantic request, validator jumlah α/β, graf dan window; validasi cache row/batch sebelum transaksi/COPY |

Definisi domain tetap berada di komponen masing-masing. Kontrak yang dibagikan
adalah bentuk pertukaran berversi, bukan kewajiban mengimpor satu paket Python.
Tidak ada perubahan format wajib request publik atau migrasi database.

## Lingkungan

- Windows, Python 3.11.14 (Anaconda), Pydantic 2.13.5, FastAPI 0.162.0,
  asyncpg 0.32.0; executable lokal `.venv-spatial/Scripts/python.exe`.
- Validasi JSON Schema: jsonschema 4.25.1 dengan rfc3339-validator 0.1.4.
  Dependensi uji dinyatakan pada `src/backend/osm/requirements-dev.txt`.
  Pada sesi lokal, rfc3339-validator dipasang ke `.cache/contract-test-deps`
  dan ditambahkan ke PYTHONPATH agar tidak mengubah instalasi Conda bersama.
- Database tulis: container disposable `postgis/postgis:18-3.6`, Compose project
  `ti-se-01-contracts`, `localhost:55432`, bukan database produksi.
- Tes HTTP/asyncio dijalankan di luar sandbox: sandbox Windows menahan pembuatan
  socketpair lokal sebelum request dijalankan. Tidak ada perubahan kode aplikasi
  untuk mengatasi batas sandbox.

## Hasil yang benar-benar dijalankan

| Pemeriksaan | Hasil |
|---|---|
| Kontrak baru + validasi + sinkronisasi OpenAPI | **60 passed** |
| Worker terkait: kontrak, spatial edgeweight, data worker, scheduler, graph writer; dengan PostGIS | **94 passed** |
| Seluruh Data Worker, PyTorch diimpor sebelum collection; tanpa TEST_DATABASE_URL | **285 passed, 76 skipped** |
| Backend osm/pollution/planner/solver/public_api lengkap; dengan PostGIS | **458 passed, 15 skipped, 2 failed**; kedua kegagalan juga direproduksi pada baseline asli, rincian di bawah |
| Komponen backend yang berubah (osm/pollution/planner/public_api), final; dengan PostGIS | **362 passed, 15 skipped, 1 deselected**; hanya mismatch contoh baseline di bawah yang dikecualikan |
| Solver cancellation, serial tanpa suite lain berjalan | **8 passed** pada source saat ini; **8 passed** pada baseline HEAD |
| Frontend `npm test -- --reporter=dot` | **102 passed** pada 8 berkas, termasuk kontrak API |
| Frontend `npm run build` | Berhasil; peringatan ukuran chunk > 500 kB |

Suite backend lengkap di atas dijalankan sebelum tambahan dua uji offset zona
waktu; keduanya kemudian ditambahkan dalam siklus RED → GREEN dan masuk ke
60 uji kontrak serta 362 uji komponen backend terakhir.

JUnit/log sesi lokal tersedia pada `ci-reports/ti-se-01-*.xml` dan `.log`.
Direktori ini diabaikan Git sesuai aturan repo; ringkasan dan perintah reproduksi
di dokumen ini menjadi bukti yang dapat dibagikan melalui repository.

## Kasus regresi yang ditambahkan

- Point/preferensi tidak menerima boolean atau string numerik; jumlah α/β
  diperiksa langsung oleh skema runtime request, sebelum akses cache.
- Graf menolak ID ganda, endpoint tidak ada, jumlah node/edge tidak cocok,
  bbox terbalik, panjang tidak positif, vertex salah dan mode tidak diizinkan.
- Bobot menolak konsentrasi/indeks negatif, Infinity/NaN wire, confidence > 1,
  sumber tidak dikenal, ID pecahan/boolean/nullable kosong dan metadata non-JSON.
- Window menolak zona waktu hilang, format compact, epoch, durasi salah,
  awal tidak sejajar jam dan offset tidak sah seperti +00:60. Offset valid
  dinormalisasi UTC pada worker maupun backend.
- Writer menolak row dari window lain dan metadata batch tidak valid sebelum
  transaksi; measured zero dan konsentrasi hilang tetap dibedakan.
- Output rute memeriksa metrik, waktu hourly dan urutan recommended/comparison.
- Contoh valid/tidak valid diperiksa dengan model runtime; kasus struktural
  diperiksa pula dengan JSON Schema. Bundle tersimpan harus sama dengan hasil
  generator dari model yang benar-benar dipakai API.
- `test_ti_se_01_versioned_graph_and_weight_contract_roundtrip` menulis graf/bobot
  menggunakan writer asli, membaca melalui repository backend, memvalidasi hasil
  serializer terhadap skema bersama dan memeriksa ID, graph_version, provenance,
  timestamp, nol dan null. Uji ini **lulus** pada PostGIS lokal.

Uji regresi pertama dijalankan sebelum implementasi: 20 kegagalan backend dan
12 kegagalan worker menunjukkan validasi yang belum ada. Temuan review ID
nullable, format timestamp dan output rute juga direproduksi dengan uji gagal
sebelum diperbaiki. Review terpisah tidak menemukan isu P0/P1.

## Dua kegagalan baseline yang tidak disembunyikan

1. `solver/tests/test_model_service.py::test_unavailable_model`: koneksi ke port
   localhost tertutup berakhir sebagai `model_timeout`, sedangkan uji berharap
   `model_unavailable`. Gagal pula ketika uji dijalankan sendiri pada salinan
   source HEAD `1f7692e` tanpa perubahan task ini.
2. `public_api/tests/test_examples.py::test_examples_are_what_the_api_answers`:
   fixture rute menggunakan `travel_time_min=8.462`, hasil lokal `8.461`.
   Perbandingan fixture identik gagal pada salinan backend **dan worker** HEAD
   `1f7692e` dengan PostGIS yang sama. Contoh tersimpan tetap valid terhadap
   kontrak dan tetap dibaca frontend; file contoh tidak ditulis ulang untuk
   menyembunyikan perbedaan pembulatan ini.

Tidak ada klaim bahwa seluruh repository bebas kegagalan. Kedua masalah di
atas berada di luar perubahan kontrak dan perlu dilacak terpisah. Uji integrasi
OSM yang memerlukan DATABASE_URL deployment dilewati; TEST_DATABASE_URL hanya
digunakan untuk fixture tulis lokal yang terisolasi.

Dua run regression luas yang mengecualikan kegagalan baseline masing-masing
berakhir **459 passed, 15 skipped, 2 deselected, 1 failed**, akibat assertion
waktu solver cancellation: `test_baseline_stops_after_the_timeout` (0.401 s
dibanding batas 0.4 s) dan `test_mock_search_does_not_block_the_timeout` (batas
latensi/penyelesaian thread). Pemeriksaan modul cancellation saat suite komponen
masih berjalan juga menghasilkan 7 passed / 1 failed. Setelah pengujian lain
selesai, run serial modul yang sama menghasilkan **8 passed** pada source saat
ini dan **8 passed** pada HEAD asli. Hasil ini dicatat sebagai sensitivitas timing,
bukan disembunyikan dengan melonggarkan assertion atau mengubah kode solver.

Percobaan awal collection seluruh worker mengalami DLL load-order PyTorch
Windows (c10.dll) pada test_registered_spatial, test_spatial_evaluation dan
test_stgnn. Impor torch sendiri berhasil; mengimpornya sebelum pytest membuat
suite lengkap berjalan dengan hasil 285/76 di atas. Tidak ada tes yang dihapus.
Warning Starlette/httpx, PyTorch dan NumPy/pyproj juga tetap tercatat pada log.

## Reproduksi

Perintah berikut dari root repository, dengan dependensi backend/worker dan
frontend terpasang. Sesuaikan `python` dengan interpreter lingkungan uji.

```powershell
python -m pytest -c src/backend/public_api/pytest.ini src/backend/public_api/tests/test_data_contracts.py src/backend/public_api/tests/test_data_contracts_validation.py src/backend/public_api/tests/test_openapi.py -q

docker compose -f src/backend/testdb/docker-compose.yml -p ti-se-01-contracts up -d --wait
$env:TEST_DATABASE_URL = 'postgresql://test:test@localhost:55432/test'
python -m pytest src/data_worker/tests/test_contract_validation.py src/data_worker/tests/test_spatial_edgeweight.py src/data_worker/tests/test_data_worker.py src/data_worker/tests/test_worker_schedule.py src/data_worker/tests/test_osm_cache_writer.py -q
python -m pytest -c src/backend/public_api/pytest.ini src/backend/osm/tests src/backend/pollution/tests src/backend/planner/tests src/backend/solver/tests src/backend/public_api/tests -q

# Full worker run without a configured database (DB cases skip):
Remove-Item Env:TEST_DATABASE_URL
python -c "import torch, pytest; raise SystemExit(pytest.main(['src/data_worker/tests', '-q']))"

# From src/frontend:
npm ci --ignore-scripts --no-audit --no-fund
npm test -- --reporter=dot
npm run build
```

Regenerasi bundle dan OpenAPI dari `src/backend`:

```powershell
python -m public_api.data_contracts --write
python -m public_api.openapi --write
```

Setelah pengujian, hentikan hanya Compose project disposable yang dibuat di atas:
`docker compose -f src/backend/testdb/docker-compose.yml -p ti-se-01-contracts down`.

Container dan network project tersebut sudah dihapus setelah verifikasi.
Graphify diperbarui dengan `D:/conda_envs/base2/Scripts/graphify.exe update .`
(AST-only, tanpa API); graph akhir memiliki 5.422 node dan memakai tampilan
komunitas agregat karena melebihi 5.000 node. `git diff --check` bersih.
