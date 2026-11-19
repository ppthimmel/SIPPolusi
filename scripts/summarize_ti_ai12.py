"""Summarize the frozen TI-AI-12 paired scenario records."""
import collections
import gzip
import json
import statistics
import sys
from pathlib import Path

records = [json.loads(line) for line in gzip.open(Path(sys.argv[1]), 'rt', encoding='utf-8')]
groups = collections.defaultdict(dict)
for record in records:
    groups[record['od_id']][record['alpha']] = record
gaps, exposures, times = [], [], []
fulfilled = shifted = both_worse = same = paired = 0
for group in groups.values():
    mid, low, high = group[0.5], group[0.0], group[1.0]
    if not all(x['policy']['reached'] and x['dijkstra']['reached'] for x in (mid, low, high)):
        continue
    paired += 1
    baseline, policy, optimal = high['dijkstra'], mid['policy'], mid['dijkstra']
    t, e = baseline['time_s'], baseline['exposure']
    optimum = 0.5 * optimal['time_s'] / t + 0.5 * optimal['exposure'] / e
    gaps.append(100 * ((0.5 * policy['time_s'] / t + 0.5 * policy['exposure'] / e) / optimum - 1))
    exposures.append(100 * (policy['exposure'] / e - 1))
    times.append(100 * (policy['time_s'] / t - 1))
    same += policy['route_sha256'] == optimal['route_sha256']
    both_worse += policy['exposure'] > e and policy['time_s'] > t
    a, b = low['policy'], high['policy']
    ok = a['exposure'] <= b['exposure'] and a['time_s'] >= b['time_s']
    fulfilled += ok
    shifted += ok and a['route_sha256'] != b['route_sha256']
print(json.dumps({'complete_pairs': paired, 'alpha_05_same_as_optimum': same,
                  'alpha_05_mean_objective_gap_pct': statistics.mean(gaps),
                  'alpha_05_median_objective_gap_pct': statistics.median(gaps),
                  'alpha_05_max_objective_gap_pct': max(gaps),
                  'alpha_05_median_exposure_change_vs_fastest_pct': statistics.median(exposures),
                  'alpha_05_median_time_change_vs_fastest_pct': statistics.median(times),
                  'alpha_05_slower_and_more_exposed': both_worse,
                  'preference_fulfilled': fulfilled, 'preference_shifted': shifted}, indent=2))
