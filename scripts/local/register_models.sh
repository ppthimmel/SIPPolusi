#!/bin/sh
# Register every model folder with the local registry (docker compose service register_models, Data Worker image).
# /out holds the route package built by package_route; /local-models holds spatial bundles you put in
# ./.local-models (with or without package.json; one without is packaged in a copy, see
# docs/devops/local-railway-parity.md).
# Same verification as the Railway job (python -m spatial_model.training register-from-bucket), then production,
# because the local stack serves what it registers. A folder already registered is reported and skipped.
status=0
for d in /out/*/ /local-models/*/; do
    [ -d "$d" ] || continue
    echo "== ${d}"
    if ! python -m spatial_model.training register-dir "$d" --promote; then
        echo "not registered: ${d}"
        status=1
    fi
done
exit $status
