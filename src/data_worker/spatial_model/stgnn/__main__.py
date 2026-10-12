"""python -m spatial_model.stgnn train|evaluate --help"""

import argparse
import tomllib
from pathlib import Path

from .model import ModelConfig
from .train import TrainingConfig, evaluate_checkpoint, train


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest="command", required=True)
    t = sub.add_parser("train", help="fit on train, select on validation; does not evaluate test")
    t.add_argument("--snapshot", required=True)
    t.add_argument("--out", required=True)
    t.add_argument("--target", choices=["pm25", "no2"], default="pm25")
    t.add_argument("--protocol", choices=["fixed_split", "loso"], default="fixed_split")
    t.add_argument("--fold", type=int)
    t.add_argument("--config", type=Path, default=Path(__file__).parent / "configs/default.toml")
    t.add_argument("--epochs", type=int)
    t.add_argument("--seed", type=int)
    t.add_argument("--no-graph", action="store_true")
    t.add_argument("--no-local", action="store_true")
    e = sub.add_parser("evaluate", help="explicitly evaluate a completed checkpoint")
    e.add_argument("--checkpoint", required=True)
    e.add_argument("--snapshot", required=True)
    e.add_argument("--out", required=True)
    e.add_argument("--split", choices=["val", "test"], default="test")
    for p in (t, e):
        p.add_argument("--device", default="cpu")
        p.add_argument("--threads", type=int, default=4)
    args = parser.parse_args(argv)
    if args.threads < 1:
        parser.error("--threads must be positive")
    import torch
    torch.set_num_threads(args.threads)
    if args.command == "train":
        config = tomllib.loads(args.config.read_text(encoding="utf-8"))
        model = config["model"]
        model["dilations"] = tuple(model["dilations"])
        if args.no_graph:
            model["use_graph"] = False
        if args.no_local:
            model["use_local"] = False
        training = config["training"]
        for key in ("epochs", "seed"):
            if getattr(args, key) is not None:
                training[key] = getattr(args, key)
        train(args.snapshot, args.out, args.target, args.protocol, args.fold,
              ModelConfig(**model), TrainingConfig(**training), args.device)
    else:
        evaluate_checkpoint(args.checkpoint, args.snapshot, args.out, args.split, args.device)


if __name__ == "__main__":
    main()
