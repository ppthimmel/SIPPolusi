"""Akuisisi dan pemrosesan GEOS-CF v2 *forecast* (fcst) untuk 9 sel Jakarta.

Setiap hari NASA menjalankan satu run prakiraan GEOS-CF v2 pada 09z dengan 120 jam
ke depan (`aqc_tavg_1hr_glo_L1440x721_slv`, produk dan grid yang sama dengan `ana`).
Berbeda dengan `ana` yang terbit 6–63 jam sesudah jamnya, nilai prakiraan untuk jam t
sudah terbit sebelum t, sehingga dapat dipakai pada waktu inferensi.

- Nilai: subset OPeNDAP 3 × 3 sel 0,25° (lat −6,5…−6,0; lon 106,5…107,0), sama dengan `ana`.
- Waktu tersedia: header `Last-Modified` berkas jam terakhir run di portal datashare
  (run dianggap tersedia setelah lengkap; konservatif untuk seluruh lead).
- Penyelarasan dan QC sama dengan `ana`: cap tavg menit ke-30 − 30 menit = awal jam;
  PM2.5 `pm25_rh35` µg/m³; NO2 mol/mol × 1e9 = ppb × 1,8804 = µg/m³; rentang 0–2000 µg/m³
  dan 0–10000 ppb.

    python scripts/fetch_geoscf_fcst.py --start 2026-01-01 --end 2026-09-30 \\
        --raw <folder>/geoscf_fcst_jakarta_2026.json --out dataset_processed/geos-cf
"""

from __future__ import annotations

import argparse
import datetime as dt
import email.utils
import hashlib
import json
import re
import time
import urllib.error
import urllib.request
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import numpy as np
import pandas as pd

OPENDAP = "https://opendap.nccs.nasa.gov/dods/gmao/geos-cf/v2/fcst/aqc_tavg_1hr_glo_L1440x721_slv"
PORTAL = "https://portal.nccs.nasa.gov/datashare/gmao/geos-cf/v2/fcst"
COLLECTION = "aqc_tavg_1hr_glo_L1440x721_slv"
INIT_HOUR = 9
LEADS = 120
LAT_INDEX = (334, 336)          # −90 + 0,25·i → −6,5 … −6,0
LON_INDEX = (1146, 1148)        # −180 + 0,25·j → 106,5 … 107,0
LAT_CENTERS = [-6.5, -6.25, -6.0]
LON_CENTERS = [106.5, 106.75, 107.0]
UGM3_PER_PPB = 1.8804308553640376      # 298,15 K, 101.325 Pa; sama dengan manifest ana
PM25_RANGE = (0.0, 2000.0)
NO2_PPB_RANGE = (0.0, 10000.0)
VERSION = "geoscf-fcst-preprocessed-v1"


def _get(url: str, method: str = "GET", tries: int = 4) -> tuple[bytes, dict]:
    for attempt in range(tries):
        try:
            req = urllib.request.Request(url, method=method, headers={"User-Agent": "SIPPolusi-data-worker"})
            with urllib.request.urlopen(req, timeout=180) as resp:
                return resp.read(), dict(resp.headers)
        except (urllib.error.URLError, TimeoutError) as exc:
            if attempt == tries - 1:
                raise RuntimeError(f"{method} {url}: {exc}") from exc
            time.sleep(5 * (attempt + 1))
    raise AssertionError


def _parse_ascii(text: str, name: str) -> np.ndarray:
    """Array OPeNDAP ASCII ``name, [d0][d1]...`` → numpy dengan bentuk yang dinyatakan."""
    start = text.index(f"{name}, [")
    shape = [int(x) for x in re.findall(r"\[(\d+)\]", text[start:text.index("\n", start)])]
    values = []
    for line in text[text.index("\n", start) + 1:].splitlines():
        if not line.strip():
            if len(values) >= int(np.prod(shape)):
                break
            continue
        if not line.startswith("[") and values:
            break
        body = line.split("], ", 1)[1] if line.startswith("[") else line
        values.extend(float(x) for x in body.split(","))
    return np.asarray(values[:int(np.prod(shape))], dtype=float).reshape(shape)


def _grads_time(days: np.ndarray) -> pd.DatetimeIndex:
    """Waktu GrADS (hari; ordinal Python + 1) → UTC. Diverifikasi terhadap waktu awal run."""
    base = [dt.datetime.fromordinal(int(d) - 1) + dt.timedelta(days=float(d) - int(d)) for d in days]
    return pd.DatetimeIndex(base).tz_localize("UTC").round("min")


def run_times(init: pd.Timestamp) -> pd.DatetimeIndex:
    """Cap tavg tiap lead: awal run + 30 menit + k jam, k = 0…119."""
    return init + pd.Timedelta(minutes=30) + pd.to_timedelta(np.arange(LEADS), unit="h")


def _lead_url(init: pd.Timestamp, valid: pd.Timestamp) -> str:
    name = (f"GEOS.cf.fcst.{COLLECTION}.{init:%Y%m%d_%H}z%2B{valid:%Y%m%d_%H%M}z.R0.nc4")
    return f"{PORTAL}/Y{init:%Y}/M{init:%m}/D{init:%d}/{name}"


def fetch_run(init: pd.Timestamp, get=None) -> dict:
    """Subset satu run; ``get(url, method)`` → (isi, header) dapat diganti (bawaan ``_get``)."""
    get = get or _get
    (a, b), (c, d) = LAT_INDEX, LON_INDEX
    sub = f"[0:{LEADS - 1}][0][{a}:{b}][{c}:{d}]".replace("[", "%5B").replace("]", "%5D")
    url = (f"{OPENDAP}/{COLLECTION}.{init:%Y%m%d_%H}z.ascii?pm25_rh35{sub},no2{sub},"
           f"time%5B0:{LEADS - 1}%5D,lat%5B{a}:{b}%5D,lon%5B{c}:{d}%5D")
    body, _ = get(url)
    text = body.decode()
    lat, lon = _parse_ascii(text, "lat"), _parse_ascii(text, "lon")
    if not (np.allclose(lat, LAT_CENTERS) and np.allclose(lon, LON_CENTERS)):
        raise ValueError(f"{init}: grid subset tidak sesuai ({lat}, {lon})")
    times = run_times(init)
    # Sumbu waktu GrADS linear (awal run + 30 menit + k jam). Larik ``time`` ASCII hanya pemeriksaan:
    # baris panjangnya dapat terpotong sehingga satu angka terbaca salah (teramati pada run 2026-08-27).
    decoded = _grads_time(_parse_ascii(text, "time"))
    if decoded[0] != times[0] or (decoded != times).sum() > 2:
        raise ValueError(f"{init}: waktu run tidak sesuai ({decoded[0]}, {(decoded != times).sum()} beda)")
    first = get(_lead_url(init, times[0]), "HEAD")[1].get("Last-Modified")
    last = get(_lead_url(init, times[-1]), "HEAD")[1].get("Last-Modified")
    if not first or not last:
        raise ValueError(f"{init}: Last-Modified tidak tersedia")
    as_iso = lambda h: email.utils.parsedate_to_datetime(h).astimezone(dt.timezone.utc).isoformat()  # noqa: E731
    return dict(init_utc=init.isoformat(), time_utc=[t.isoformat() for t in times],
                pm25_rh35=_parse_ascii(text, "pm25_rh35")[:, 0].tolist(),
                no2=_parse_ascii(text, "no2")[:, 0].tolist(),
                first_lead_last_modified=as_iso(first), last_lead_last_modified=as_iso(last),
                source_url=url.split("?")[0])


def process(raw: dict) -> pd.DataFrame:
    """Raw run → satu baris per (run, awal jam valid, sel)."""
    frames = []
    for run in raw["runs"]:
        init = pd.Timestamp(run["init_utc"])
        times = run_times(init)
        if len(run["time_utc"]) != LEADS or len(run["pm25_rh35"]) != LEADS or len(run["no2"]) != LEADS:
            raise ValueError(f"{init}: jumlah lead tidak {LEADS}")
        pm = np.asarray(run["pm25_rh35"], float).reshape(len(times), 9)
        ppb = np.asarray(run["no2"], float).reshape(len(times), 9) * 1e9
        start = times - pd.Timedelta(minutes=30)
        frames.append(pd.DataFrame({
            "init_utc": init, "time_window_start": np.repeat(start, 9),
            "lead_hours": np.repeat(((start - init) / pd.Timedelta(hours=1)).astype("float32"), 9),
            "cell_id": np.tile(np.arange(9, dtype="int8"), len(times)),
            "pm25_ugm3": pm.ravel(), "no2_ppb": ppb.ravel(),
            "first_published_utc": pd.Timestamp(run["first_lead_last_modified"]),
            "available_at_utc": pd.Timestamp(run["last_lead_last_modified"])}))
    frame = pd.concat(frames, ignore_index=True)
    for column, (low, high) in (("pm25_ugm3", PM25_RANGE), ("no2_ppb", NO2_PPB_RANGE)):
        frame.loc[frame[column].notna() & ~frame[column].between(low, high), column] = np.nan
    frame["pm25_ugm3"] = frame.pm25_ugm3.astype("float32")
    frame["no2_ppb"] = frame.no2_ppb.astype("float32")
    frame["no2_ugm3"] = frame.no2_ppb * np.float32(UGM3_PER_PPB)
    frame["available_pm25"] = frame.pm25_ugm3.notna()
    frame["available_no2"] = frame.no2_ppb.notna()
    for col in ("init_utc", "time_window_start", "first_published_utc", "available_at_utc"):
        frame[col] = frame[col].astype("datetime64[us, UTC]")
    if frame.duplicated(["init_utc", "time_window_start", "cell_id"]).any():
        raise ValueError("Baris prakiraan ganda")
    return frame.sort_values(["init_utc", "time_window_start", "cell_id"]).reset_index(drop=True)


def sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--start", required=True, help="tanggal run pertama (UTC)")
    parser.add_argument("--end", required=True, help="tanggal run terakhir (UTC), inklusif")
    parser.add_argument("--raw", type=Path, required=True, help="JSON mentah (dilanjutkan bila sudah ada)")
    parser.add_argument("--out", type=Path, required=True, help="folder dataset_processed/geos-cf")
    parser.add_argument("--workers", type=int, default=1, help="permintaan paralel ke server NASA (1 disarankan)")
    parser.add_argument("--pause", type=float, default=20.0, help="jeda antar-run dalam detik")
    args = parser.parse_args()

    raw = json.loads(args.raw.read_text(encoding="utf-8")) if args.raw.exists() else {
        "product": f"GEOS-CF v2 fcst {COLLECTION}", "lat": LAT_CENTERS, "lon": LON_CENTERS, "runs": []}
    have = {r["init_utc"] for r in raw["runs"]}
    todo = [day + pd.Timedelta(hours=INIT_HOUR) for day in pd.date_range(args.start, args.end, freq="D", tz="UTC")
            if (day + pd.Timedelta(hours=INIT_HOUR)).isoformat() not in have]
    args.raw.parent.mkdir(parents=True, exist_ok=True)
    # OPeNDAP menghitung subset di server (±13 s per variabel). Permintaan beruntun yang rapat dapat
    # membuat firewall NCCS memblokir IP pemanggil (teramati 13 Oktober 2026), jadi bawaannya satu
    # permintaan sekaligus dengan jeda antar-run.
    def polite(init):
        run = fetch_run(init)
        time.sleep(args.pause)
        return run

    with ThreadPoolExecutor(max_workers=args.workers) as pool:
        for init, run in zip(todo, pool.map(polite, todo)):
            raw["runs"].append(run)
            raw["runs"].sort(key=lambda r: r["init_utc"])
            args.raw.write_text(json.dumps(raw), encoding="utf-8")   # simpan bertahap; dapat dilanjutkan
            print(f"{init:%Y-%m-%d} ok", flush=True)

    frame = process(raw)
    args.out.mkdir(parents=True, exist_ok=True)
    path = args.out / "geoscf_fcst_cell_hourly.parquet"
    frame.to_parquet(path, index=False)
    publish = (frame.drop_duplicates("init_utc").available_at_utc
               - frame.drop_duplicates("init_utc").init_utc) / pd.Timedelta(hours=1)
    manifest = dict(
        schema=VERSION, source=dict(product=f"GEOS-CF v2 fcst {COLLECTION}", access=OPENDAP,
                                    availability=f"Last-Modified berkas lead terakhir run di {PORTAL}",
                                    raw_file=args.raw.name, raw_sha256=sha256(args.raw),
                                    variables={"pm25_rh35": "µg/m³ pada RH 35% (termasuk air)",
                                               "no2": "mol/mol udara kering"}),
        runs=dict(n=int(frame.init_utc.nunique()), first=str(frame.init_utc.min()), last=str(frame.init_utc.max()),
                  init_hour_utc=INIT_HOUR, leads_per_run=LEADS),
        cells=dict(n=9, lat_centers=LAT_CENTERS, lon_centers=LON_CENTERS, cell_id="iy * 3 + ix (sama dengan ana)"),
        time_alignment="cap tavg menit ke-30 dikurangi 30 menit = time_window_start (awal jam, UTC)",
        availability_policy="available_at_utc = Last-Modified lead terakhir (run lengkap); first_published_utc = "
                            "Last-Modified lead pertama",
        publication_hours_after_init=dict(min=float(publish.min()), median=float(publish.median()),
                                          max=float(publish.max())),
        no2_conversion=dict(ugm3_per_ppb=UGM3_PER_PPB, T_ref_K=298.15, P_ref_Pa=101325.0),
        decoded_time_mismatches={r["init_utc"]: int((pd.to_datetime(r["time_utc"], utc=True)
                                                       != run_times(pd.Timestamp(r["init_utc"]))).sum())
                                 for r in raw["runs"] if (pd.to_datetime(r["time_utc"], utc=True)
                                                          != run_times(pd.Timestamp(r["init_utc"]))).any()},
        qc=dict(pm25_range=PM25_RANGE, no2_ppb_range=NO2_PPB_RANGE,
                pm25_missing=int(frame.pm25_ugm3.isna().sum()), no2_missing=int(frame.no2_ppb.isna().sum())),
        rows=int(len(frame)), output=dict(file=path.name, sha256=sha256(path)))
    (args.out / "fcst_manifest.json").write_text(json.dumps(manifest, indent=2, ensure_ascii=False) + "\n",
                                                 encoding="utf-8")
    print(json.dumps(manifest["runs"]), json.dumps(manifest["publication_hours_after_init"]))


if __name__ == "__main__":
    main()
