"""SourceReader snapshots source files once across changing serving tile requests."""
from collections import Counter
import json
from pathlib import Path

import numpy as np
import pandas as pd
import pytest

from feature_matrix import sources


@pytest.fixture
def source_tree(tmp_path, monkeypatch):
    ids = ["r0200_c0200", "r0216_c0216", "r0232_c0232"]
    cells = sources.active_cells().set_index("grid_id").loc[ids].reset_index()
    monkeypatch.setattr(sources, "active_cells", lambda: cells.copy())
    def save(name, frame):
        path = tmp_path / name
        path.parent.mkdir(parents=True, exist_ok=True)
        for col in frame.select_dtypes(include="datetimetz"):
            frame[col] = frame[col].astype("datetime64[ns, UTC]")
        frame.to_parquet(path, index=False)
    early = pd.Timestamp("2026-09-19T00:00Z")
    for day in (18, 19):
        frame = pd.DataFrame(dict(grid_id=ids, scene_id=f"landsat-{day}",
            observed_at=pd.Timestamp(f"2026-09-{day}T00:00Z"), produced_at=early,
            ndvi=[.1, .2, .3], ndbi=[.4, .5, .6], lst_c=[25., 26., 27.],
            ndvi_available=True, ndbi_available=True, lst_c_available=True))
        save(f"landsat/scenes/{day}.parquet", frame)
    viirs = pd.DataFrame(dict(viirs_cell_id=sources.viirs_tile_cell_ids(cells.longitude, cells.latitude),
        observation_date_utc=early, longitude=cells.longitude, latitude=cells.latitude,
        ntl=[1., 2., 3.], ntl_available=True, observed_end_at=early, produced_at=early))
    save("viirs/daily.parquet", viirs)
    (tmp_path / "viirs/manifest.json").write_text(json.dumps({"inputs": [
        {"observation_date": "2026-09-19", "scene_id": "viirs-19"}]}))
    s5p = pd.DataFrame(dict(grid_id=sources.s5p_cell_ids(cells.easting, cells.northing),
        no2_mol_m2=0.001, available=True, observed_at=early, produced_at=early, scene_id="s5p"))
    save("sentinel5p/observations.parquet", s5p.drop_duplicates("grid_id"))
    positions = cells.grid_id.str.extract(r"r(\d+)_c(\d+)").astype(int)
    save("geos-cf/grid100m_geoscf_lookup.parquet", pd.DataFrame(dict(
        gx=positions[1], gy=444-positions[0], geoscf_cell_id=[101, 102, 103])))
    geos = pd.DataFrame(dict(cell_id=[101, 102, 103], time_window_start=early,
        available_at_utc=early+pd.Timedelta(hours=2), latency_hours=1.,
        pm25_ugm3=[10., 20., 30.], no2_ugm3=[40., 50., 60.], available_pm25=True, available_no2=True))
    save("geos-cf/geoscf_cell_hourly.parquet", geos)
    fcst = geos.drop(columns="latency_hours").assign(init_utc=early,
        time_window_start=pd.Timestamp("2026-09-20T11:00Z"), lead_hours=35.)
    save("geos-cf/geoscf_fcst_cell_hourly.parquet", fcst)
    weather = pd.DataFrame(dict(time_utc=pd.date_range("2026-09-20T00:00Z", periods=6, freq="h")))
    for feature in sources.WEATHER:
        weather[feature] = 1.
        weather[f"{feature}_available"] = True
    save("open_meteo/hourly.parquet", weather)
    return tmp_path, ids


def read(reader, ids):
    return reader(ids, "2026-09-20T10:00Z", "2026-09-20T13:00Z")


def test_changing_tiles_read_each_source_parquet_once(source_tree, monkeypatch):
    root, ids = source_tree
    reader = sources.SourceReader(root)
    counts = Counter()
    real_read = pd.read_parquet
    def counted(path, *args, **kwargs):
        counts[Path(path).relative_to(root).as_posix()] += 1
        return real_read(path, *args, **kwargs)
    monkeypatch.setattr(pd, "read_parquet", counted)
    for subset in ([ids[0]], ids[1:], list(reversed(ids)), [ids[0]]):
        read(reader, subset)
        reader.cell_mapping(subset)
    assert set(counts) == {p.relative_to(root).as_posix() for p in root.rglob("*.parquet")}
    assert all(count == 1 for count in counts.values()), dict(counts)


@pytest.mark.parametrize("rule", sources.VIIRS_RULES)
def test_warm_tiles_preserve_cold_results_order_and_no_aliasing(source_tree, rule):
    root, ids = source_tree
    reader = sources.SourceReader(root, viirs_cell_rule=rule)
    read(reader, [ids[0]])
    subset = list(reversed(ids))
    expected = read(sources.SourceReader(root, viirs_cell_rule=rule), subset)
    actual = read(reader, subset)
    pd.testing.assert_frame_equal(actual, expected)
    assert actual.grid_id.iloc[:3].tolist() == subset
    actual.loc[:, "ntl"] = -100.
    mapping = reader.cell_mapping(subset)
    mapping.loc[:, "viirs_cell_id"] = "changed"
    pd.testing.assert_frame_equal(read(reader, subset), expected)


def test_unknown_grid_is_rejected_before_source_io(source_tree, monkeypatch):
    root, _ = source_tree
    reader = sources.SourceReader(root)
    def fail(*args, **kwargs):
        raise AssertionError("Unexpected source I/O")
    monkeypatch.setattr(pd, "read_parquet", fail)
    with pytest.raises(ValueError, match="di luar sel aktif"):
        read(reader, ["r0000_c0000"])


def test_snapshot_is_stable_and_new_reader_sees_updates(source_tree):
    root, ids = source_tree
    reader = sources.SourceReader(root)
    expected = read(reader, ids)
    path = root / "viirs/daily.parquet"
    changed = pd.read_parquet(path)
    changed["ntl"] += 10
    changed.to_parquet(path, index=False)
    read(reader, ids[:1])  # Force a different tile, beyond the last-request cache.
    pd.testing.assert_frame_equal(read(reader, ids), expected)
    fresh = read(sources.SourceReader(root), ids)
    np.testing.assert_array_equal(fresh.ntl, expected.ntl + 10)


def test_failed_load_can_retry_without_partial_snapshot(source_tree):
    root, ids = source_tree
    reader = sources.SourceReader(root)
    path = root / "geos-cf/geoscf_fcst_cell_hourly.parquet"
    hidden = path.with_suffix(".hidden")
    path.rename(hidden)
    try:
        with pytest.raises(FileNotFoundError):
            read(reader, ids)
        assert reader._source_cache is None
    finally:
        hidden.rename(path)
    pd.testing.assert_frame_equal(read(reader, ids), read(sources.SourceReader(root), ids))


def test_missing_source_cells_stay_missing(source_tree):
    root, ids = source_tree
    for path in (root / "landsat/scenes").glob("*.parquet"):
        frame = pd.read_parquet(path)
        frame[frame.grid_id.ne(ids[0])].to_parquet(path, index=False)
    path = root / "viirs/daily.parquet"
    frame = pd.read_parquet(path)
    frame.iloc[1:].to_parquet(path, index=False)
    reader = sources.SourceReader(root)
    read(reader, ids[1:])
    result = read(reader, ids[:1])
    assert result[[*sources.LANDSAT_FEATURES, "ntl"]].isna().all().all()
    assert result.geoscf_pm25_ugm3.notna().all()


def test_viirs_manifest_validation_applies_only_to_requested_cells(source_tree):
    root, ids = source_tree
    path = root / "viirs/daily.parquet"
    frame = pd.read_parquet(path)
    frame.loc[0, "observation_date_utc"] += pd.Timedelta(days=1)
    frame.to_parquet(path, index=False)
    reader = sources.SourceReader(root)
    read(reader, ids[1:])
    with pytest.raises(ValueError, match="Tanggal VIIRS"):
        read(reader, ids[:1])
    pd.testing.assert_frame_equal(read(reader, ids[1:]), read(sources.SourceReader(root), ids[1:]))
