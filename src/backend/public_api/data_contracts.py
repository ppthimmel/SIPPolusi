"""TI-SE-01 exchange schemas. Regenerate: python -m public_api.data_contracts --write.

Select a type with #/$defs/Point, #/$defs/RouteRequest, etc. Semantic rules
(preference sum, window duration, graph references) are enforced by these same
runtime models and documented in docs/software/data-contracts-TI-SE-01.md.
"""
from __future__ import annotations

import argparse
import json
from pathlib import Path

from osm.schemas import PointModel, RoadGraphOut
from planner.schemas import RouteRequestIn, SolverInputOut
from pollution.schemas import EdgeWeightOut, EdgeWeightsOut, TimeWindowOut
from public_api.schemas import RouteFeatureCollection

CONTRACT_VERSION = "1.0.0"
SCHEMA_PATH = Path(__file__).with_name("data-contracts-v1.schema.json")
EXAMPLES_PATH = Path(__file__).with_name("examples") / "data-contracts-v1.json"
MODELS = {
    "Point": PointModel,
    "RouteRequest": RouteRequestIn,
    "RoadGraph": RoadGraphOut,
    "EdgeWeight": EdgeWeightOut,
    "TimeWindow": TimeWindowOut,
    "EdgeWeights": EdgeWeightsOut,
    "SolverInput": SolverInputOut,
    "RouteOutput": RouteFeatureCollection,
}


def build() -> dict:
    definitions = {}
    for name, model in MODELS.items():
        schema = model.model_json_schema()
        definitions.update(schema.pop("$defs", {}))
        definitions[name] = schema
    return {
        "$schema": "https://json-schema.org/draft/2020-12/schema",
        "$id": f"urn:sippolusi:data-contracts:{CONTRACT_VERSION}",
        "title": "SIPPolusi data exchange contracts",
        "description": "Select a type using #/$defs/<type>. Cross-field rules require runtime validation; see the TI-SE-01 contract document.",
        "x-contract-version": CONTRACT_VERSION,
        "$defs": dict(sorted(definitions.items())),
    }


def render(schema: dict) -> str:
    return json.dumps(schema, indent=2, ensure_ascii=False, allow_nan=False) + "\n"


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--write", action="store_true")
    args = parser.parse_args()
    content = render(build())
    if args.write:
        SCHEMA_PATH.write_text(content, encoding="utf-8")
        print(f"{SCHEMA_PATH} diperbarui")
    else:
        print(content, end="")


if __name__ == "__main__":
    main()
