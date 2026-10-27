"""Train, register, and promote the ds-v0.3.0 pollutant models."""
from __future__ import annotations

import argparse
import json
from pathlib import Path
from spatial_model.configs import DEFAULT_SEED


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest="command", required=True)
    t = commands.add_parser("train")
    t.add_argument("target", choices=("pm25", "no2"))
    SNAP = next((p / "docs/experiments/ds-v0.3.0" for p in Path(__file__).resolve().parents
                 if (p / "docs/experiments/ds-v0.3.0").is_dir()), Path("docs/experiments/ds-v0.3.0"))
    t.add_argument("--snapshot", type=Path, default=SNAP)
    t.add_argument("--out", type=Path, required=True)
    t.add_argument("--fold", type=int)
    t.add_argument("--seed", type=int, default=DEFAULT_SEED)
    t.add_argument("--epochs", type=int, default=100)
    t.add_argument("--device", default="cpu")
    t.add_argument("--resume", action="store_true")
    t.add_argument("--register", action="store_true")
    r = commands.add_parser("register")
    r.add_argument("bundle", type=Path)
    p = commands.add_parser("promote")
    p.add_argument("target", choices=("pm25", "no2"))
    p.add_argument("version")
    args = parser.parse_args(argv)
    if args.command == "promote":
        from spatial_model.artifacts.registry import promote
        print(promote(args.target, args.version))
    elif args.command == "register":
        from spatial_model.artifacts.registry import register
        print(json.dumps(register(args.bundle)))
    else:
        if args.target == "pm25":
            from spatial_model.models.stgnn.train import train
            bundle = train(args.snapshot, args.out, protocol="fixed_split" if args.fold is None else "loso",
                           fold=args.fold, seed=args.seed, epochs=args.epochs, device=args.device, resume=args.resume)
        else:
            if args.resume:
                parser.error("LightGBM training is deterministic and does not use checkpoint resume")
            from spatial_model.models.lightgbm.train import train
            bundle = train(args.snapshot, args.out, seed=args.seed, fold=args.fold)
        print(bundle)
        if args.register:
            from spatial_model.artifacts.registry import register
            print(json.dumps(register(bundle)))


if __name__ == "__main__":
    main()
