# Benchmark berpasangan v1 — 13 November 2026

Run menyelesaikan 50 pasangan OD × 3 preferensi pada satu field IDW historis. PIC A dan weighted Dijkstra memakai graf, bobot, OD dan preferensi yang sama. [Protokol](../benchmark-protocol.md) dan kode `fb4faa4` dibekukan sebelum run; [manifest](manifest.json) menyimpan input, 50 pasangan dan sampling. Proses selesai dengan exit code 0 setelah 50/50 pasangan.

| Ukuran | PIC A | Weighted Dijkstra |
|---|---:|---:|
| Rute berhasil / permintaan | 150/150 | 150/150 |
| Rute berhasil yang tidak valid | 0 | 0 |
| Latensi median | 173,1 ms | 12,9 ms |
| Latensi p95 | 407,1 ms | 45,3 ms |
| Latensi maksimum | 1.473,2 ms | 67,2 ms |

Pada α=0,5, 35/50 hash rute PIC A sama dengan optimum. Rata-rata selisih objektif terhadap optimum 0,043%, maksimum 0,505%; 11/50 rute PIC A lebih lambat sekaligus lebih terpapar daripada rute tercepat. Median perubahan paparan terhadap rute tercepat adalah 0%, sehingga target pengurangan 4% tidak tercapai pada sampel ini. Arah preferensi terpenuhi pada 46/50 pasangan (92%); hanya 7/50 (14%) benar-benar berganti rute dalam arah yang dimaksud. Rute identik dihitung terpenuhi sesuai definisi TI-AI-10.

Pada sampel dan lingkungan ini Dijkstra lebih cepat dan optimal terhadap objektif yang sama; bukti ini tidak menunjukkan keuntungan praktis PIC A. Waktu mengukur pencarian per permintaan pada satu thread, termasuk pencarian rute tercepat dan referensi untuk kedua metode; pemuatan, pembentukan input, jaringan dan API tidak diukur. Tidak ada timeout yang ditegakkan, dan tidak ada kasus gagal pada run ini. Hasil tidak menggantikan pengujian batas waktu atau pengukuran produksi.

Sampel OD baru tidak beririsan dengan 250 pasangan TI-AI-11; kedua titik masing-masing ada pada blok test. Ini tidak menjadikan field atau pemilihan model blind: field sudah dipakai sebelumnya, dan ruas rute dapat melintasi blok lain. Hanya satu waktu dan medan IDW, bukan field model spasial terdaftar, serta 50 OD sepeda; tidak menggeneralisasi ke seluruh kondisi. Sebanyak 1.177 ruas graf tanpa bobot diisi median sesuai `RouteEnv`; run tidak merekam jumlah ruas imputasi per rute. Weighted A* proyek dan penggunaan sumber daya per metode belum diukur. Hasil mentah mencatat hash rute ringkas, bukan seluruh geometri.

## Artefak dan pengulangan

- [Hasil per permintaan](scenarios.jsonl.gz), [ringkasan latensi](summary.json), dan [ringkasan kualitas](quality-summary.json). Jalankan `python scripts/summarize_ti_ai12.py docs/experiments/TI-AI-12/run-v1/scenarios.jsonl.gz` untuk menghitung ulang ringkasan kualitas; hanya pasangan lengkap dipakai (50/50 pada run ini).
- Python 3.11.18, PyTorch 2.15.0+cpu, satu thread, Linux WSL2; image Data Worker `sha256:ce0b1db66e998cdd73cf0a90dfb138b028ce2c922740d0d8e34a13b8fe152496`, host Docker 16 CPU dan 16.643.186.688 byte memori. Identitas image tidak menjamin waktu yang sama pada hardware lain.
- CSV yang dipakai kini dibekukan di [`inputs/idw-20260929.csv.gz`](../inputs/idw-20260929.csv.gz). Fixture awal dibuat dari parquet asli yang hash-nya diverifikasi terhadap manifest TI-AI-11 menggunakan `scripts/prepare_ti_ai11_route_fixture.py`; lihat [petunjuk reload](../../TI-AI-11/container-verification.md). Script benchmark menolak CSV jika hash berbeda. Parquet asli dan image tidak disimpan di repositori, sehingga membangun ulang field dan lingkungan sepenuhnya masih memerlukan artefak tersebut.
- Jalankan pada checkout commit `fb4faa4` dalam image tersebut dengan repository di `/repo`, fixture di `/fixture`, `PYTHONPATH=/repo`, dan `python scripts/benchmark_ti_ai12.py --weights /fixture/weights.csv --out <direktori-baru> --revision fb4faa4 --image-id ce0b1db66e99`. Waktu dapat berubah antar run; jangan menimpa artefak ini.
