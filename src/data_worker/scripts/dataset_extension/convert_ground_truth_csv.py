"""Ground-truth CSV exports (Railway + GCP) -> Parquet export in the old `sippolusi_ground_truth_*` format.

Usage: python convert_gt.py OUT_DIR CSV_DIR [CSV_DIR ...]   (later dirs win on duplicate keys)
Writes station.parquet, observation.parquet, manifest.json (what feature_matrix.labels / the snapshot builder read).
"""
import hashlib
import json
import sys
from pathlib import Path

import pandas as pd

# Study area, as documented in the 20261006 export README (`in_jakarta_bbox`).
LON = (106.68, 106.98)
LAT = (-6.38, -6.08)
TS_COLS = {"station": ["first_seen_utc", "last_seen_utc"], "observation": ["ts_utc", "first_seen_utc"]}
STATION_COLS = ["uuid", "kode", "initial", "name", "type", "lat", "lng", "kecamatan", "kota", "alamat",
                "dioperasikan", "first_seen_utc", "last_seen_utc", "active", "in_jakarta_bbox"]
OBS_COLS = ["station_uuid", "metric", "ts_utc", "value", "qc", "first_seen_utc"]


def read(path: Path, table: str) -> pd.DataFrame:
    # keep_default_na=False: an empty qc must stay "" (valid), not NaN (which the label rules treat as flagged).
    df = pd.read_csv(path, keep_default_na=False, na_values={"value": [""], "lat": [""], "lng": [""]},
                     dtype={"qc": str} if table == "observation" else None, low_memory=False)
    for c in TS_COLS[table]:
        df[c] = pd.to_datetime(df[c].replace("", pd.NA), utc=True, format="ISO8601").astype("datetime64[us, UTC]")
    return df


def in_bbox(st: pd.DataFrame) -> pd.Series:
    return st.lng.between(*LON) & st.lat.between(*LAT)


def sha(p: Path) -> str:
    return hashlib.sha256(p.read_bytes()).hexdigest()


def main(out: Path, sources: list[Path]) -> None:
    out.mkdir(parents=True, exist_ok=False)
    stations, obs = [], []
    for i, src in enumerate(sources):
        st = read(src / "station.csv.gz", "station").assign(_src=i)
        st["active"] = st.active.astype(str).str.lower().isin(["t", "true", "1"])
        stations.append(st)
        obs.append(read(src / "observation.csv.gz", "observation").assign(_src=i))
    st = pd.concat(stations, ignore_index=True)
    # One row per station: keep the newest metadata, widest first/last seen.
    first = st.groupby("uuid").first_seen_utc.min()
    last = st.groupby("uuid").last_seen_utc.max()
    st = st.sort_values(["uuid", "_src", "last_seen_utc"]).drop_duplicates("uuid", keep="last").copy()
    st["first_seen_utc"], st["last_seen_utc"] = st.uuid.map(first), st.uuid.map(last)
    st["lat"], st["lng"] = st.lat.astype(float), st.lng.astype(float)
    st["in_jakarta_bbox"] = in_bbox(st)
    st = st[STATION_COLS].sort_values("kode").reset_index(drop=True)

    ob = pd.concat(obs, ignore_index=True)
    ob["value"] = ob.value.astype(float)
    ob["qc"] = ob.qc.fillna("").astype(str)
    n_all = len(ob)
    ob = ob.sort_values(["station_uuid", "metric", "ts_utc", "_src"]).drop_duplicates(
        ["station_uuid", "metric", "ts_utc"], keep="last")
    ob = ob[OBS_COLS].reset_index(drop=True)
    missing = set(ob.station_uuid) - set(st.uuid)
    if missing:
        raise SystemExit(f"observations for unknown stations: {sorted(missing)[:5]}")

    st.to_parquet(out / "station.parquet", index=False)
    ob.to_parquet(out / "observation.parquet", index=False)
    manifest = {
        "source": "PostgreSQL schema 'ground_truth' (SIPPolusi Data Worker), CSV exports converted to Parquet",
        "inputs": [str(s) for s in sources],
        "snapshot_utc": str(ob.first_seen_utc.max()),
        "in_jakarta_bbox_rule": {"lon": LON, "lat": LAT},
        "duplicates_dropped": int(n_all - len(ob)),
        "files": {
            "station.parquet": {"rows": len(st), "sha256": sha(out / "station.parquet")},
            "observation.parquet": {"rows": len(ob), "min_ts_utc": str(ob.ts_utc.min()),
                                    "max_ts_utc": str(ob.ts_utc.max()), "sha256": sha(out / "observation.parquet")},
        },
    }
    (out / "manifest.json").write_text(json.dumps(manifest, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(manifest, indent=2))


if __name__ == "__main__":
    main(Path(sys.argv[1]), [Path(p) for p in sys.argv[2:]])
