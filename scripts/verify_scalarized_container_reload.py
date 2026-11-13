"""Compare a registry-resolved PIC A package with recorded reference routes."""
import hashlib
import json
import os
import platform
import tempfile
from pathlib import Path

import torch
from public_api.data_contracts import CONTRACT_VERSION
from route_model.astar_osm import get_router
from route_model.model_package import PackageIncompatible
from route_model.morl.packaging import load_packaged_scalarized_policy, runtime_contract
from route_model.morl.scalarized_serving import decode_preference_route
from route_model.training_env import EnvConfig, ODPair, RouteEnv, load_weights
from solver.registry import RegistryClient

torch.set_num_threads(1)
fixture = Path('/reference')
cases = json.loads((fixture / 'cases.json').read_text(encoding='utf-8'))
with tempfile.TemporaryDirectory() as cache:
    resolved = RegistryClient(os.environ['MLFLOW_TRACKING_URI']).resolve('morl-route-policy', 'production', cache)
    model, package, compatibility = load_packaged_scalarized_policy(
        resolved.directory, runtime_contract(cases['graph_version'], CONTRACT_VERSION))
    if package['inference']['policy_family'] != 'preference_conditioned':
        raise ValueError('Local production alias does not resolve to PIC A')
    rejected = []
    for label, runtime in (('wrong_graph', runtime_contract('another-graph', CONTRACT_VERSION)),
                           ('wrong_major', runtime_contract(cases['graph_version'], '2.0.0'))):
        try:
            load_packaged_scalarized_policy(resolved.directory, runtime)
        except PackageIncompatible:
            rejected.append(label)
        else:
            raise AssertionError(f'{label} was not rejected')
    _, _, minor = load_packaged_scalarized_policy(resolved.directory, runtime_contract(cases['graph_version'], '1.99.0'))
    if minor.status != 'flagged':
        raise AssertionError('Minor contract mismatch was not flagged')
    router = get_router('/graph', 'bike')
    if router.version != cases['graph_version']:
        raise ValueError('Reference graph version differs')
    weights = load_weights(fixture / 'weights.csv', router.version, cases['window'])
    pairs = {c['od_id']: ODPair(c['od_id'], c['start'], c['end'], 'test') for c in cases['cases']}
    env = RouteEnv(router, weights, list(pairs.values()), EnvConfig(split='test'),
                   window_splits={'test': [cases['window']]})
    results = []
    for case in cases['cases']:
        alpha = case['alpha']
        route = decode_preference_route(model, router, env.exposure, case['start'], case['end'],
                                        (alpha, 1-alpha), max_steps=400, max_steps_factor=3.0)
        digest = hashlib.sha256(json.dumps(list(route.edge_ids)).encode()).hexdigest()[:16]
        if digest != case['reference_route_sha256']:
            raise AssertionError(f"Reference route differs for {case['od_id']} alpha {alpha}")
        results.append({'od_id': case['od_id'], 'alpha': alpha, 'edges': len(route.edge_ids),
                        'reference_route_sha256': case['reference_route_sha256'], 'actual_route_sha256': digest})
    print(json.dumps({'result': 'pass', 'model': resolved.identity, 'package_sha256': resolved.package_sha256,
                      'family': package['inference']['policy_family'], 'compatibility': compatibility.as_dict(),
                      'mismatch_checks': {'rejected': rejected, 'minor_version': minor.status},
                      'graph_version': router.version, 'window': cases['window'],
                      'reference_field_sha256': cases['source_field_sha256'], 'cases': results,
                      'python': platform.python_version(), 'torch': torch.__version__}, indent=2))
