"""Evaluate spatial models independently of training."""
import argparse
import sys
from pathlib import Path


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__, add_help=False)
    parser.add_argument("model", choices=("idw", "stgnn", "lightgbm"))
    argv = list(sys.argv[1:] if argv is None else argv)
    if not argv or argv[0] in ("-h", "--help"):
        parser.print_help()
        return
    args, rest = parser.parse_known_args(argv)
    if args.model == "idw":
        from .idw.__main__ import main as idw_main
        return idw_main(rest)
    model_parser = argparse.ArgumentParser(prog=f"python -m spatial_model.evaluation {args.model}")
    model_parser.add_argument("--snapshot", type=Path, required=True)
    model_parser.add_argument("--checkpoint", type=Path, required=True,
                              help="ST-GNN best.pt file or LightGBM checkpoint/bundle directory")
    model_parser.add_argument("--out", type=Path, required=True)
    model_parser.add_argument("--split", choices=("val", "test", "both"), default="both")
    if args.model == "stgnn":
        model_parser.add_argument("--device", default="cpu")
        model_parser.add_argument("--batch-size", type=int, default=8)
        model_parser.add_argument("--threads", type=int, default=2)
    options = model_parser.parse_args(rest)
    kwargs = dict(splits=("val", "test") if options.split == "both" else (options.split,))
    if options.out.exists():
        model_parser.error("Use a new evaluation output directory")
    if args.model == "stgnn":
        from .stgnn.evaluate import evaluate
        kwargs.update(device=options.device, batch_size=options.batch_size, threads=options.threads)
    else:
        from .lightgbm.evaluate import evaluate
    evaluate(options.snapshot, options.checkpoint, options.out, **kwargs)
    print(options.out.resolve())


if __name__ == "__main__":
    main()
