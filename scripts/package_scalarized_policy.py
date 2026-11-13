"""Create a serving package from an existing selected scalarized run (no training)."""
import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src/backend"))

from public_api.data_contracts import CONTRACT_VERSION  # noqa: E402
from route_model.morl.packaging import package_scalarized_policy  # noqa: E402


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--run", type=Path, required=True, help="scalarized training run directory")
    parser.add_argument("--out", type=Path, required=True, help="new or empty package directory")
    parser.add_argument("--decision", help="evaluation decision to record in the package")
    args = parser.parse_args()
    package = package_scalarized_policy(args.run, args.out, data_contracts_version=CONTRACT_VERSION,
                                        decision=args.decision)
    print(json.dumps({"model_id": package["model_id"], "model_version": package["model_version"],
                      "policy_family": package["inference"]["policy_family"], "files": len(package["artifacts"])},
                     indent=2))


if __name__ == "__main__":
    main()
