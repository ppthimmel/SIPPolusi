# Protokol benchmark berpasangan v1

Dibekukan sebelum run. Tujuannya mengukur kualitas dan latensi PIC A terhadap weighted Dijkstra pada input yang sama, bukan mengklaim evaluasi independen menyeluruh.

- Graf `jakarta-20261005`, mode sepeda; bobot CSV fixture TI-AI-11, SHA-256 `1c2493d80ce33763f5d2485d1560d909960b63c08c5c9234d64ac58d321b2584`, berasal dari field IDW 29 September 2026 00:00 UTC. Field ini sudah diperiksa sebelumnya.
- PIC A checkpoint SHA-256 `28709b934d26d99d1d8e0124409b5aad31ecfceb3780b872aa869614a54a0af6`; satu thread CPU. Paket, graf, bobot dan lingkungan dimuat sebelum pengukuran.
- Sampel 50 OD terjangkau pada blok test (`SplitSpec` seed 42), jarak garis lurus 0,5–3 km, seed sampling 1212026. Tolak seluruh pasangan yang ada pada sampel perbandingan TI-AI-11, lalu ambil 50 pertama yang tersisa. Pasangan dan hitungan penolakan disimpan. Blok test memisahkan titik asal dan tujuan dari blok train/val, bukan seluruh ruas yang dilalui; field dan populasi bukan blind baru.
- Uji α = 0, 0,5, 1 untuk setiap pasangan. PIC A memakai `decode_preference_route`, batas 400 langkah dan faktor 3. Weighted Dijkstra meminimalkan objektif normalisasi yang sama. Urutan metode bergantian menurut indeks skenario.
- Waktu permintaan PIC A mengukur pemanggilan decode, termasuk pencarian rute tercepat internal. Waktu Dijkstra mencakup pencarian rute tercepat, perhitungan referensi dan pencarian weighted. Pengukuran tidak mencakup pemuatan model/graf, pembangunan input, komunikasi jaringan atau API. Tidak ada batas waktu yang ditegakkan; kegagalan dihitung dan alasan dicatat. Latensi ini bukan benchmark layanan produksi.
- Simpan setiap hasil, waktu, hash rute, metrik waktu/paparan/panjang, identitas input dan lingkungan. Laporkan keberhasilan termasuk gagal, median dan p95 waktu, objektif berpasangan, serta perubahan paparan dan waktu pada α=0,5. Tiap perubahan protokol atau input menghasilkan run baru.
