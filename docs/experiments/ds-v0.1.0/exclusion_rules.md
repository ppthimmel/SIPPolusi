# Aturan eksklusi ds-v0.1.0

Pembacaan ground truth SPKU (ekspor `ground_truth`, seluruh cap waktu UTC sejati) dibersihkan sebelum diagregasi per time window. Nilai tidak pernah diubah atau diinterpolasi; pembacaan yang dibuang hanya diberi alasan. Setiap pembacaan memperoleh satu alasan, yaitu aturan pertama yang cocok menurut urutan tabel. Label time window adalah rata-rata pembacaan valid dalam [awal jam, akhir jam).

| # | Aturan | Alasan | PM2.5 | NO2 |
|---|---|---|---|---|
| 1 | Stasiun di luar bbox studi (`station.in_jakarta_bbox = false`) | Dua stasiun Kepulauan Seribu berada di luar wilayah studi dan grid 100 m. | 787 | 0 |
| 2 | Stasiun dikecualikan seluruhnya | DKI_PM25_40 (SDN 01 Manggarai): PM2.5 macet tepat 32,00; sebagian bacaannya lolos bendera qc. | 823 | 0 |
| 3 | Bendera `qc` portal tidak kosong (`S`, `N`, `R`, `Z`) | Sensor macet, nilai negatif, di luar rentang fisik, atau nol sebagai penanda kosong. | 2.310 | 81 |
| 4 | Nilai tepat 0 | Portal memakai 0 sebagai "tidak ada data"; bacaan nol tidak diberi bendera. | 169 | 45 |
| 5 | Nilai ≥ 999,99 | Kode sentinel Kelapa Gading (DKI2) yang lolos bendera qc. | 1 | 0 |
| 6 | Parameter stasiun dikecualikan sejak waktu tertentu | PM2.5 DKI_PM25_33 (RPTRA Citra Permata) macet 14,00 sejak 2026-09-19 10:00Z. | 6 | 0 |
| 7 | Bagian dari deret ≥ 12 nilai identik berturut-turut pada deret penuh | Definisi bendera `S` portal diterapkan ulang pada seluruh deret (semua bendera). Portal menghitung `S` pada riwayat 48 jam yang bergulir, sehingga ujung deret macet yang panjang kehilangan benderanya (mis. DKI_PM25_85 macet 34,00 dan DKI_PM25_57 macet 19,00). | 207 | 0 |
| 8 | Jam dengan pembacaan valid lebih sedikit dari kadensi stasiun | Kadensi = modus jumlah bacaan per jam stasiun-parameter (2 untuk deret 30 menit, 1 untuk deret 60 menit). Label jam tidak boleh mewakili hanya sebagian jam. | 279 | 84 |
| | **Valid** | | 78.170 dari 82.752 | 9.863 dari 10.073 |

## Parameter

- Sentinel: `>= 999.99`.
- Stasiun dikecualikan: DKI_PM25_40.
- PM25 DKI_PM25_33 sejak 2026-09-19T10:00:00Z.
- Deret macet: ≥ 12 nilai identik berturut-turut.
- Jam lengkap wajib: ya.

## Yang diperiksa tetapi tidak dibuang

- **Duplikat stasiun/penyedia:** tidak ada pasangan stasiun dengan nilai identik pada ≥ 20% cap waktu bersama. DKJ37 dan DKI98 (Taman Sungai Kendal) berjarak 1,3 m tetapi nilainya berbeda (median selisih PM2.5 27 µg/m³), sehingga keduanya dipertahankan sebagai dua seri dalam satu grup stasiun.
- **Stasiun bias rendah:** stasiun `DKI_PM25_*` bertipe Sensor dengan rerata PM2.5 blok train < 10 µg/m³ ditandai `suspected_low_bias`, tidak dibuang.
- **Klaster nilai lintas stasiun pada jam yang sama:** porsi stasiun terbanyak dengan nilai bulat yang sama ≤ 27,5% pada seluruh jam; tidak ditemukan pola gangguan portal, sehingga tidak ada aturan.
- **Cap waktu:** ekspor sudah dikoreksi −7 jam secara seragam (label portal selalu WIB); 0 bacaan dengan `ts_utc > first_seen_utc`.
