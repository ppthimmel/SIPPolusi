"""Konektor Open-Meteo (IMeteoFeed, ``fetch_meteo``): cuaca per jam satu titik Jakarta.

Titik, variabel, dan satuannya sama dengan data latih TI-AI-01
(``dataset_processed/open_meteo``): -6,2 LS / 106,85 BT, tujuh variabel inti,
model ``best_match``, zona UTC. Satu permintaan per siklus memuat dua hari ke
belakang dan dua hari ke depan; jam lampau pada API ini adalah jam-jam pertama
setiap run model yang disambung, sama dengan Historical Forecast API data latih.

Tanpa kunci API (penggunaan nonkomersial). Tabel 3.19: 30 detik, tiga kali.
"""

from __future__ import annotations

import numpy as np
import pandas as pd

from feature_matrix.sources import WEATHER

from .http import FormatError, HttpClient, Policy

SOURCE = "open_meteo"
URL = "https://api.open-meteo.com/v1/forecast"
LATITUDE, LONGITUDE = -6.2, 106.85
POLICY = Policy(timeout_s=30, tries=3)
UNITS = {"temperature_2m": "°C", "relative_humidity_2m": "%", "wind_speed_10m": "km/h",
         "wind_direction_10m": "°", "boundary_layer_height": "m", "precipitation": "mm",
         "surface_pressure": "hPa"}
#: Rentang fisik; nilai di luar rentang dibuang (menjadi kosong) dan dicatat pada ``qc``.
RANGES = {"temperature_2m": (-10.0, 50.0), "relative_humidity_2m": (0.0, 100.0),
          "wind_speed_10m": (0.0, 250.0), "wind_direction_10m": (0.0, 360.0),
          "boundary_layer_height": (0.0, 6000.0), "precipitation": (0.0, 500.0),
          "surface_pressure": (800.0, 1100.0)}


def params(past_days: int = 2, forecast_days: int = 2) -> dict:
    return {"latitude": LATITUDE, "longitude": LONGITUDE, "hourly": ",".join(WEATHER),
            "past_days": past_days, "forecast_days": forecast_days, "timezone": "UTC"}


def fetch(client: HttpClient) -> dict:
    return client.get_json(URL, params=params())


def parse(payload: dict) -> tuple[pd.DataFrame, dict]:
    """Respons JSON → satu baris per jam UTC (tanpa agregasi atau imputasi) dan ringkasan kualitas.

    Nilai kosong tetap kosong (``None`` → NaN). Format yang berubah (kunci,
    variabel, satuan, zona waktu, atau panjang larik) dinaikkan sebagai
    ``FormatError``; jam ganda dideduplikasi dengan nilai terakhir.
    """
    if not isinstance(payload, dict) or not isinstance(payload.get("hourly"), dict):
        raise FormatError("open_meteo: respons tanpa objek 'hourly'")
    if payload.get("utc_offset_seconds", 0) != 0:
        raise FormatError(f"open_meteo: zona waktu respons bukan UTC ({payload.get('timezone')})")
    hourly = payload["hourly"]
    missing = [v for v in ["time", *WEATHER] if v not in hourly]
    if missing:
        raise FormatError(f"open_meteo: variabel hilang dari respons: {missing}")
    units = payload.get("hourly_units") or {}
    changed = {v: units.get(v) for v in WEATHER if units.get(v) not in (None, UNITS[v])}
    if changed:
        raise FormatError(f"open_meteo: satuan berubah: {changed}")
    lengths = {len(hourly[v]) for v in ["time", *WEATHER]}
    if len(lengths) != 1:
        raise FormatError("open_meteo: panjang larik per variabel berbeda")
    try:
        times = pd.to_datetime(hourly["time"], format="%Y-%m-%dT%H:%M").tz_localize("UTC")
    except (ValueError, TypeError) as exc:
        raise FormatError("open_meteo: format waktu tidak dikenal") from exc
    frame = pd.DataFrame({"time_utc": times})
    qc: dict[str, int] = {}
    for variable in WEATHER:
        values = pd.to_numeric(pd.Series(hourly[variable], dtype="object"), errors="coerce").astype("float64")
        low, high = RANGES[variable]
        bad = values.notna() & ~values.between(low, high)
        if bad.any():
            qc[variable] = int(bad.sum())
            values[bad] = np.nan
        frame[variable] = values.to_numpy()
        frame[f"{variable}_qc"] = bad.to_numpy()
    duplicates = int(frame.time_utc.duplicated().sum())
    frame = frame.drop_duplicates("time_utc", keep="last").sort_values("time_utc").reset_index(drop=True)
    quality = {"hours": len(frame), "duplicate_hours": duplicates, "out_of_range": qc,
               "missing": {v: int(frame[v].isna().sum()) for v in WEATHER}}
    return frame, quality
