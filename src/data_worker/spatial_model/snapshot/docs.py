"""Dokumen yang dihasilkan untuk snapshot: aturan eksklusi, statistik split, README."""

from __future__ import annotations

import pandas as pd

from feature_matrix.labels import REASONS

from .split import SPLITS

METRIC_OF = {"pm25": "PM25", "no2": "NO2"}
TARGET_NAME = {"pm25": "PM2.5", "no2": "NO2"}

REASON_TEXT = {
    "outside_study_area": ("Stasiun di luar bbox studi (`station.in_jakarta_bbox = false`)",
                           "Dua stasiun Kepulauan Seribu berada di luar wilayah studi dan grid 100 m."),
    "excluded_station": ("Stasiun dikecualikan seluruhnya",
                         "DKI_PM25_40 (SDN 01 Manggarai): PM2.5 macet tepat 32,00; sebagian bacaannya lolos bendera qc."),
    "qc_flag": ("Bendera `qc` portal tidak kosong (`S`, `N`, `R`, `Z`)",
                "Sensor macet, nilai negatif, di luar rentang fisik, atau nol sebagai penanda kosong."),
    "zero_value": ("Nilai tepat 0",
                   "Portal memakai 0 sebagai \"tidak ada data\"; bacaan nol tidak diberi bendera."),
    "sentinel": ("Nilai ≥ 999,99", "Kode sentinel Kelapa Gading (DKI2) yang lolos bendera qc."),
    "excluded_since": ("Parameter stasiun dikecualikan sejak waktu tertentu",
                       "PM2.5 DKI_PM25_33 (RPTRA Citra Permata) macet 14,00 sejak 2026-09-19 10:00Z."),
    "stuck_run": ("Bagian dari deret ≥ 12 nilai identik berturut-turut pada deret penuh",
                  "Definisi bendera `S` portal diterapkan ulang pada seluruh deret (semua bendera). Portal "
                  "menghitung `S` pada riwayat 48 jam yang bergulir, sehingga ujung deret macet yang panjang "
                  "kehilangan benderanya (mis. DKI_PM25_85 macet 34,00 dan DKI_PM25_57 macet 19,00)."),
    "incomplete_hour": ("Jam dengan pembacaan valid lebih sedikit dari kadensi stasiun",
                        "Kadensi = modus jumlah bacaan per jam stasiun-parameter (2 untuk deret 30 menit, 1 untuk "
                        "deret 60 menit). Label jam tidak boleh mewakili hanya sebagian jam."),
}


def exclusion_counts(readings: pd.DataFrame, raw_stations: pd.DataFrame, cfg: dict) -> dict:
    scope = cfg["scope"]
    r = readings[(readings.ts_utc >= pd.Timestamp(scope["first_window"]))
                 & (readings.ts_utc < pd.Timestamp(scope["end_window"]))]
    out = {}
    for target in scope["targets"]:
        m = r[r.metric == METRIC_OF[target]]
        counts = m.exclusion_reason.fillna("valid").value_counts()
        out[target] = dict(readings=int(len(m)), valid=int(counts.get("valid", 0)),
                           excluded={k: int(counts.get(k, 0)) for k in REASONS})
    return out


def exclusion_rules(cfg: dict, counts: dict) -> str:
    lab = cfg["labels"]
    lines = [
        f"# Aturan eksklusi {cfg['version']}", "",
        "Pembacaan ground truth SPKU (ekspor `ground_truth`, seluruh cap waktu UTC sejati) dibersihkan sebelum "
        "diagregasi per time window. Nilai tidak pernah diubah atau diinterpolasi; pembacaan yang dibuang hanya "
        "diberi alasan. Setiap pembacaan memperoleh satu alasan, yaitu aturan pertama yang cocok menurut urutan "
        "tabel. Label time window adalah rata-rata pembacaan valid dalam [awal jam, akhir jam).", "",
        "| # | Aturan | Alasan | PM2.5 | NO2 |", "|---|---|---|---|---|",
    ]
    for i, key in enumerate(REASONS, 1):
        title, why = REASON_TEXT[key]
        lines.append(f"| {i} | {title} | {why} | {_num(counts['pm25']['excluded'][key])} | "
                     f"{_num(counts['no2']['excluded'][key])} |")
    lines += [
        f"| | **Valid** | | {_num(counts['pm25']['valid'])} dari {_num(counts['pm25']['readings'])} | "
        f"{_num(counts['no2']['valid'])} dari {_num(counts['no2']['readings'])} |", "",
        "## Parameter", "",
        f"- Sentinel: `>= {lab['sentinel']}`.",
        f"- Stasiun dikecualikan: {', '.join(lab['excluded_stations'])}.",
        *[f"- {r['metric']} {r['kode']} sejak {r['since']}." for r in lab.get("excluded_from", [])],
        f"- Deret macet: ≥ {lab['stuck_run_min']} nilai identik berturut-turut.",
        f"- Jam lengkap wajib: {'ya' if lab['require_complete_hour'] else 'tidak'}.", "",
        "## Yang diperiksa tetapi tidak dibuang", "",
        "- **Duplikat stasiun/penyedia:** tidak ada pasangan stasiun dengan nilai identik pada ≥ 20% cap waktu "
        "bersama. DKJ37 dan DKI98 (Taman Sungai Kendal) berjarak 1,3 m tetapi nilainya berbeda (median selisih "
        "PM2.5 27 µg/m³), sehingga keduanya dipertahankan sebagai dua seri dalam satu grup stasiun.",
        "- **Stasiun bias rendah:** stasiun `DKI_PM25_*` bertipe Sensor dengan rerata PM2.5 blok train "
        f"< {lab['low_bias_suspect']['max_mean_ugm3']:g} µg/m³ ditandai `suspected_low_bias`, tidak dibuang.",
        "- **Klaster nilai lintas stasiun pada jam yang sama:** porsi stasiun terbanyak dengan nilai bulat yang "
        "sama ≤ 27,5% pada seluruh jam; tidak ditemukan pola gangguan portal, sehingga tidak ada aturan.",
        "- **Cap waktu:** ekspor sudah dikoreksi −7 jam secara seragam (label portal selalu WIB); 0 bacaan dengan "
        "`ts_utc > first_seen_utc`.", "",
    ]
    return "\n".join(lines)


def counts(labels: pd.DataFrame, stations: pd.DataFrame, split_frames: dict, folds: pd.DataFrame) -> dict:
    out = dict(stations=int(len(stations)), groups=int(stations.group_id.nunique()),
               station_split={s: dict(groups=int(stations.loc[stations.station_split == s, "group_id"].nunique()),
                                      stations=int((stations.station_split == s).sum())) for s in SPLITS},
               label_rows=int(len(labels)),
               valid={t: int(labels[f"{t}_ugm3"].notna().sum()) for t in ("pm25", "no2")},
               splits={s: {t: int((f.target == t).sum()) for t in ("pm25", "no2")} for s, f in split_frames.items()},
               loso_folds={t: int(folds.loc[folds.target == t, "fold"].nunique()) for t in ("pm25", "no2")})
    return out


def _num(n) -> str:
    return f"{n:,}".replace(",", ".")


def _dec(x) -> str:
    return f"{x:.1f}".replace(".", ",")


def split_stats(labels: pd.DataFrame, stations: pd.DataFrame, split_frames: dict, folds: pd.DataFrame,
                blocks: dict) -> str:
    lines = ["# Statistik split", "",
             "## Blok temporal (awal time window, UTC)", "", "| Blok | Mulai | Akhir (eksklusif) |", "|---|---|---|",
             *[f"| {k} | {v[0]} | {v[1]} |" for k, v in blocks.items()], "",
             "## Split stasiun (per grup)", "",
             "| Split | Grup | Stasiun | Reference | Sensor | Stasiun PM2.5 | Stasiun NO2 |", "|---|---|---|---|---|---|---|"]
    has = {t: set(labels.loc[labels[f"{t}_ugm3"].notna(), "station_uuid"]) for t in ("pm25", "no2")}
    for s in SPLITS:
        g = stations[stations.station_split == s]
        lines.append(f"| {s} | {g.group_id.nunique()} | {len(g)} | {(g.station_type == 'Reference').sum()} | "
                     f"{(g.station_type == 'Sensor').sum()} | {g.uuid.isin(has['pm25']).sum()} | "
                     f"{g.uuid.isin(has['no2']).sum()} |")
    lines += ["", "Kota administrasi per split stasiun:", "", "| Kota | " + " | ".join(SPLITS) + " |",
              "|---|" + "---|" * len(SPLITS)]
    tab = stations.groupby(["kota", "station_split"]).size().unstack(fill_value=0).reindex(columns=list(SPLITS),
                                                                                            fill_value=0)
    lines += [f"| {k} | " + " | ".join(str(int(v)) for v in row) + " |" for k, row in tab.iterrows()]
    for target in ("pm25", "no2"):
        lines += ["", f"## {TARGET_NAME[target]}", "",
                  "| Split | Protokol | Stasiun-jam | Stasiun | Grup | Jam | Rerata | Simpangan baku | Median | Maks |",
                  "|---|---|---|---|---|---|---|---|---|---|"]
        for s, f in split_frames.items():
            sub = f[f.target == target]
            if not len(sub):
                lines.append(f"| {s} | - | 0 | 0 | 0 | 0 | - | - | - | - |")
                continue
            v = sub.value_ugm3
            lines.append(f"| {s} | {sub.protocol.iloc[0]} | {_num(len(sub))} | {sub.station_uuid.nunique()} | "
                         f"{sub.group_id.nunique()} | {sub.time_window_start.nunique()} | {_dec(v.mean())} | "
                         f"{_dec(v.std())} | {_dec(v.median())} | {_dec(v.max())} |")
        f = folds[folds.target == target]
        lines += ["", f"Fold LOSO {TARGET_NAME[target]}: {f.fold.nunique()} fold (satu per grup), "
                  f"{f.station_uuid.nunique()} stasiun, {_num(int(f.n_labels.sum()))} label stasiun-jam "
                  f"pada seluruh periode."]
    unused = labels[labels.pm25_ugm3.notna() & labels.pm25_split.isna()]
    lines += ["", "## Label di luar split tetap PM2.5", "",
              f"{_num(len(unused))} label PM2.5 valid tidak masuk split tetap karena berada pada jeda antarblok "
              "atau pada kombinasi stasiun × blok yang berbeda (mis. stasiun uji pada blok train). Label ini "
              "tetap dipakai oleh fold LOSO.", ""]
    return "\n".join(lines)


def readme(manifest: dict, report: dict) -> str:
    c = manifest["counts"]
    sp = c["splits"]
    return "\n".join([
        f"# Snapshot dataset {manifest['version']}", "",
        "Snapshot awal yang dibekukan untuk pelatihan dan evaluasi Spatial Downscaling Model (TI-AI-03, isu #14). "
        "Folder ini immutable: perubahan aturan atau data menghasilkan versi baru, bukan menimpa versi ini. "
        "Data operasional di PostgreSQL (`ground_truth.*`, `pollution.*`) terus berubah dan tidak dipakai langsung.",
        "", "Dibangun dengan:", "", "```bash", "cd src/data_worker",
        "python -m spatial_model.snapshot build --ground-truth <ekspor sippolusi_ground_truth_20261006> "
        "--out <folder baru>", "python -m spatial_model.snapshot verify ../../docs/experiments/ds-v0.1.0", "```", "",
        "## Ringkasan", "",
        f"- **Rentang:** time window {manifest['time_range']['first_window']} s.d. "
        f"{manifest['time_range']['end_window_exclusive']} (eksklusif), UTC.",
        f"- **Wilayah:** grid 100 m EPSG:32748; {c['stations']} stasiun pada {manifest['spatial']['label_cells']} "
        f"sel; graf {_num(manifest['spatial']['nodes'])} node.",
        f"- **Target:** PM2.5 ({_num(c['valid']['pm25'])} label stasiun-jam) dan NO2 ({_num(c['valid']['no2'])}), "
        "µg/m³.",
        f"- **Split PM2.5 blind ganda:** train {_num(sp['train']['pm25'])}, val {_num(sp['val']['pm25'])}, "
        f"test {_num(sp['test']['pm25'])} stasiun-jam; {c['groups']} grup stasiun "
        f"({c['station_split']['train']['groups']}/{c['station_split']['val']['groups']}/"
        f"{c['station_split']['test']['groups']}).",
        f"- **LOSO per grup:** {c['loso_folds']['pm25']} fold PM2.5, {c['loso_folds']['no2']} fold NO2.",
        f"- **Laporan kebocoran:** {'lolos' if report['passed'] else 'GAGAL'} "
        f"({sum(x['status'] == 'lolos' for x in report['checks'])}/{len(report['checks'])}).", "",
        "## Berkas", "",
        "| Berkas | Isi |", "|---|---|",
        "| `manifest.json` | Rentang waktu, wilayah, fitur dan target beserta satuan, jumlah per split, seed, aturan "
        "split dan eksklusi, versi sumber, SHA-256 setiap berkas, commit, versi pustaka |",
        "| `CHECKSUMS.sha256` | SHA-256 seluruh berkas (`shasum -a 256 -c CHECKSUMS.sha256`) |",
        "| `labels.parquet` | Label stasiun-jam: nilai, jumlah bacaan, alasan eksklusi, blok, split per target |",
        "| `stations.parquet` | Stasiun, sel, node, grup, split stasiun, penanda bias rendah |",
        "| `splits/{train,val,test}.parquet` | Kunci label per split dan target, beserta protokolnya |",
        "| `splits/loso_folds.parquet` | Fold leave-one-station-out per grup dan target |",
        "| `splits/loso_sensor_distance.parquet` | `dist_nearest_sensor_m` per node tanpa grup yang ditahan |",
        "| `split_spec.json`, `split_stats.md` | Aturan split, penugasan grup, statistik tiap split |",
        "| `exclusion_rules.md` | Aturan eksklusi, alasan, dan jumlah bacaan terbuang |",
        "| `normalization.json` | Statistik normalisasi fitur, target, dan rasio GEOS-CF/SPKU dari data train |",
        "| `leakage_report.{json,md}` | Delapan pemeriksaan kebocoran |",
        "| `stgnn/` | FeatureMatrix ST-GNN TI-AI-02 (`read_stgnn_batch`-ready); `labels.split` = split PM2.5 "
        "blind ganda (`none` di luar split), sensor = stasiun train |",
        "| `source/` | Salinan ekspor ground truth yang dibekukan |",
        "| `config.toml` | Konfigurasi pembangunan |", "",
        "## Pemakaian", "",
        "- **PM2.5, split tetap:** latih pada `splits/train.parquet`, pilih model pada `val`, laporkan pada "
        "`test`. Fitur `dist_nearest_sensor_m` di `stgnn/features.parquet` sudah memakai stasiun train saja.",
        "- **PM2.5 dan NO2, LOSO:** untuk fold k, tahan seluruh stasiun grup `group_id` fold tersebut dari "
        "pelatihan dan dari fitur sensor (`read_stgnn_batch(..., sensors=...)` atau "
        "`splits/loso_sensor_distance.parquet`).",
        "- **Normalisasi:** pakai `normalization.json`, atau fit ulang hanya pada baris train.", ""])
