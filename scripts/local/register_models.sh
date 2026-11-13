#!/bin/sh
# Register every packaged model folder with the local registry (docker compose service register_models).
# /out holds the route package built by package_route; /local-models holds spatial bundles you put in
# ./.local-models (each needs a package.json: see docs/devops/local-railway-parity.md).
# A folder already registered (same package.json hash) is reported and skipped, so this can be rerun.
status=0
for d in /out/*/ /local-models/*/; do
    [ -d "$d" ] || continue
    if [ ! -f "${d}package.json" ]; then
        echo "skip ${d}: no package.json (package it first)"
        continue
    fi
    echo "== ${d}"
    if output=$(REGISTER_PREFIX=local/ REGISTER_LOCAL_DIR="$d" python /tools/register_from_bucket.py 2>&1); then
        echo "$output" | grep -E "verified|registered|RESOLVED"
    elif echo "$output" | grep -q "already holds this package"; then
        echo "$output" | grep "already holds this package"
    else
        echo "$output" | tail -5
        echo "not registered: ${d}"
        status=1
    fi
done
exit $status
