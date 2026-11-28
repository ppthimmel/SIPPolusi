"""Train, register, and promote the pollutant models."""
from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
from spatial_model.configs import DEFAULT_SEED


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest="command", required=True)
    t = commands.add_parser("train")
    t.add_argument("target", choices=("pm25", "no2"))
    SNAP = next((p / "docs/experiments/ds-v0.3.0" for p in Path(__file__).resolve().parents
                 if (p / "docs/experiments/ds-v0.3.0").is_dir()), Path("docs/experiments/ds-v0.3.0"))
    t.add_argument("--snapshot", type=Path,
                   help="snapshot folder. Required for NO2, so the production dataset (ds-v0.4.0) is chosen "
                        "explicitly; PM2.5 defaults to docs/experiments/ds-v0.3.0")
    t.add_argument("--out", type=Path, required=True)
    t.add_argument("--fold", type=int)
    t.add_argument("--seed", type=int, default=DEFAULT_SEED)
    t.add_argument("--epochs", type=int, default=100)
    t.add_argument("--device", default="cpu")
    t.add_argument("--resume", action="store_true")
    t.add_argument("--register", action="store_true")
    t.add_argument("--family", choices=("lightgbm", "xgboost"),
                   help="NO2 model family; default xgboost (XGBoost + IDW correction, the production model "
                        "selected on ds-v0.4.0). lightgbm trains the earlier LightGBM model")
    r = commands.add_parser("register")
    r.add_argument("bundle", type=Path)
    p = commands.add_parser("promote", help="set the alias production (a deliberate team decision)")
    p.add_argument("target", choices=("pm25", "no2", "route"))
    p.add_argument("version")
    k = commands.add_parser("package", help="write package.json into a bundle (TI-AI-11)")
    k.add_argument("bundle", type=Path)
    k.add_argument("--grid-spec-id")
    q = commands.add_parser("register-route", help="register a route policy package (TI-AI-11)")
    q.add_argument("package", type=Path)
    b = commands.add_parser("register-from-bucket",
                            help="register a package staged in the registry's bucket, through its proxy (TI-DO-10b)")
    b.add_argument("prefix", help="folder under the bucket path artifacts/, e.g. incoming/spatial/pm25/<version>/")
    b.add_argument("--grid-spec-id", help="for a spatial bundle without package.json")
    b.add_argument("--wait", type=float, default=120.0, help="seconds to wait for a sleeping registry (0: no wait)")
    commands.add_parser("railway-job", help="one-off Railway job: REGISTER_PREFIX registers, PROMOTE=<target>:<version> "
                                            "promotes the current candidate (src/model_registry/README.md)")
    d = commands.add_parser("register-dir", help="register-from-bucket for a local folder (local compose job)")
    d.add_argument("directory", type=Path)
    d.add_argument("--grid-spec-id", help="for a spatial bundle without package.json")
    d.add_argument("--promote", action="store_true", help="then give it production (local stack only)")
    d.add_argument("--wait", type=float, default=120.0, help="seconds to wait for the registry (0: no wait)")
    args = parser.parse_args(argv)
    if args.command == "register-dir":
        from spatial_model.artifacts.staged import RegistrationError, register_directory
        try:
            print(json.dumps(register_directory(args.directory, grid_spec_id=args.grid_spec_id, promote=args.promote,
                                                wait=args.wait)))
        except RegistrationError as exc:
            parser.exit(1, f"register-dir: FAILED: {exc}\n")
    elif args.command in ("register-from-bucket", "railway-job"):
        from spatial_model.artifacts.staged import RegistrationError, promote_candidate, register_from_bucket
        prefix, promotion = ((args.prefix, None) if args.command == "register-from-bucket"
                             else (os.environ.get("REGISTER_PREFIX", "").strip(), os.environ.get("PROMOTE", "").strip()))
        grid_spec_id = (args.grid_spec_id if args.command == "register-from-bucket"
                        else os.environ.get("REGISTER_GRID_SPEC_ID", "").strip() or None)
        try:
            if prefix and promotion:
                raise RegistrationError("REGISTER_PREFIX and PROMOTE are both set; set one per run")
            if promotion:
                print(json.dumps(promote_candidate(promotion)))
            elif prefix:
                print(json.dumps(register_from_bucket(prefix, grid_spec_id=grid_spec_id,
                                                      wait=getattr(args, "wait", 120.0))))
            else:
                parser.exit(0, "railway-job: neither REGISTER_PREFIX nor PROMOTE is set; nothing to do\n")
        except RegistrationError as exc:
            parser.exit(1, f"{args.command}: FAILED: {exc}\n")
    elif args.command == "package":
        from spatial_model.artifacts.packaging import package_bundle
        print(json.dumps(package_bundle(args.bundle, grid_spec_id=args.grid_spec_id)["model_version"]))
    elif args.command == "register-route":
        from spatial_model.artifacts.registry import register_route_package
        print(json.dumps(register_route_package(args.package)))
    elif args.command == "promote" and args.target == "route":
        from spatial_model.artifacts.registry import promote_route
        print(promote_route(args.version))
    elif args.command == "promote":
        from spatial_model.artifacts.registry import promote
        print(promote(args.target, args.version))
    elif args.command == "register":
        from spatial_model.artifacts.registry import register
        print(json.dumps(register(args.bundle)))
    else:
        if args.target == "pm25" and args.family is not None:
            parser.error("--family applies to NO2; PM2.5 trains the ST-GNN")
        if args.target == "no2" and args.snapshot is None:
            parser.error("NO2 training needs --snapshot; the production NO2 model is trained on ds-v0.4.0")
        if args.target == "pm25":
            from spatial_model.models.stgnn.train import train
            bundle = train(args.snapshot or SNAP, args.out, protocol="fixed_split" if args.fold is None else "loso",
                           fold=args.fold, seed=args.seed, epochs=args.epochs, device=args.device, resume=args.resume)
        else:
            if args.resume:
                parser.error("Tabular training does not use checkpoint resume")
            from spatial_model.models.lightgbm.train import train
            bundle = train(args.snapshot, args.out, seed=args.seed, fold=args.fold, family=args.family or "xgboost")
        print(bundle)
        if args.register:
            from spatial_model.artifacts.registry import register
            print(json.dumps(register(bundle)))


if __name__ == "__main__":
    main()
