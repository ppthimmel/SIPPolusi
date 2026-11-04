"""Recompute the PM2.5 ST-GNN fixed-split test metrics from a checkpoint with the repository's own evaluator.

    python scripts/recompute_pm25_test_metrics.py --bundle <bundle dir> --out <output dir> \\
        [--snapshot docs/experiments/ds-v0.3.0]

The evaluator refuses a checkpoint whose recorded snapshot hashes differ from the checkout's. On a Windows checkout the
text files of the snapshot have CRLF line endings and hash differently from the LF originals, and some documentation
files were edited after training. This script therefore checks, before overriding the recorded hashes, that
  * every data file (.parquet, .csv) is byte-identical to the one the checkpoint was trained on, and
  * every differing file is a text file (.md, .toml, .json);
  it prints which differing files are explained by CRLF -> LF and which are not.
It then runs the evaluator on the test split with the checkpoint's own recorded hashes. Needs the Data Worker
requirements (torch, torch-geometric, pandas, pyarrow) and Python 3.12 or older.
"""
import argparse
import hashlib
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src/data_worker"))


def main():
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--bundle", type=Path, required=True)
    parser.add_argument("--out", type=Path, required=True)
    parser.add_argument("--snapshot", type=Path, default=ROOT / "docs/experiments/ds-v0.3.0")
    args = parser.parse_args()

    import torch
    from spatial_model.evaluation.stgnn import evaluate as ev
    from spatial_model.models.stgnn.data import prepare

    checkpoint = args.bundle / "best.pt"
    meta = torch.load(checkpoint, map_location="cpu", weights_only=True)["metadata"]
    if meta.get("target", "pm25") != "pm25":
        sys.exit("this script is for the PM2.5 ST-GNN checkpoint")
    data = prepare(args.snapshot, meta["protocol"], meta["fold"])
    recorded = meta["snapshot_hashes"]
    differing = sorted(k for k in set(recorded) | set(data.hashes) if recorded.get(k) != data.hashes.get(k))
    crlf_only = [k for k in differing
                 if hashlib.sha256((args.snapshot / k).read_bytes().replace(b"\r\n", b"\n")).hexdigest() == recorded.get(k)]
    other = [k for k in differing if k not in crlf_only]
    data_equal = all(recorded.get(k) == data.hashes.get(k) for k in recorded if k.endswith((".parquet", ".csv")))
    print(f"files whose hash differs: {len(differing)}; explained by CRLF->LF: {len(crlf_only)}; other: {other}")
    print("data files (.parquet/.csv) identical to training time:", data_equal)
    if not data_equal or any(not k.endswith((".md", ".toml", ".json")) for k in other):
        sys.exit("a data file differs from the training snapshot; not evaluating")
    data.hashes = dict(recorded)
    ev.evaluate(args.snapshot, checkpoint, args.out, splits=("test",), device="cpu", batch_size=8, threads=2, _data=data)


if __name__ == "__main__":
    main()
