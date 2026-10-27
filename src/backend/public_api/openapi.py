"""OpenAPI document of the public contract: the paths the API Gateway exposes.

    python -m public_api.openapi            print to stdout
    python -m public_api.openapi --write    update public_api/openapi.json

Only gateway-routed paths are kept (POST /v1/routes, GET /v1/exposure-surface,
/v1/osm/*, /v1/pollution/*); the inspection endpoints (/v1/routes/solve, /plan,
/solver-input) and /health are internal. tests/test_openapi.py fails when
openapi.json is out of date, so the committed file always matches the code.
"""
from __future__ import annotations

import argparse
import json
import pathlib
from typing import Any

SPEC_PATH = pathlib.Path(__file__).with_name("openapi.json")
PUBLIC_EXACT = {"/v1/routes", "/v1/exposure-surface"}
PUBLIC_PREFIXES = ("/v1/osm/", "/v1/pollution/")

DESCRIPTION = """\
Kontrak publik SIPPolusi, diakses lewat API Gateway (`https://<gateway>/v1/...`).

- **Koordinat** selalu [lon, lat] WGS 84 (`urn:ogc:def:crs:OGC::CRS84`, RFC 7946). Respons GeoJSON
  menyatakannya di `metadata.crs` dan `metadata.coordinate_order`.
- **Satuan** setiap metrik numerik ada di `metadata.units` (menit, meter, indeks·menit, µg·min/m³, µg/m³).
- **Galat** memakai skema seragam `{"error": {"code", "message", "request_id", "reason"?}}` dengan kode
  pada Tabel 3.16. 429, 502, dan 504 dihasilkan API Gateway.
- **Batas ukuran body** 16 KiB (413 `payload_too_large`).
- Setiap respons membawa header `X-Request-ID` untuk penelusuran log.

Data jalan © OpenStreetMap contributors, ODbL (https://www.openstreetmap.org/copyright).
"""


_ERROR_REF = {"$ref": "#/components/schemas/ErrorResponse"}
_ERROR_CONTENT = {"application/json": {"schema": _ERROR_REF}}


def uniform_error_docs(spec: dict[str, Any]) -> dict[str, Any]:
    """Make the document say what the error handlers do (osm/errors.py).

    FastAPI documents request validation as 422 HTTPValidationError, but the app
    answers 400 invalid_request in the uniform schema. Replace it, and give every
    operation a "default" response with that schema for the codes it does not list."""
    for ops in spec.get("paths", {}).values():
        for op in ops.values():
            responses = op.setdefault("responses", {})
            # FastAPI files every response "model" under the route's media type
            # (application/geo+json); errors are always sent as application/json.
            for code, response in responses.items():
                content = response.get("content", {})
                if code.startswith("2") or "application/json" not in content:
                    continue
                for media in [m for m in content if m != "application/json"]:
                    schema = content.pop(media).get("schema")
                    if schema is not None:
                        content["application/json"].setdefault("schema", schema)
            auto = responses.get("422", {}).get("content", {}).get("application/json", {}).get("schema", {})
            if auto.get("$ref", "").endswith("/HTTPValidationError"):
                del responses["422"]
                responses.setdefault("400", {"description": "invalid_request: parameter atau body tidak valid",
                                             "content": _ERROR_CONTENT})
            responses.setdefault("default", {"description": "Galat lain dengan skema seragam (Tabel 3.16)",
                                             "content": _ERROR_CONTENT})
    schemas = spec.get("components", {}).get("schemas", {})
    for name in ("HTTPValidationError", "ValidationError"):  # in this order: the first refers to the second
        others = {k: v for k, v in schemas.items() if k != name}
        if name not in _refs(spec.get("paths", {})) | _refs(others):
            schemas.pop(name, None)
    return spec


def restore_response_examples(spec: dict[str, Any]) -> dict[str, Any]:
    """FastAPI drops null values from examples when it builds the document, so
    "fallback_reason": null would disappear and look optional. Put back the
    response examples of the public endpoints as public_api.router declares them."""
    from fastapi.encoders import jsonable_encoder

    from public_api.router import DOCUMENTED_RESPONSES

    for (path, method), responses in DOCUMENTED_RESPONSES.items():
        op = spec.get("paths", {}).get(path, {}).get(method)
        if op is None:
            continue
        for code, response in responses.items():
            documented = op["responses"].get(str(code), {}).get("content", {})
            for media, content in response.get("content", {}).items():
                for name, example in content.get("examples", {}).items():
                    target = documented.get(media, {}).get("examples", {}).get(name)
                    if target is not None:
                        target["value"] = jsonable_encoder(example["value"])
    return spec


def install_openapi(app) -> None:
    """Serve the corrected document at /openapi.json and /docs."""
    original = app.openapi

    def openapi() -> dict[str, Any]:
        if app.openapi_schema is None:
            app.openapi_schema = restore_response_examples(uniform_error_docs(original()))
        return app.openapi_schema

    app.openapi = openapi


def is_public(path: str) -> bool:
    return path in PUBLIC_EXACT or path.startswith(PUBLIC_PREFIXES)


def _refs(node: Any) -> set[str]:
    found: set[str] = set()
    if isinstance(node, dict):
        ref = node.get("$ref")
        if isinstance(ref, str) and ref.startswith("#/components/schemas/"):
            found.add(ref.rsplit("/", 1)[1])
        for value in node.values():
            found |= _refs(value)
    elif isinstance(node, list):
        for value in node:
            found |= _refs(value)
    return found


def public_openapi(spec: dict[str, Any]) -> dict[str, Any]:
    """spec reduced to the public paths and the schemas they reference."""
    paths = {p: ops for p, ops in spec["paths"].items() if is_public(p)}
    schemas = spec.get("components", {}).get("schemas", {})
    keep: set[str] = set()
    todo = _refs(paths)
    while todo:
        name = todo.pop()
        if name not in keep:
            keep.add(name)
            todo |= _refs(schemas.get(name, {})) - keep
    import fastapi

    return {
        "openapi": spec["openapi"],
        # FastAPI's output differs slightly between versions; the sync test compares
        # byte for byte only with the version that wrote the file.
        "info": {**spec["info"], "title": "SIPPolusi Public API", "description": DESCRIPTION,
                 "x-generator": f"fastapi {fastapi.__version__}"},
        "paths": paths,
        "components": {"schemas": {name: schemas[name] for name in sorted(keep)}},
    }


def build() -> dict[str, Any]:
    from osm.app import create_app
    from public_api.config import ApiSettings

    # No database and no .env: the document depends on code only.
    return public_openapi(create_app(repository=object(), api_settings=ApiSettings(_env_file=None)).openapi())


def render(spec: dict[str, Any]) -> str:
    return json.dumps(spec, indent=2, ensure_ascii=False) + "\n"


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--write", action="store_true", help=f"write {SPEC_PATH.name}")
    text = render(build())
    if parser.parse_args().write:
        SPEC_PATH.write_text(text, encoding="utf-8")
        print(f"{SPEC_PATH} diperbarui")
    else:
        print(text, end="")


if __name__ == "__main__":
    main()
