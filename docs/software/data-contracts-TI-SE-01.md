# Kontrak data lintas komponen — TI-SE-01

Versi **1.0.0**, 27 Oktober 2026. Mengikat pertukaran Data Worker, model AI,
cache, backend, dan frontend. Rujukan implementasi Bab III: Tabel 3.3,
antarmuka cache Tabel 3.11/3.14–3.17, Kode 3.1–3.3.

## Sumber kontrak

- [JSON Schema 2020-12](../../src/backend/public_api/data-contracts-v1.schema.json):
  seluruh field, tipe, `required`, nullable, default dan batas struktural.
- [Corpus contoh valid/tidak valid](../../src/backend/public_api/examples/data-contracts-v1.json):
  `valid.<tipe>` dan `invalid[]`, termasuk alasan penolakan untuk keenam tipe.
- [API publik](../../src/backend/public_api/README.md),
  [OpenAPI](../../src/backend/public_api/openapi.json), [hasil uji](verification-TI-SE-01.md).

Identitas skema `urn:sippolusi:data-contracts:1.0.0`. Pilih tipe dengan `$ref`
`#/$defs/Point`, `RouteRequest`, `RoadGraph`, `EdgeWeight`, `TimeWindow`,
`RouteOutput`. `EdgeWeights` adalah envelope bobot; `SolverInput` adalah input
model rute. Akar bundle hanya menyimpan definisi; validasi harus memilih tipe.

```python
import json
from jsonschema import Draft202012Validator, FormatChecker

schema = json.load(open("src/backend/public_api/data-contracts-v1.schema.json", encoding="utf-8"))
Draft202012Validator(
    {**schema, "$ref": "#/$defs/RouteRequest"},
    format_checker=FormatChecker(),
).validate(payload)
```

`rfc3339-validator` diperlukan agar `FormatChecker` memeriksa `date-time`;
dependensi uji ada di `osm/requirements-dev.txt`. JSON Schema memeriksa struktur
dan batas per field. Aturan lintas field (jumlah preferensi, durasi window,
referensi simpul) diperiksa oleh model runtime dan uji kontrak. Lolos skema
saja belum berarti valid secara semantik.

## Aturan umum

| Aspek | Ketentuan |
|---|---|
| Serialisasi | JSON UTF-8, nama field `snake_case`; angka JSON, bukan string/boolean untuk koordinat dan preferensi |
| CRS | WGS 84 / EPSG:4326; GeoJSON memakai CRS84 dengan urutan eksplisit `[lon, lat]` |
| Point | `{ "lon": 106.8, "lat": -6.2 }`, derajat; lon −180…180, lat −90…90, finite |
| Array koordinat | Tepat dua angka `[lon,lat]`; bbox `[min_lon,min_lat,max_lon,max_lat]`, min < max |
| Waktu wire | ISO 8601/RFC 3339 dengan zona waktu; keluaran UTC Z (atau ekuivalen +00:00), bukan Unix epoch |
| Jarak/kecepatan | `*_m`: meter; `speed_mps`: meter/detik; `travel_time_s`: detik; `*_min`: menit |
| Polutan | `pm25_ugm3`, `no2_ugm3`, `concentration_ugm3`: µg/m³, finite ≥ 0 bila tersedia |
| Indeks/keyakinan | `exposure_index`: tanpa satuan, ≥ 0; `confidence_score`: pecahan 0…1 |
| ID | node_id, edge_id, u, v: integer, disimpan PostgreSQL BIGINT; tidak diganti saat melintasi komponen |
| Hilang | null berbeda dari nol; field nullable yang masuk `required` tetap wajib dikirim, dengan null bila hilang |

NaN/Infinity tidak valid dalam JSON. DataFrame boleh memakai NaN sebagai penanda
hilang pada kolom numerik nullable; writer menyimpannya sebagai SQL NULL.
Infinity, nilai negatif, dan confidence > 1 ditolak. `model_metadata` harus
object JSON tanpa NaN/Infinity. Koordinat, ID, dan waktu tidak diimputasi.

## Point dan RouteRequest

Runtime: [PointModel](../../src/backend/osm/schemas.py),
[RouteRequestIn/PreferenceIn](../../src/backend/planner/schemas.py),
[validasi wilayah studi](../../src/backend/planner/validation.py).

| Field request | Wajib / aturan |
|---|---|
| origin, destination | Wajib, Point dengan lat dan lon wajib, tidak nullable |
| mode | Wajib, walk / bike |
| pollutant | Opsional, default pm25; pilihan pm25 / no2; tidak menerima null |
| preference.alpha | Wajib, bobot waktu tanpa satuan, 0…1 |
| preference.beta | Wajib, bobot paparan tanpa satuan, 0…1 |

`abs(alpha + beta - 1) <= 1e-6`. Tidak ada default preferensi tersembunyi.
String numerik dan boolean ditolak untuk Point/preferensi. Field request
tambahan diabaikan sesuai perilaku Pydantic yang sudah dipakai; produsen tetap
mengirim field terdokumentasi saja.

```json
{"origin":{"lon":106.8272,"lat":-6.1754},"destination":{"lon":106.823,"lat":-6.195},"mode":"walk","preference":{"alpha":0.6,"beta":0.4}}
```

Mode car, destination hilang, latitude 106, dan α=0.6/β=0.5 tidak valid: 400
`invalid_request` sebelum membaca cache. Koordinat global valid di luar bbox
studi `[106.68,-6.38,106.98,-6.08]` menghasilkan 422 `outside_study_area`.
Kecepatan ditentukan profil backend (default walk 1.3, bike 4.2 m/s), bukan
parameter klien. Backend memilih window polusi complete terakhir; request
publik tidak memiliki parameter waktu tersembunyi.

## TimeWindow

Wire valid: `{"time_window_start":"2026-10-27T05:00:00Z","time_window_end":"2026-10-27T06:00:00Z"}`.
Keduanya wajib, timezone-aware, sejajar jam UTC (menit/detik/mikrodetik nol),
selisih tepat 3.600 detik. Interval `[start,end)`. Offset +07:00 dinormalisasi
ke UTC. Window dua jam, terbalik, mulai 05:30, tanpa zona waktu, dan epoch ditolak.

| Representasi | Pemetaan tetap |
|---|---|
| [Worker TimeWindow](../../src/data_worker/contracts.py) | start → time_window_start, end → time_window_end; dinormalisasi UTC |
| [Backend TimeWindow](../../src/backend/pollution/models.py) | Menyimpan start; end = start + satu jam |
| [TimeWindowOut](../../src/backend/pollution/schemas.py) | Envelope wire, dipakai ulang WindowOut dan EdgeWeightsOut |
| SQL cache | time_window_start TIMESTAMPTZ adalah identitas; end dihitung satu jam kemudian |

`starting_at()` adalah helper penjadwalan yang membulatkan ke awal jam;
constructor dan validator input tidak membulatkan input tidak valid.

## RoadGraph

Wire mengikuti [RoadGraphOut](../../src/backend/osm/schemas.py), hasil
GET /v1/osm/graph; domain di [osm/models.py](../../src/backend/osm/models.py).

| Field | Aturan |
|---|---|
| graph_version, mode, bbox | Wajib; versi tidak kosong; mode dan bbox mengikuti aturan umum |
| node_count, edge_count | Wajib, ≥ 0, sama dengan panjang daftar nodes/edges |
| nodes[] | node_id, lon, lat, tags wajib; WGS 84; tags object string → string |
| edges[] | edge_id, u, v, length_m, highway, name, oneway, walk_allowed, bike_allowed, osm_way_id, coordinates wajib |
| Metadata ruas nullable | highway, name, osm_way_id, coordinates boleh null; length_m wajib finite > 0 meter |
| coordinates | Jika diminta: ≥ 2 vertex [lon,lat], searah u → v; null berarti geometri tidak diminta, bukan geometri hilang di DB |
| rejected | Wajib, object alasan → hitungan baris yang dikeluarkan saat perakitan |

ID nodes/edges harus unik; u/v harus ada dalam nodes; ruas mendukung mode
payload. Graf kosong memakai list kosong dan hitungan nol. [Assembler](../../src/backend/osm/graph.py)
memeriksa endpoint geometri dengan toleransi 1e-5 derajat dan menghitung baris
ditolak; tidak ada imputasi topologi. Walk mengabaikan oneway, bike mengikuti
oneway, tetapi wire tetap u → v. Corpus berisi graf lengkap dan contoh rusak
(endpoint tidak ada, panjang nol, vertex latitude 91).

Pemetaan [RoadGraphData worker](../../src/data_worker/osm_cache/writer.py):
`version` menjadi graph_version pada tabel graf/node/edge; GraphNode disimpan
sebagai POINT SRID 4326 dan GraphEdge.coordinates sebagai LINESTRING SRID 4326.
Worker memakai list nodes; backend domain memakai dict node_id → node;
RoadGraphOut.build mengubahnya menjadi list JSON. Mode/bbox adalah filter baca,
bukan atribut graf mentah. ID, versi, koordinat, dan length_m dipertahankan.
Edge ID unik lintas versi; versi baru diaktifkan secara transaksional.
CRS grid AI internal (UTM/meter) tidak menjadi CRS wire graf.

## EdgeWeight dan EdgeWeights

Definisi [worker](../../src/data_worker/contracts.py), [backend](../../src/backend/pollution/models.py),
[serializer](../../src/backend/pollution/schemas.py), [writer](../../src/data_worker/spatial_model/inference/cache.py).

| Field EdgeWeight wire | Wajib / aturan |
|---|---|
| edge_id | Wajib integer, merujuk ruas graf; model tidak membuat ulang ID |
| pm25_ugm3, no2_ugm3, exposure_index | Wajib nullable, finite ≥ 0 bila tersedia |
| confidence_score | Wajib nullable, finite 0…1 bila tersedia |
| background_source | Wajib nullable; aod / reanalysis bila tersedia |
| estimation_source | Wajib nullable untuk data historis; produsen baru wajib idw / stgnn / hybrid |
| graph_version, model_version | Wajib nullable untuk historis; penulisan baru mensyaratkan versi batch tidak kosong |
| model_metadata | Opsional default {}; sumber/versi/fallback per polutan, object JSON |
| trace_run_id | Opsional default null; cache baru memerlukan trace sumber sel |

Envelope EdgeWeights memuat field TimeWindow, model_version, coverage_ratio,
graph_version, mode, bbox, data_stale, age_minutes, count, excluded, weights[].
Semua wajib; versi/coverage/mode boleh null sesuai skema. graph_version=null
pada envelope berarti membaca tanpa filter versi. Window berlaku untuk seluruh
rows; jangan menafsirkan EdgeWeight tanpa konteks envelope/cache key.

Worker membawa time_window_start pada dataclass. Writer menerima satu window,
graph_version dan model_version batch; waktu row harus cocok sebelum transaksi.
DataFrame tanpa kolom waktu memakai window batch; jika kolomnya ada, harus cocok.
Writer menambahkan versi batch pada setiap row SQL. Backend memindahkan waktu
ke envelope, mempertahankan versi per row. Pemetaan antarmuka ini tetap dan diuji.

Writer menolak ID ganda/noninteger, sumber tidak didukung, nilai di luar batas,
metadata JSON tidak valid, versi kosong, coverage di luar 0…1 sebelum COPY.
Hanya window complete dibaca; rollback tidak mengekspos hasil parsial. Sebagian
nilai cache memakai REAL, sehingga pengujian nilai memakai toleransi numerik.

I = ½(PM2.5/15 + NO2/25); bila hanya satu polutan tersedia, pakai rasio polutan
itu. Planner mengisi indeks ruas tanpa estimasi dengan median bbox dan confidence
0, menandainya weight_source=imputed_median. Konsentrasi tidak diimputasi.
Jika tidak ada bobot yang dapat dipakai, backend mengembalikan error cache.

## Input model rute dan keluaran rute

SolverInput adalah proyeksi eksplisit graf/bobot, didefinisikan di
[planner/schemas.py](../../src/backend/planner/schemas.py). travel_time_s dalam
detik; length_m meter; concentration_ugm3 mengikuti pollutant request, sementara
kedua konsentrasi asal tetap tersedia. [IRouteSolve](../../src/backend/solver/README.md)
mendokumentasikan deadline, dua urutan edge ID, kontinuitas, label dan fallback.

RouteOutput = [RouteFeatureCollection](../../src/backend/public_api/schemas.py):
GeoJSON dengan tepat dua LineString, recommended lalu comparison. Properties:
role, route_source, preference_applied, edge_ids dan metrik berikut.

| Metrik | Batas / satuan |
|---|---|
| distance_m | finite ≥ 0, meter |
| travel_time_min | finite ≥ 0, menit |
| exposure_index_min | finite ≥ 0, indeks·menit, Σ Iₑ τₑ |
| exposure_ug_min_m3 | finite ≥ 0 atau null, µg·menit/m³; null bila ada ruas tanpa konsentrasi |
| confidence_score, concentration_coverage | finite 0…1 |
| imputed_edge_count | integer ≥ 0 |

Metadata memuat request_id, mode/pollutant/preference, waktu data, versi graf/model,
sumber/fallback, estimation, comparison, notes, CRS, coordinate_order, units dan disclaimer;
field lengkap/required/null ada pada skema. Delta waktu/paparan boleh negatif;
delta persen null jika denominator nol. Model kontrak output publik menolak
field tambahan. Backend menghitung geometry/metrik dari edge ID terverifikasi.
Frontend memvalidasi dan memetakan wire menjadi view types di api.ts; contoh
policy/fallback yang sama dipakai uji frontend.

`metadata.estimation` memuat `source` (nullable) dan `fallback_pollutants`
(daftar pm25/no2). Input solver juga memuat `source.downscaling_fallback`
(default daftar kosong). Keduanya mengikuti metadata provenance terbaru di dev
dan tercakup dalam bundle skema v1.0.0.

## Versi dan regenerasi

Versi 1.0.0 dipasang pada artefak skema, bukan field wajib baru pada payload.
Komponen memakai kontrak melalui sumber berversi Git; bundle bukan endpoint
runtime baru. Versi aplikasi OpenAPI (1.2.0), graph_version dan model_version
memiliki arti berbeda dan tidak digunakan untuk menebak kompatibilitas kontrak.

- Patch: koreksi dokumentasi tanpa mengubah payload valid.
- Minor: perluasan kompatibel pada profil yang mengizinkannya. Consumer ketat
  diperbarui bersama produsen dan diuji sebelum rollout.
- Major: field wajib, arti, satuan, CRS berubah, field dihapus atau domain nilai
  berubah tidak kompatibel. Sediakan profil/artefak baru dan migrasi eksplisit;
  jangan mengganti arti field v1 diam-diam.

Regenerasi dari src/backend:

```bash
python -m public_api.data_contracts --write
python -m public_api.openapi --write
```

Uji membandingkan bundle tersimpan dengan model runtime, memeriksa corpus valid
dan tidak valid, serializer aktual, kontrak frontend, dan alur writer/reader PostGIS.
