# `osm_cache`: penulisan OSM Cache DB

`write_road_graph(conn, graph)` menulis graf jalan sebagai versi baru di schema `osm`
(Dokumen Desain Tabel 3.11, `IRoadGraphWrite` Tabel 3.14/3.15, UT-OPS-04, IT-K-09).
Pembacanya adalah Backend ([`src/backend/osm`](../../backend/osm/README.md)).

## Alur

1. **Tulis sebagai `building`.** Dalam satu transaksi: baris `graph_version` berstatus `building`,
   lalu `COPY` node dan ruas. Setelah commit, Backend tetap membaca versi `active` sebelumnya.
2. **Periksa di basis data.** Yang diperiksa:
   - jumlah baris sama dengan input;
   - node ujung setiap ruas ada;
   - ujung geometri sama dengan lokasi node `u`/`v` (toleransi 1e-5°);
   - `length_m > 0` dan geometri minimal 2 titik;
   - `edge_id` tidak dipakai versi lain;
   - komponen terhubung terbesar ≥ 95% node (Tabel 3.9).
3. **Aktifkan.** Dalam satu transaksi: versi `active` lama menjadi `retired`, versi baru menjadi
   `active` (index unik parsial menjamin hanya satu yang `active`). Bila langkah 2 atau 3
   gagal, versi `building` dibuang dan versi `active` sebelumnya tetap berlaku.

Aturan versi:

- **Versi bersifat tetap.** Menulis ulang versi yang sudah `active` atau `retired` ditolak
  (`GraphVersionExists`).
- **Sisa `building` diganti.** Sisa versi `building` dari penulisan yang terputus diganti,
  bukan diduplikasi.
- **`edge_id` unik secara global**, karena `pollution.edge_pollution` hanya merujuk `edge_id`.
  Pakai `next_edge_id(conn)` untuk nomor awal versi baru; versi pertama di Railway memakai
  1–373.352, jadi versi berikutnya mulai dari 373.353. Penulis menjaga aturan ini berlapis:
  1. memeriksa `edge_id` yang sudah dipakai sebelum menulis;
  2. menangkap pelanggaran unique index `road_edge_edge_id_key` saat COPY, bila index ada;
  3. memeriksa lagi setelah menulis, untuk database tanpa unique index.

  Railway sudah punya unique index ini sejak 2026-10-13, jadi lapis 3 berlaku untuk basis data
  lain yang belum memilikinya.
- **Satu-satunya jalur import.** Script import lama (`/root/osm-jakarta/`) sudah tidak ada.
  Jangan mengimpor graf dengan script lain yang menomori `edge_id` dari 1.

## Contoh

```python
import psycopg
from osm_cache import GraphEdge, GraphNode, RoadGraphData, next_edge_id, write_road_graph

with psycopg.connect(dsn, autocommit=True) as conn:
    first = next_edge_id(conn)
    graph = RoadGraphData(
        version="jakarta-20261012",
        nodes=[GraphNode(1, 106.8272, -6.1754), GraphNode(2, 106.8282, -6.1754)],
        edges=[GraphEdge(first, 1, 2, 110.6, "footway", walk_allowed=True, bike_allowed=False,
                         coordinates=((106.8272, -6.1754), (106.8282, -6.1754)))],
    )
    result = write_road_graph(conn, graph)   # WriteResult(version, ..., retired_version)
```

## DDL dan uji

[`schema.sql`](schema.sql) adalah salinan struktur schema `osm` dan `pollution` di Railway
(per 2026-10-13), termasuk unique index `road_edge_edge_id_key`.
`schema_sql(osm, pollution)` mengganti nama schema supaya uji bisa membuat salinan terisolasi. Berkas ini untuk uji dan basis data lokal baru, bukan migrasi produksi.

Uji: [`tests/test_osm_cache_writer.py`](../tests/test_osm_cache_writer.py), dijalankan ke
basis data lokal ([`src/backend/testdb`](../../backend/testdb/docker-compose.yml)).
Langkahnya ada di [`src/backend/pollution/README.md`](../../backend/pollution/README.md#menguji).
