"""Audit the existing exploratory comparison without rerunning models."""
import gzip
import hashlib
import json
from collections import Counter
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SOURCE = ROOT / "docs/experiments/TI-AI-11/morl-comparison"


def sha(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def audit():
    files = ("manifest.json", "decision.json", "summary.json", "scenario_od_pairs.json", "scenarios.jsonl.gz")
    groups = {}
    with gzip.open(SOURCE / "scenarios.jsonl.gz", "rt", encoding="utf-8") as stream:
        for line in stream:
            r = json.loads(line)
            key = f"{r['stratum']}/{r['fields']}"
            g = groups.setdefault(key, {"records": 0, "failures": Counter(), "alpha_05": {"count": 0, "same_optimal": 0}})
            g["records"] += 1
            for name, data in r["methods"].items():
                route = data["route"]
                if not route["reached"]:
                    g["failures"][f"{name}:{route.get('failure', 'unknown')}"] += 1
            if r["alpha"] == 0.5:
                route = r["methods"]["scalarized"]["route"]
                if route["reached"] and r["optimal"]["reached"]:
                    g["alpha_05"]["count"] += 1
                    g["alpha_05"]["same_optimal"] += route["route_sha256"] == r["optimal"]["route_sha256"]
    return {"scope": "exploratory evidence audit; not a new model run", "input_sha256": {f: sha(SOURCE / f) for f in files}, "groups": groups}


if __name__ == "__main__":
    print(json.dumps(audit(), indent=2, sort_keys=True))
