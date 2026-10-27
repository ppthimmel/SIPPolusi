"""Measure changing-tile source reads; optionally compare exact pre-change frames.

Run with PYTHONPATH=src/data_worker. Output frames stay in the specified directory.
"""
import argparse
from collections import Counter
import hashlib
import json
from pathlib import Path
from time import perf_counter
from unittest.mock import patch

import pandas as pd
import psutil

from feature_matrix.sources import SourceReader
from spatial_model.inference.serving import tiles


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--compare", type=Path)
    args = parser.parse_args()
    args.output.mkdir(parents=True, exist_ok=True)
    reader = SourceReader()
    cells = reader._cells.reset_index()
    positions = cells.grid_id.str.extract(r"r(\d+)_c(\d+)").astype(int)
    cells["row"], cells["col"] = positions[0], positions[1]
    groups = list(tiles(cells))
    # Spread tiles across the domain; shared cells emulate sensor-grid additions.
    chosen = [groups[i] for i in (0, len(groups)//3, 2*len(groups)//3, len(groups)-1)]
    shared = cells.iloc[::max(1, len(cells)//75)].grid_id.tolist()
    requests = [list(dict.fromkeys([*part.loc[part.grid_id.isin(cells.grid_id), "grid_id"], *shared]))
                for part_index in (1, 0) for pair in chosen for part in [pair[part_index]]]
    counts, timings, hashes = Counter(), [], []
    original_read = pd.read_parquet
    def counted(path, *a, **kw):
        counts[Path(path).relative_to(reader.root).as_posix()] += 1
        return original_read(path, *a, **kw)
    with patch.object(pd, "read_parquet", counted):
        for i, ids in enumerate(requests):
            start = perf_counter()
            frame = reader(ids, "2026-09-28T13:00Z", "2026-09-29T13:00Z")
            timings.append(perf_counter()-start)
            hashes.append(hashlib.sha256(pd.util.hash_pandas_object(frame, index=False).values.tobytes()).hexdigest())
            if args.compare:
                pd.testing.assert_frame_equal(frame, original_read(args.compare / f"{i:03d}.parquet"))
            frame.to_parquet(args.output / f"{i:03d}.parquet", index=False)
    def retained_bytes(value):
        if isinstance(value, (pd.DataFrame, pd.Series)):
            size = value.memory_usage(index=True, deep=True)
            return int(size.sum() if isinstance(size, pd.Series) else size)
        if isinstance(value, dict):
            return sum(retained_bytes(item) for item in value.values())
        return 0
    memory = psutil.Process().memory_info()
    result = dict(requests=len(requests), total_seconds=sum(timings), seconds=timings,
                  parquet_reads=sum(counts.values()), reads_by_file=dict(counts),
                  frame_hashes=hashes, exact_parity=bool(args.compare),
                  rss_mb=memory.rss/1024**2,
                  peak_rss_mb=getattr(memory, "peak_wset", memory.rss)/1024**2,
                  source_tables_mb=retained_bytes(getattr(reader, "_source_cache", None))/1024**2)
    (args.output / "result.json").write_text(json.dumps(result, indent=2))
    print(json.dumps({k: v for k, v in result.items() if k not in ("reads_by_file", "frame_hashes")}, indent=2))


if __name__ == "__main__":
    main()
