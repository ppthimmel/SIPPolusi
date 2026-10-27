"""Verify real bundles: MLflow/MinIO → inference → PostGIS → backend readback.

Uses the isolated docker-compose.spatial-verification.yml stack and refuses remote databases.
Credentials must be supplied through environment variables, never printed.
"""
from __future__ import annotations

import argparse
import asyncio
import dataclasses
import json
import os
import sys
from pathlib import Path
from urllib.parse import urlparse

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src/data_worker"))


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--pm25", type=Path, required=True)
    parser.add_argument("--no2", type=Path, required=True)
    parser.add_argument("--out", type=Path, default=ROOT / "docs/experiments/TI-AI-05/registered_verification.json")
    args = parser.parse_args()
    # Load PyTorch before other native numerical runtimes on Windows.
    import torch
    import numpy as np
    import pandas as pd
    import psycopg
    from sqlalchemy import create_engine
    from database import init_db
    from spatial_model.artifacts.registry import register, promote, resolve
    from spatial_model.inference.serving import GridInputs, load_model
    from spatial_model.data.grid import GridSpec, to_lonlat
    from spatial_model.inference import run_downscale_inference, load_idw_settings
    from spatial_model.inference.provenance import read_edge_trace
    from contracts import TimeWindow
    from spatial_model.artifacts.bundles import export_bundle

    dsn = os.environ["TEST_DATABASE_URL"]
    parsed = urlparse(dsn)
    if parsed.hostname not in ("127.0.0.1", "localhost") or parsed.path != "/spatial_test":
        raise ValueError("Verification requires the isolated local spatial_test database")
    snapshot = ROOT / "docs/experiments/ds-v0.3.0"
    # Refresh the PM2.5 export with final source hashes and recorded evaluation artifacts.
    checkpoint = torch.load(args.pm25 / "best.pt", map_location="cpu", weights_only=True)
    pm_bundle = export_bundle(args.pm25, "pm25", snapshot, checkpoint["metadata"])
    versions = {"pm25": register(pm_bundle), "no2": register(args.no2 / "bundle")}
    for target, version in versions.items():
        promote(target, version["version"])
    os.environ["SPATIAL_MODEL_CACHE"] = str(ROOT / ".cache/registered-verification-fresh")
    for target in versions:
        path, _ = resolve(target)
        load_model(path, target)

    nodes = pd.read_parquet(snapshot / "stgnn/nodes.parquet")
    rc = nodes.grid_id.str.extract(r"r(\d+)_c(\d+)").astype(int).to_numpy()
    available = set(map(tuple, rc))
    # Two adjacent interior cells plus the complete one-hop halo.
    chosen = next(((r,c,2) for r,c in rc if all((i,j) in available for i in range(r-1,r+2) for j in range(c-1,c+3))), None)
    if chosen is None:
        chosen = next((r,c,1) for r,c in rc if all((i,j) in available for i in range(r-1,r+2) for j in range(c-1,c+2)))
    row, col, width = map(int, chosen)
    spec = GridSpec(x0=676900+100*col, y0=9336600-100*(row+1), nx=width, ny=1)
    features = pd.read_parquet(snapshot / "stgnn/features.parquet")
    def reader(ids, start, end):
        return features[features.grid_id.isin(ids) & features.time_utc.ge(start) & features.time_utc.lt(end)].copy()
    labels = pd.read_parquet(snapshot / "labels.parquet")
    end = pd.Timestamp("2026-09-29T12:00Z")
    readings = labels[labels.time_utc.between(end-pd.Timedelta(hours=23),end)][["time_utc","station_uuid","pm25_ugm3","no2_ugm3"]]
    inputs = GridInputs(reader, readings)
    stations = pd.read_parquet(snapshot / "stations.parquet").set_index("uuid")
    current = readings[readings.time_utc.eq(end)].set_index("station_uuid")
    truth = []
    for station, record in current.iterrows():
        for pollutant in ("pm25","no2"):
            if pd.notna(record[f"{pollutant}_ugm3"]):
                st = stations.loc[station]
                truth.append(dict(station_id=station,lon=st.lng,lat=st.lat,pollutant=pollutant,value=record[f"{pollutant}_ugm3"]))
    ground_truth = pd.DataFrame(truth)
    xs = np.array([spec.x0+10, spec.x0+100*width-10])
    ys = np.full(2,spec.y0+50)
    lon, lat = to_lonlat(xs,ys)
    coordinates = np.column_stack([lon,lat])
    edge_id, graph_version = 987654321, "registered-model-verification"
    edges = (np.array([edge_id]), coordinates, np.zeros(2,dtype=int))
    window = TimeWindow.starting_at((end-pd.Timedelta(hours=1)).to_pydatetime())
    init_db(create_engine(dsn.replace("postgresql://","postgresql+psycopg2://",1)))
    with psycopg.connect(dsn,autocommit=True) as conn:
        wkt = "LINESTRING("+", ".join(f"{x} {y}" for x,y in coordinates)+")"
        conn.execute("INSERT INTO osm.graph_version(version,status) VALUES (%s,'active') ON CONFLICT(version) DO NOTHING",(graph_version,))
        conn.execute("INSERT INTO osm.road_edge(edge_id,graph_version,u,v,length_m,walk_allowed,bike_allowed,geom) VALUES (%s,%s,1,2,%s,true,true,ST_GeomFromText(%s,4326)) ON CONFLICT DO NOTHING",(edge_id,graph_version,float(xs[-1]-xs[0]),wkt))
        kwargs = dict(ground_truth=ground_truth,edges=edges,graph_version=graph_version,grid_spec=spec,
                      conn=conn,settings=load_idw_settings(),inputs=inputs,artifact_dir=None)
        first = run_downscale_inference(window,force=True,**kwargs)
        assert first.rows_written == 1
        assert first.model_metadata['pm25']['source'] == 'idw_stgnn_residual', first.model_metadata
        assert first.model_metadata['no2']['source'] == 'lightgbm', first.model_metadata
        second = run_downscale_inference(window,**kwargs)
        assert second.status == 'skipped' and second.run_id == first.run_id
        assert first.artifacts == {}
        trace = read_edge_trace(conn, edge_id, window.start)
        assert trace['run_id'] == first.run_id and len(trace['cells']) == width
        assert trace['manifest']['input_fingerprint'] == first.input_fingerprint
        cells = pd.DataFrame(trace['cells'])
        reconstructed = {}
        for pollutant in ('pm25', 'no2'):
            valid = cells[f'{pollutant}_ugm3'].notna()
            reconstructed[pollutant] = float(np.average(cells.loc[valid, f'{pollutant}_ugm3'], weights=cells.loc[valid, 'length_m']))

    async def readback():
        sys.path.append(str(ROOT / "src/backend"))
        from osm.db import OsmDatabase
        from osm.config import OsmSettings
        from osm.models import BBox
        from pollution.repository import PollutionRepository
        from pollution.models import TimeWindow as BackendWindow
        db=OsmDatabase(OsmSettings(DATABASE_URL=dsn))
        await db.start()
        try:
            result=await PollutionRepository(db,None).read_pollution_weight(
                BBox(float(lon.min()-.001),float(lat.min()-.001),float(lon.max()+.001),float(lat.max()+.001)),
                BackendWindow(window.start),graph_version=graph_version)
            assert len(result.weights)==1
            weight=result.weights[0]
            assert weight.model_metadata['no2']['source']=='lightgbm'
            assert weight.trace_run_id == first.run_id
            backend_trace = await PollutionRepository(db,None).read_edge_trace(edge_id, BackendWindow(window.start))
            assert backend_trace['run_id'] == first.run_id and len(backend_trace['cells']) == width
            for pollutant in ('pm25', 'no2'):
                assert abs(getattr(weight, f'{pollutant}_ugm3') - reconstructed[pollutant]) < 0.001
            return dataclasses.asdict(weight), backend_trace
        finally:
            await db.close()
    weight, backend_trace=asyncio.run(readback())
    report=dict(status='passed',dataset='ds-v0.3.0',registered_versions=versions,
                window_start=window.start.isoformat(),grid_cells=spec.n_cells,
                run=dataclasses.asdict(first),repeat_status=second.status,backend_readback=weight,
                durable_trace=backend_trace, reconstructed_concentrations=reconstructed,
                optional_artifacts_enabled=False,
                limits='Historical local replay on a small grid; not citywide accuracy or Railway deployment.')
    args.out.parent.mkdir(parents=True,exist_ok=True)
    args.out.write_text(json.dumps(report,indent=2,default=str)+'\n')
    print(f"Verified both registry models through backend readback: {args.out}")


if __name__ == '__main__':
    main()
