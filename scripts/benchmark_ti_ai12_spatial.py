"""Spatial replay PIC A / Dijkstra benchmark; see TI-AI-12/benchmark-spatial-protocol.md."""
import argparse
import datetime
import gzip
import hashlib
import json
import platform
import statistics
import time
from pathlib import Path

import torch
from src.backend.route_model.astar_osm import get_router
from src.backend.route_model.training_env import EnvConfig, RouteEnv, SplitSpec, load_weights, sample_od_pairs
from src.backend.route_model.morl.evaluate_candidate import measure
from src.backend.route_model.morl.infer import PolicyFailure, fastest_reference, optimal_weighted_route
from src.backend.route_model.morl.scalarized_serving import decode_preference_route, load_scalarized_policy

ROOT = Path(__file__).resolve().parents[1]
GRAPH = ROOT / 'src/data_worker/dataset_processed/OSM'
MODEL = ROOT / 'docs/experiments/TI-AI-09/scalarized/20261103T081938Z-scalarized-bike-v1-6eab87d4/checkpoints/best.pt'
PREVIOUS = ROOT / 'docs/experiments/TI-AI-11/morl-comparison/scenario_od_pairs.json'

def digest(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()

def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--weights', type=Path, required=True)
    parser.add_argument('--out', type=Path, required=True)
    parser.add_argument('--revision', required=True)
    parser.add_argument('--image-id', required=True)
    args = parser.parse_args()
    if args.out.exists():
        raise FileExistsError('Output already exists; use a new run directory')
    expected = 'a066d187c4477f1436dd03f85e3340c5e8c1acc14a14c80ec8eab44b6a765712'
    assert digest(args.weights) == expected, 'unexpected weights'
    assert digest(MODEL) == '28709b934d26d99d1d8e0124409b5aad31ecfceb3780b872aa869614a54a0af6'
    torch.set_num_threads(1)
    router = get_router(GRAPH, 'bike')
    if router.version != 'jakarta-20261005':
        raise ValueError('Unexpected graph')
    model, _ = load_scalarized_policy(MODEL)
    previous = json.loads(PREVIOUS.read_text(encoding='utf-8'))
    used = {(p['start'], p['end']) for group in previous.values() for p in group}
    pairs, sampling = sample_od_pairs(router, SplitSpec(seed=42), {'test': 75}, seed=1212026,
                                      min_distance_m=500, max_distance_m=3000, max_attempts_factor=2000)
    fresh = [p for p in pairs if (p.start, p.end) not in used]
    if len(fresh) < 50:
        raise ValueError('Not enough pairs after exclusions')
    pairs = fresh[:50]
    weights = load_weights(args.weights, router.version, '2026-11-13T09:00:00Z')
    env = RouteEnv(router, weights, pairs, EnvConfig(split='test'), window_splits={'test': [weights.time_window]})
    args.out.mkdir(parents=True)
    manifest = {'revision': args.revision, 'image_id': args.image_id, 'started_at': datetime.datetime.now(datetime.timezone.utc).isoformat(),
                'python': platform.python_version(), 'platform': platform.platform(), 'torch': torch.__version__,
                'threads': torch.get_num_threads(), 'weights_sha256': digest(args.weights), 'model_sha256': digest(MODEL),
                'graph_sha256': {p.name: digest(p) for p in (GRAPH / 'graph_version.csv', GRAPH / 'road_node.csv.gz', GRAPH / 'road_edge.csv.gz')},
                'previous_pairs_sha256': digest(PREVIOUS), 'sampling': sampling, 'excluded_previous': 75-len(fresh),
                'imputed_edge_count': len(env.imputed_edges), 'pairs': [vars(p) for p in pairs]}
    (args.out / 'manifest.json').write_text(json.dumps(manifest, indent=2) + '\n', encoding='utf-8')
    records = []
    for i, od in enumerate(pairs):
        for alpha in (0.0, 0.5, 1.0):
            record = {'od_id': od.od_id, 'alpha': alpha}
            def policy():
                route = decode_preference_route(model, router, env.exposure, od.start, od.end, (alpha, 1-alpha), max_steps=400, max_steps_factor=3.0)
                return list(route.edge_ids)
            def dijkstra():
                _, t, e = fastest_reference(router, env.exposure, od.start, od.end)
                edges, _ = optimal_weighted_route(router, env.exposure, od.start, od.end, alpha, 1-alpha, t, e)
                return edges
            methods = [('policy', policy), ('dijkstra', dijkstra)]
            if (i * 3 + int(alpha * 2)) % 2:
                methods.reverse()
            for name, fn in methods:
                start = time.perf_counter()
                try:
                    edges = fn()
                    elapsed = (time.perf_counter()-start)*1000
                    result = measure(router, env.exposure, edges, od.start, od.end) if edges else {'reached': False, 'failure': 'no_route'}
                except PolicyFailure as exc:
                    elapsed = (time.perf_counter()-start)*1000
                    result = {'reached': False, 'failure': exc.reason}
                result['elapsed_ms'] = elapsed
                record[name] = result
            records.append(record)
        print(f'{i+1}/50 pairs', flush=True)
    with gzip.open(args.out / 'scenarios.jsonl.gz', 'wt', encoding='utf-8', newline='\n') as stream:
        for record in records:
            stream.write(json.dumps(record, allow_nan=False) + '\n')
    summary = {}
    for method in ('policy', 'dijkstra'):
        times = sorted(r[method]['elapsed_ms'] for r in records)
        summary[method] = {'requests': len(records), 'success': sum(r[method]['reached'] for r in records),
                           'invalid': sum(r[method].get('valid') is False for r in records),
                           'median_ms': statistics.median(times), 'p95_ms': times[int(len(times)*0.95)-1], 'max_ms': max(times)}
    (args.out / 'summary.json').write_text(json.dumps(summary, indent=2) + '\n', encoding='utf-8')

if __name__ == '__main__':
    main()
