"""Serving invariants for the pollutant-specific registered models."""
import json
from pathlib import Path
from types import SimpleNamespace

import torch
import numpy as np
import pandas as pd
import pytest

from spatial_model.models.stgnn.model import ModelConfig, STGNN
from spatial_model.models.stgnn.preprocessing import Preprocessor, TEMPORAL_COLUMNS, LOCAL_COLUMNS
from spatial_model.models.lightgbm.model import predict
from spatial_model.inference.serving import GridInputs
from spatial_model.artifacts.bundles import validate_bundle


def test_registry_accepts_serving_status_without_inventing_metrics(tmp_path, monkeypatch):
    """Serving fits can be registered even though they have no held-out score."""
    import sys
    from types import ModuleType
    from contextlib import nullcontext
    from spatial_model.artifacts import registry

    (tmp_path / "bundle.json").write_text("{}")
    (tmp_path / "metrics.json").write_text(json.dumps({
        "status": "not_evaluated", "reason": "Serving fit", "val": {"rmse": 3.5}}))
    manifest = dict(target="no2", dataset="ds-v0.3.0", code_revision="test", metadata={})
    monkeypatch.setattr(registry, "validate_bundle", lambda *args: manifest)
    logged = []
    mlflow = ModuleType("mlflow")
    for name in ("set_tracking_uri", "set_experiment", "set_tags", "log_params", "log_artifacts"):
        setattr(mlflow, name, lambda *args, **kwargs: None)
    mlflow.log_metrics = lambda values: logged.append(values)
    mlflow.start_run = lambda: nullcontext(SimpleNamespace(info=SimpleNamespace(artifact_uri="s3://test", run_id="run")))
    exceptions = ModuleType("mlflow.exceptions")
    exceptions.MlflowException = type("MlflowException", (Exception,), {})
    monkeypatch.setitem(sys.modules, "mlflow", mlflow)
    monkeypatch.setitem(sys.modules, "mlflow.exceptions", exceptions)
    client = SimpleNamespace(create_registered_model=lambda *args: None,
        create_model_version=lambda *args, **kwargs: SimpleNamespace(version="1"),
        set_registered_model_alias=lambda *args: None)
    monkeypatch.setattr(registry, "client", lambda: client)
    assert registry.register(tmp_path)["version"] == "1"
    assert logged == [{"val.rmse": 3.5}]


def test_zero_head_is_exact_idw_and_reload(tmp_path):
    model = STGNN(ModelConfig(temporal_channels=58, local_channels=8, hidden_channels=16,
                             local_hidden_channels=8, heads=2, dropout=0.2)).eval()
    torch.nn.init.zeros_(model.regression[-1].weight)
    torch.nn.init.zeros_(model.regression[-1].bias)
    temporal = torch.randn(1, 5, 24, 58)
    local = torch.randn(1, 5, 8)
    edges = torch.tensor([[0,1,2,3], [1,2,3,4]])
    with torch.no_grad():
        z = model(temporal, local, edges).numpy()[0,:,0]
    idw = np.arange(5.) + 10
    np.testing.assert_array_equal(np.maximum(idw + 20*z, 0), idw)
    torch.save(model.state_dict(), tmp_path/'weights.pt')
    model.load_state_dict(torch.load(tmp_path/'weights.pt', weights_only=True))
    with torch.no_grad():
        np.testing.assert_array_equal(model(temporal,local,edges).numpy()[0,:,0], z)


def test_feature_order_and_missingness():
    temporal = np.ones((24,3,len(TEMPORAL_COLUMNS)))
    local = np.ones((24,3,len(LOCAL_COLUMNS)))
    preprocessing = Preprocessor().fit(temporal, local, [1,2,3])
    temporal[0,0,0] = np.nan
    assert np.isfinite(preprocessing.transform(temporal,local)[0]).all()
    with pytest.raises(ValueError, match="order"):
        preprocessing.transform(temporal,local,temporal_columns=TEMPORAL_COLUMNS[::-1])


@pytest.mark.parametrize('transform,expected', [('direct',2),('resid_mean',12),('resid_idw',22),('resid_idw_p1all',32)])
def test_lightgbm_target_reconstruction(transform, expected):
    frame = pd.DataFrame(dict(src_mean=[10.], idw_p2_k8=[20.], idw_p1_all=[30.]))
    model = SimpleNamespace(predict=lambda data: np.array([2.]))
    result = predict(model, dict(columns=list(frame), target_transform=transform), frame)
    assert result[0] == expected


def test_bundle_rejects_missing_payload(tmp_path):
    (tmp_path/'bundle.json').write_text(json.dumps(dict(format_version=1,dataset='ds-v0.3.0',unit='ug/m3',target='no2',hashes={})))
    with pytest.raises(ValueError, match='Incomplete'):
        validate_bundle(tmp_path)


def test_grid_inputs_reject_future_provenance():
    end = pd.Timestamp('2026-09-29T12:00Z')
    readings = pd.DataFrame(dict(time_utc=[end], station_uuid=['s'], no2_ugm3=[1.]))
    def reader(ids,start,stop):
        return pd.DataFrame(dict(grid_id=['r0001_c0001'],time_utc=[end],easting=[1.],
                                 weather_available_at_utc=[end+pd.Timedelta(hours=1)]))
    provider = GridInputs(reader,readings)
    nodes = pd.DataFrame(dict(grid_id=['r0001_c0001']))
    stations = pd.DataFrame(dict(grid_id=['r0001_c0001'],uuid=['s']))
    with pytest.raises(ValueError,match='Future'):
        provider.frames(nodes,stations,'no2',end)


def test_single_gat_halo_matches_full_graph():
    torch.manual_seed(42)
    model = STGNN(ModelConfig(temporal_channels=58, local_channels=8, hidden_channels=16,
                             local_hidden_channels=8,heads=2,dropout=0.0)).eval()
    t,s = torch.randn(1,5,24,58), torch.randn(1,5,8)
    edges=torch.tensor([[0,1,1,2,2,3,3,4],[1,0,2,1,3,2,4,3]])
    with torch.no_grad():
        full=model(t,s,edges)[0,2]
        halo=model(t[:,1:4],s[:,1:4],torch.tensor([[0,1,1,2],[1,0,2,1]]))[0,1]
    torch.testing.assert_close(full,halo)


def test_independent_fallback_and_input_identity(monkeypatch):
    from contracts import TimeWindow
    from spatial_model.data.grid import GridSpec
    from spatial_model.inference.runtime import infer_registered
    from spatial_model.inference import serving
    gt = pd.DataFrame(dict(station_id=['s','s'],lon=[106.8,106.8],lat=[-6.2,-6.2],pollutant=['pm25','no2'],value=[10.,20.]))
    spec=GridSpec.from_bbox((106.8,-6.2,106.801,-6.199))
    def resolve(target, selector):
        if target=='no2':
            raise ConnectionError('registry unavailable')
        return Path('fixture'), 'pm25:1'
    monkeypatch.setattr(serving,'load_model',lambda *args: SimpleNamespace(metadata={'estimation_source':'idw_stgnn_residual'}))
    monkeypatch.setattr(serving,'predict_grid',lambda *args: np.full(spec.n_cells,12.))
    inputs=SimpleNamespace(readings=pd.DataFrame({'x':[1]}),fingerprint='features1')
    start = pd.Timestamp('2026-09-29T12:00Z').to_pydatetime()
    args=(TimeWindow(start, start+pd.Timedelta(hours=1)),gt,spec,{'power':2,'neighbors':8})
    grid,meta,identity,_=infer_registered(*args,inputs=inputs,resolver=resolve)
    assert meta['pm25']['source']=='idw_stgnn_residual'
    assert meta['no2']['source']=='idw'
    assert (grid.pm25_ugm3==12).all() and (grid.no2_ugm3==20).all()
    inputs.fingerprint='features2'
    assert infer_registered(*args,inputs=inputs,resolver=resolve)[2] != identity
    inputs.fingerprint='features1'
    def resolve_v2(target, selector):
        if target=='no2':
            raise ConnectionError('registry unavailable')
        return Path('fixture'), 'pm25:2'
    assert infer_registered(*args,inputs=inputs,resolver=resolve_v2)[2] != identity


def test_registry_rejects_corrupt_cached_artifact(tmp_path, monkeypatch):
    from spatial_model.artifacts import registry
    from spatial_model.artifacts.bundles import digest
    root=tmp_path/'bundle'
    root.mkdir()
    (root/'model.txt').write_text('payload')
    (root/'metadata.json').write_text('{}')
    (root/'stations.parquet').write_text('station fixture')
    manifest=dict(format_version=1,dataset='ds-v0.3.0',unit='ug/m3',target='no2',
                  hashes={p.name:digest(p) for p in root.iterdir()})
    (root/'bundle.json').write_text(json.dumps(manifest))
    expected=digest(root/'bundle.json')
    target=tmp_path/registry.NAMES['no2']/'1'/expected
    import shutil
    shutil.copytree(root,target)
    (target/'model.txt').write_text('corrupted')
    calls=[]
    class Client:
        def get_model_version_by_alias(self,name,selector):
            calls.append((name,selector))
            return SimpleNamespace(version='1',tags={'bundle_sha256':expected})
    monkeypatch.setattr(registry,'client',lambda:Client())
    with pytest.raises(ValueError,match='integrity'):
        registry.resolve('no2',cache_dir=tmp_path)
    assert len(calls)==1


@pytest.mark.parametrize('target,package', [('pm25','stgnn'),('no2','lightgbm')])
def test_notebook_uses_shared_training_api(target,package,monkeypatch):
    import importlib
    module=importlib.import_module(f'spatial_model.models.{package}.train')
    calls=[]
    monkeypatch.setattr(module,'train',lambda *args,**kwargs:calls.append((args,kwargs)) or Path('bundle'))
    root=Path(__file__).resolve().parents[3]
    notebook=json.loads((root/f'notebooks/spatial_model/train_{package}.ipynb').read_text(encoding='utf-8'))
    scope=dict(SNAPSHOT=root/'docs/experiments/ds-v0.3.0',OUTPUT=Path('out'))
    exec(''.join(notebook['cells'][2]['source']),scope)
    assert calls[0][0]==(scope['SNAPSHOT'],scope['OUTPUT'])
    assert calls[0][1]['seed']==42
