"""Compare source and registry-resolved spatial package inference in a clean worker image."""
import hashlib
import json
import platform
import tempfile
from pathlib import Path

import numpy as np
import pandas as pd
from spatial_model.artifacts.packaging import runtime_contract
from spatial_model.artifacts.registry import resolve
from spatial_model.data.grid import GridSpec
from spatial_model.inference.serving import GridInputs, load_model, predict_grid

dataset = Path('/snapshot')
nodes = pd.read_parquet(dataset / 'stgnn/nodes.parquet')
rc = nodes.grid_id.str.extract(r'r(\d+)_c(\d+)').astype(int).to_numpy()
available = set(map(tuple, rc))
chosen = next(((r, c, 2) for r, c in rc if all((i, j) in available
               for i in range(r-1, r+2) for j in range(c-1, c+3))), None)
if chosen is None:
    chosen = next((r, c, 1) for r, c in rc if all((i, j) in available
                  for i in range(r-1, r+2) for j in range(c-1, c+2)))
row, col, width = map(int, chosen)
spec = GridSpec(x0=676900+100*col, y0=9336600-100*(row+1), nx=width, ny=1)
features = pd.read_parquet(dataset / 'stgnn/features.parquet')


def reader(ids, start, end):
    return features[features.grid_id.isin(ids) & features.time_utc.ge(start) & features.time_utc.lt(end)].copy()


labels = pd.read_parquet(dataset / 'labels.parquet')
end = pd.Timestamp('2026-09-29T12:00Z')
readings = labels[labels.time_utc.between(end-pd.Timedelta(hours=23), end)][
    ['time_utc', 'station_uuid', 'pm25_ugm3', 'no2_ugm3']]
results = {}
with tempfile.TemporaryDirectory() as cache:
    for target, folder in (('pm25', '01-pm25'), ('no2', '02-no2')):
        source = Path('/reference') / folder
        resolved, identity = resolve(target, 'production', cache_dir=cache)
        source_hash = hashlib.sha256((source/'package.json').read_bytes()).hexdigest()
        resolved_hash = hashlib.sha256((resolved/'package.json').read_bytes()).hexdigest()
        if source_hash != resolved_hash:
            raise AssertionError(f'{target} registry package differs from the source package')
        reference_model = load_model(source, target, runtime_contract())
        reloaded = load_model(resolved, target, runtime_contract())
        expected = predict_grid(reference_model, GridInputs(reader, readings), spec, source, end)
        actual = predict_grid(reloaded, GridInputs(reader, readings), spec, resolved, end)
        if not np.isfinite(expected).all() or not np.isfinite(actual).all():
            raise AssertionError(f'{target} produced a non-finite prediction')
        if not np.allclose(expected, actual, rtol=1e-6, atol=1e-6):
            raise AssertionError(f'{target} inference differs after registry reload')
        results[target] = {'model': identity, 'compatibility': reloaded.compatibility.as_dict(),
                           'package_sha256': resolved_hash, 'source_package_sha256': source_hash,
                           'reference_predictions_ugm3': expected.tolist(),
                           'reloaded_predictions_ugm3': actual.tolist(),
                           'max_absolute_difference': float(np.max(np.abs(expected-actual)))}
print(json.dumps({'result': 'pass', 'comparison': 'source package vs independently registry-resolved package',
                  'dataset': 'ds-v0.3.0', 'window': end.isoformat(), 'grid': {'row': row, 'col': col, 'width': width},
                  'tolerance': {'rtol': 1e-6, 'atol': 1e-6}, 'models': results,
                  'snapshot_sha256': {name: hashlib.sha256((dataset/name).read_bytes()).hexdigest() for name in ('stgnn/nodes.parquet', 'stgnn/features.parquet', 'labels.parquet')},
                  'python': platform.python_version()}, indent=2, allow_nan=False))
