"""Recompute latency and outcome summaries from recorded TI-AI-12 scenarios."""
import argparse
import gzip
import json
import math
import statistics
from pathlib import Path


def summarize(records):
    if not records:
        raise ValueError('No scenario records')
    result = {}
    for method in ('policy', 'dijkstra'):
        times = sorted(record[method]['elapsed_ms'] for record in records)
        result[method] = {
            'requests': len(records),
            'success': sum(record[method]['reached'] for record in records),
            'invalid': sum(record[method].get('valid') is False for record in records),
            'median_ms': statistics.median(times),
            'p95_ms': times[math.ceil(len(times) * 0.95) - 1],
            'max_ms': max(times),
        }
    return result


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('scenarios', type=Path)
    args = parser.parse_args()
    with gzip.open(args.scenarios, 'rt', encoding='utf-8') as stream:
        result = summarize([json.loads(line) for line in stream])
    print(json.dumps(result, indent=2))


if __name__ == '__main__':
    main()
