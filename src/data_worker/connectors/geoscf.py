"""Konektor GEOS-CF v2 (ISatelliteArchive): analisis (``ana``) dan prakiraan (``fcst``) PM2.5/NO2.

Produk, subset 3 × 3 sel 0,25°, penyelarasan waktu, QC, dan konversi satuan
sama dengan data latih TI-AI-01 (``dataset_processed/geos-cf``):

- **ana:** satu berkas per jam di datashare NCCS. Waktu tersedia
  (``available_at_utc``) adalah header ``Last-Modified`` berkas jam tersebut;
  nilainya diambil lewat subset OPeNDAP. Jam yang belum terbit (HTTP 404)
  diperiksa lagi pada siklus berikutnya.
- **fcst:** satu run per hari pada 09z, 120 jam ke depan; dianggap tersedia
  setelah berkas lead terakhir terbit (``scripts/fetch_geoscf_fcst.py``).

Tanpa autentikasi. Tabel 3.19: 60 detik, tiga kali; subset OPeNDAP dihitung di
server (±13 detik per variabel), sehingga permintaan nilai diberi batas
``OPENDAP_TIMEOUT_S``. Bila gagal, prakiraan run sebelumnya untuk jam yang sama
tetap dipakai pembaca fitur (fallback Tabel 3.19).
"""

from __future__ import annotations

import datetime as dt
import email.utils
import re

import numpy as np
import pandas as pd

from scripts import fetch_geoscf_fcst as fcst_script

from .http import FormatError, HttpClient, Policy

SOURCE_ANA = "geoscf_ana"
SOURCE_FCST = "geoscf_fcst"
COLLECTION = fcst_script.COLLECTION
OPENDAP_ANA = f"https://opendap.nccs.nasa.gov/dods/gmao/geos-cf/v2/ana/{COLLECTION}"
PORTAL_ANA = "https://portal.nccs.nasa.gov/datashare/gmao/geos-cf/v2/ana"
POLICY = Policy(timeout_s=60, tries=3)
OPENDAP_TIMEOUT_S = 180
SOURCE_LABEL = "geos_cf_v2_ana"
LAT_INDEX, LON_INDEX = fcst_script.LAT_INDEX, fcst_script.LON_INDEX
LAT_CENTERS, LON_CENTERS = fcst_script.LAT_CENTERS, fcst_script.LON_CENTERS
UGM3_PER_PPB = fcst_script.UGM3_PER_PPB
PM25_RANGE, NO2_PPB_RANGE = fcst_script.PM25_RANGE, fcst_script.NO2_PPB_RANGE
#: Latensi terbit ana pada data latih: 6,3-62,7 jam setelah akhir jam.
ANA_LOOKBACK_HOURS = 72
ANA_MIN_LATENCY_HOURS = 6
ANA_MAX_HEADS = 80
#: Jam ana yang menunggu agregasi OPeNDAP dan akan keluar dari jendela ANA_LOOKBACK_HOURS dalam
#: kurang dari ini diberi peringatan: setelah keluar, jam itu tidak diperiksa lagi.
ANA_EXPIRY_WARNING_HOURS = 12
#: Permintaan per run fcst bila larik waktu ASCII-nya tidak sesuai sumbu linear.
FCST_ATTEMPTS = 2


def _http_time(value: str | None) -> pd.Timestamp | None:
    if not value:
        return None
    return pd.Timestamp(email.utils.parsedate_to_datetime(value)).tz_convert("UTC")


def ana_file_url(window_start: pd.Timestamp) -> str:
    stamp = window_start + pd.Timedelta(minutes=30)
    name = f"GEOS.cf.ana.{COLLECTION}.{stamp:%Y%m%d_%H%M}z.R0.nc4"
    return f"{PORTAL_ANA}/Y{stamp:%Y}/M{stamp:%m}/D{stamp:%d}/{name}"


def ana_window(now: dt.datetime) -> tuple[pd.Timestamp, pd.Timestamp]:
    """[awal, akhir) awal jam yang diperiksa pada siklus ``now``."""
    end = pd.Timestamp(now).floor("h") - pd.Timedelta(hours=ANA_MIN_LATENCY_HOURS)
    return end - pd.Timedelta(hours=ANA_LOOKBACK_HOURS), end


def ana_candidates(now: dt.datetime, stored: set) -> list[pd.Timestamp]:
    """Awal jam yang mungkin sudah terbit tetapi belum tersimpan, terlama dulu."""
    hours = pd.date_range(*ana_window(now), freq="h", inclusive="left")
    return [h for h in hours if h not in stored]


def ana_published(client: HttpClient, window_start: pd.Timestamp) -> pd.Timestamp | None:
    """``Last-Modified`` berkas jam ``window_start``; ``None`` bila belum terbit."""
    response = client.request("HEAD", ana_file_url(window_start), ok=(200, 404), allow_redirects=True)
    if response.status_code == 404:
        return None
    published = _http_time(response.headers.get("Last-Modified"))
    if published is None:
        raise FormatError("geoscf_ana: header Last-Modified tidak tersedia")
    return published


def ana_axis(client: HttpClient) -> tuple[pd.Timestamp, int]:
    """Awal dan panjang sumbu waktu agregasi OPeNDAP ``ana`` (linear per jam, cap menit ke-30)."""
    dds = client.get(f"{OPENDAP_ANA}.dds").text
    match = re.search(r"Float64 time\[time = (\d+)\]", dds)
    if not match:
        raise FormatError("geoscf_ana: sumbu waktu tidak ditemukan pada .dds")
    first = fcst_script._grads_time(fcst_script._parse_ascii(client.get(f"{OPENDAP_ANA}.ascii?time[0:0]").text,
                                                              "time"))[0]
    if first.minute != 30:
        raise FormatError(f"geoscf_ana: cap waktu pertama {first} bukan menit ke-30")
    return first, int(match.group(1))


def ana_index(first: pd.Timestamp, start: pd.Timestamp) -> int:
    """Indeks jam ``start`` pada sumbu waktu agregasi OPeNDAP ``ana``."""
    return int((start + pd.Timedelta(minutes=30) - first) / pd.Timedelta(hours=1))


def ana_query(first: pd.Timestamp, starts: list[pd.Timestamp]) -> tuple[str, int, int]:
    i0, i1 = ana_index(first, min(starts)), ana_index(first, max(starts))
    (a, b), (c, d) = LAT_INDEX, LON_INDEX
    sub = f"[{i0}:{i1}][0][{a}:{b}][{c}:{d}]"
    query = f"pm25_rh35{sub},no2{sub},time[{i0}:{i1}],lat[{a}:{b}],lon[{c}:{d}]"
    return f"{OPENDAP_ANA}.ascii?{query}", i0, i1


def parse_ana(text: str, published: dict) -> tuple[pd.DataFrame, dict]:
    """Subset ASCII OPeNDAP → baris (sel, jam) untuk jam yang sudah terbit (``published``: awal jam → waktu terbit).

    Satu baris per (time_window_start, cell_id) dengan skema
    ``geoscf_cell_hourly.parquet``; nilai di luar rentang fisik menjadi kosong.
    """
    try:
        lat, lon = fcst_script._parse_ascii(text, "lat"), fcst_script._parse_ascii(text, "lon")
        times = fcst_script._grads_time(fcst_script._parse_ascii(text, "time"))
        pm25 = fcst_script._parse_ascii(text, "pm25_rh35")
        no2 = fcst_script._parse_ascii(text, "no2")
    except (ValueError, IndexError) as exc:
        raise FormatError(f"geoscf_ana: subset OPeNDAP tidak terbaca ({exc})") from exc
    if not (np.allclose(lat, LAT_CENTERS) and np.allclose(lon, LON_CENTERS)):
        raise FormatError(f"geoscf_ana: grid subset berubah ({lat}, {lon})")
    if pm25.shape != (len(times), 1, 3, 3) or no2.shape != pm25.shape:
        raise FormatError(f"geoscf_ana: bentuk larik {pm25.shape} tidak terduga")
    if not (times.minute == 30).all():
        raise FormatError("geoscf_ana: cap tavg bukan menit ke-30")
    starts = times - pd.Timedelta(minutes=30)
    frame = pd.DataFrame({
        "time_window_start": np.repeat(starts, 9),
        "cell_id": np.tile(np.arange(9, dtype="int16"), len(starts)),
        "pm25_ugm3": pm25[:, 0].reshape(len(starts), 9).ravel(),
        "no2_ppb": no2[:, 0].reshape(len(starts), 9).ravel() * 1e9})
    frame = frame[frame.time_window_start.isin(set(published))].reset_index(drop=True)
    quality = {}
    for column, (low, high) in (("pm25_ugm3", PM25_RANGE), ("no2_ppb", NO2_PPB_RANGE)):
        bad = frame[column].notna() & ~frame[column].between(low, high)
        quality[column] = {"out_of_range": int(bad.sum()), "missing": int(frame[column].isna().sum())}
        frame.loc[bad, column] = np.nan
    frame["pm25_ugm3"] = frame.pm25_ugm3.astype("float32")
    frame["no2_ppb"] = frame.no2_ppb.astype("float32")
    frame["no2_ugm3"] = frame.no2_ppb * np.float32(UGM3_PER_PPB)
    frame["available_at_utc"] = frame.time_window_start.map(published)
    frame["latency_hours"] = ((frame.available_at_utc - frame.time_window_start - pd.Timedelta(hours=1))
                              .dt.total_seconds() / 3600).astype("float32")
    duplicates = int(frame.duplicated(["time_window_start", "cell_id"]).sum())
    frame = frame.drop_duplicates(["time_window_start", "cell_id"], keep="last")
    quality.update(hours=int(frame.time_window_start.nunique()), duplicate_rows=duplicates)
    return frame.sort_values(["time_window_start", "cell_id"]).reset_index(drop=True), quality


def fetch_ana(client: HttpClient, now: dt.datetime, stored: set) -> tuple[pd.DataFrame, dict]:
    """Jam ``ana`` yang baru terbit sejak siklus sebelumnya; kosong bila tidak ada yang baru.

    Agregasi OPeNDAP dapat tertinggal dari berkas jam di datashare (28 Oktober 2026: tertahan
    sekitar 7 jam). Jam yang sudah terbit tetapi belum masuk agregasi tidak diminta; jam itu
    tetap belum tersimpan, sehingga diperiksa lagi pada siklus berikutnya seperti jam yang
    belum terbit, dan dihitung pada ``waiting_hours``. Jam yang menunggu dan akan keluar dari
    jendela pemeriksaan dalam kurang dari ``ANA_EXPIRY_WARNING_HOURS`` dicantumkan pada
    ``expiring_hours``, karena setelah keluar jam itu tidak diperiksa lagi.
    """
    published = {}
    pending = ana_candidates(now, stored)
    for start in pending[-ANA_MAX_HEADS:]:
        moment = ana_published(client, start)
        if moment is not None:
            published[start] = moment
    info = {"checked_hours": min(len(pending), ANA_MAX_HEADS), "published_hours": len(published)}
    if not published:
        return pd.DataFrame(), info
    first, length = ana_axis(client)
    waiting = {h for h in published if ana_index(first, h) >= length}
    if waiting:
        published = {h: t for h, t in published.items() if h not in waiting}
        info.update(published_hours=len(published), waiting_hours=len(waiting), opendap_hours=length)
        edge = ana_window(now)[0] + pd.Timedelta(hours=ANA_EXPIRY_WARNING_HOURS)
        expiring = sorted(h for h in waiting if h < edge)
        if expiring:
            info["expiring_hours"] = [f"{h:%Y-%m-%dT%H:%MZ}" for h in expiring]
        if not published:
            return pd.DataFrame(), info
    url, _, _ = ana_query(first, list(published))
    frame, quality = parse_ana(client.get(url, timeout=OPENDAP_TIMEOUT_S).text, published)
    if frame.time_window_start.nunique() != len(published):
        raise FormatError("geoscf_ana: subset tidak memuat seluruh jam yang diminta")
    return frame, {**info, **quality}


# Prakiraan --------------------------------------------------------------------
def fcst_inits(now: dt.datetime, runs: int = 2) -> list[pd.Timestamp]:
    """Run 09z yang sudah dimulai, terbaru dulu."""
    latest = (pd.Timestamp(now) - pd.Timedelta(hours=fcst_script.INIT_HOUR)).floor("D") + pd.Timedelta(
        hours=fcst_script.INIT_HOUR)
    return [latest - pd.Timedelta(days=i) for i in range(runs)]


def fcst_complete(client: HttpClient, init: pd.Timestamp) -> bool:
    """Run lengkap bila berkas lead terakhirnya sudah terbit."""
    last = fcst_script.run_times(init)[-1]
    return client.request("HEAD", fcst_script._lead_url(init, last), ok=(200, 404),
                          allow_redirects=True).status_code == 200


def _getter(client: HttpClient):
    def get(url: str, method: str = "GET"):
        timeout = OPENDAP_TIMEOUT_S if method == "GET" else POLICY.timeout_s
        response = client.request(method, url, timeout=timeout, allow_redirects=True)
        return response.content, dict(response.headers)
    return get


def fetch_fcst(client: HttpClient, init: pd.Timestamp) -> tuple[pd.DataFrame, dict]:
    """Satu run lengkap → skema ``geoscf_fcst_cell_hourly.parquet`` (``process`` skrip data latih).

    Bila larik waktu ASCII memuat lead yang tidak sesuai sumbu linear, run diminta ulang
    (``FCST_ATTEMPTS`` kali; pada run 2026-11-01 09z kueri yang sama kadang menjawab waktu yang
    benar). Bila masih tidak sesuai, keselarasan nilai lead itu tidak dapat dipastikan: run tidak
    disimpan (frame kosong, ``deferred``), sehingga diminta lagi pada siklus berikutnya, dan
    pembaca fitur memakai run sebelumnya untuk jam yang sama.
    """
    try:
        for attempt in range(1, FCST_ATTEMPTS + 1):
            try:
                run = fcst_script.fetch_run(init, get=_getter(client))
            except fcst_script.ForecastTimeMismatch:
                if attempt == FCST_ATTEMPTS:
                    raise
                continue
            if not run["time_mismatch_leads"]:
                break
        else:
            return pd.DataFrame(), {"rows": 0, "deferred": True, "attempts": attempt,
                                    "time_mismatch_leads": run["time_mismatch_leads"]}
        frame = fcst_script.process({"runs": [run]})
    except (ValueError, IndexError) as exc:
        raise FormatError(f"geoscf_fcst: run {init:%Y-%m-%d %Hz} tidak terbaca ({exc})") from exc
    quality = {"rows": len(frame), "pm25_missing": int(frame.pm25_ugm3.isna().sum()),
               "no2_missing": int(frame.no2_ppb.isna().sum()), "deferred": False, "attempts": attempt}
    return frame, quality
