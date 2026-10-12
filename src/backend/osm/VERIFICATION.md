# Bukti Verifikasi RoadGraph (Backend → OSM Cache DB)

Dibuat oleh `python -m osm.verify` pada 2026-10-12 04:30 UTC. Semua kueri baca-saja.

**Hasil: LULUS**

## Aturan penyaringan yang diterapkan Backend

Izin akses per ruas dihitung saat import (`walk_allowed`, `bike_allowed`, `oneway`; aturannya di `handoff_database.md` bagian 6). Backend menerapkannya saat merakit graf:

1. Hanya versi `active` yang dibaca (`osm.graph_version`).
2. `walk`: hanya ruas `walk_allowed = true`; setiap ruas dapat dilalui dua arah (`oneway` diabaikan).
3. `bike`: hanya ruas `bike_allowed = true`; `u → v` selalu, `v → u` hanya bila `oneway = false`.
4. Ruas ditolak bila: tanpa geometri / < 2 titik, `length_m` kosong atau ≤ 0, node ujung tidak ada atau tanpa lokasi, ujung geometri tidak sama dengan node `u`/`v`, atau `edge_id` ganda (`osm/graph.py`, `assemble_road_graph`).
5. Self-loop dibuang saat merakit subgraf permintaan; simpul asal/tujuan di-snap hanya ke simpul yang menyentuh ruas yang boleh dilalui moda (maks. 200 m).

Uji akses moda (`pytest` di `src/backend/osm`): `tests/test_assemble.py` (ruas tanpa geometri, panjang tidak valid, perbedaan akses antar moda, satu arah, tujuan terisolasi, pemetaan EdgeWeight), `tests/test_graph.py` (UT-BE-04..06), `tests/test_models.py` (arah per moda), dan `tests/test_integration_db.py` (filter moda dan validitas ruas pada DB asli).

## Metadata versi graf

| version | status | osm_timestamp | built_at | node_count | edge_count |
|---|---|---|---|---|---|
| jakarta-20261005 | active | 2026-10-05 09:50:56Z | 2026-10-05 10:58:14Z | 299.067 | 373.352 |

Backend hanya membaca versi berstatus `active` (`jakarta-20261005`); versi `building` dan `retired` diabaikan.

## Statistik node dan ruas (versi aktif)

| Item | Jumlah |
|---|---|
| Node | 299.067 |
| Node bertag (gerbang, lampu lalu lintas, dll.) | 50.306 |
| Ruas | 373.352 |
| Total panjang (km) | 17.388 |
| Ruas satu arah (`oneway`) | 68.884 |
| Self-loop (`u = v`) | 154 |

## Akses moda

| Kategori | Ruas |
|---|---|
| Boleh jalan kaki (`walk_allowed`) | 315.665 |
| Boleh sepeda (`bike_allowed`) | 303.972 |
| Keduanya | 302.164 |
| Hanya jalan kaki | 13.501 |
| Hanya sepeda | 1.808 |
| Tidak keduanya (tol, busway, private, dll.) | 55.879 |
| Satu arah dan boleh sepeda (arah `v → u` ditutup untuk sepeda) | 57.365 |

<details><summary>Per tipe jalan (<code>highway</code>)</summary>

| highway | ruas | walk | bike |
|---|---|---|---|
| residential | 123.924 | 97.651 | 97.750 |
| living_street | 91.046 | 87.993 | 88.150 |
| service | 79.107 | 57.316 | 57.273 |
| tertiary | 24.770 | 24.606 | 24.725 |
| footway | 11.455 | 11.321 | 98 |
| primary | 11.359 | 10.928 | 11.149 |
| secondary | 10.874 | 10.608 | 10.837 |
| trunk | 4.875 | 4.726 | 4.868 |
| unclassified | 3.086 | 2.981 | 2.992 |
| path | 2.906 | 2.777 | 2.775 |
| busway | 1.964 | 0 | 0 |
| steps | 1.651 | 1.645 | 5 |
| motorway_link | 1.359 | 0 | 0 |
| motorway | 1.222 | 0 | 0 |
| primary_link | 1.076 | 946 | 1.059 |
| trunk_link | 936 | 828 | 910 |
| secondary_link | 507 | 418 | 481 |
| tertiary_link | 341 | 307 | 337 |
| pedestrian | 308 | 276 | 102 |
| track | 287 | 239 | 239 |
| cycleway | 222 | 22 | 222 |
| corridor | 77 | 77 | 0 |

</details>

## Validitas ruas (seluruh versi aktif)

Aturan yang sama dipakai `assemble_road_graph` saat merakit graf; ruas yang gagal tidak dimasukkan ke `RoadGraph` dan dihitung di `rejected`.

| Pemeriksaan | Ruas bermasalah |
|---|---|
| Tanpa geometri atau < 2 titik | 0 |
| `length_m` kosong, ≤ 0, atau tak hingga | 0 |
| Node ujung `u`/`v` tidak ada atau tanpa lokasi | 0 |
| Ujung geometri ≠ lokasi node `u`/`v` (toleransi 1e-5°) | 0 |
| `length_m` beda > max(1 m, 1%) dari panjang geometri | 0 |
| `edge_id` ganda dalam versi | 0 |
| `edge_id` dipakai ulang antar versi | 0 |
| Node tanpa geometri | 0 |

## Pemetaan ke EdgeWeight

`EdgeWeight` (`pollution.edge_pollution`) dirujuk lewat `edge_id` saja; karena `edge_id` unik secara global, setiap baris memetakan ke tepat satu ruas (`RoadGraph.edges_by_id()`).

| Item | Jumlah |
|---|---|
| Baris `pollution.edge_pollution` | 0 |
| Baris tanpa ruas yang cocok | 0 |

## Contoh `read_road_graph` (±1 × 1 km sekitar Monas)

| Moda | Node | Ruas | Ruas ditolak validasi | Ruas terlarang ikut terbaca |
|---|---|---|---|---|
| walk | 911 | 1.176 | - | 0 |
| bike | 457 | 516 | - | 0 |

