"""Prepare real-field PIC A reference cases for verify_scalarized_container_reload.py."""
import argparse
import gzip
import hashlib
import json
from pathlib import Path
import pandas as pd

parser = argparse.ArgumentParser(description=__doc__)
parser.add_argument('--fields', type=Path, required=True, help='original fields from the recorded comparison')
parser.add_argument('--out', type=Path, required=True, help='new output directory')
args = parser.parse_args()
repo = Path(__file__).resolve().parents[1]
evidence = repo / 'docs/experiments/TI-AI-11/morl-comparison'
manifest = json.loads((evidence / 'manifest.json').read_text(encoding='utf-8'))
fields = json.loads((args.fields / 'manifest.json').read_text(encoding='utf-8'))
window = fields['window_splits']['test'][0]
field = args.fields / fields['windows'][window]['file']
actual = hashlib.sha256(field.read_bytes()).hexdigest()
if actual != manifest['field_files_sha256'][window]:
    raise ValueError('Reference field hash does not match recorded comparison')
with gzip.open(evidence / 'scenarios.jsonl.gz', 'rt', encoding='utf-8') as stream:
    selected = [r for line in stream if (r := json.loads(line))['stratum'] == 'trained-range'
                and r['fields'] == 'real' and r['window'] == window and r['alpha'] in (0.0, 0.5, 1.0)
                and r['methods']['scalarized']['route']['reached']]
ids = list(dict.fromkeys(r['od_id'] for r in selected))
ids = [item for item in ids if sum(r['od_id'] == item for r in selected) == 3][:2]
cases = [{k: r[k] for k in ('od_id', 'start', 'end', 'window', 'alpha')} |
         {'reference_route_sha256': r['methods']['scalarized']['route']['route_sha256']}
         for r in selected if r['od_id'] in ids]
if len(cases) != 6:
    raise ValueError('Could not find two complete successful reference routes')
args.out.mkdir(parents=True, exist_ok=False)
pd.read_parquet(field, columns=['edge_id', 'exposure_index']).to_csv(args.out / 'weights.csv', index=False)
(args.out / 'cases.json').write_text(json.dumps({'graph_version': manifest['graph_version'], 'window': window,
    'source_field_sha256': actual, 'cases': cases}, indent=2) + '\n', encoding='utf-8')
print(f'Prepared {len(cases)} recorded successful route cases; source field hash matched.')
