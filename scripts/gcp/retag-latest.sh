#!/usr/bin/env bash
# Point :latest at the images of a deployed commit. Run by .github/workflows/deploy-gcp.yml after the smoke test,
# so :latest is always the last image set that passed a deploy (scripts/gcp/jobs/*.yaml use it). See README.md.
# Usage: IMAGE_TAG=<deployed commit SHA> scripts/gcp/retag-latest.sh
# Needs: gcloud signed in with roles/artifactregistry.writer on the image repository.
#
# `gcloud artifacts tags update` moves an existing tag (artifactregistry.tags.update); `tags create` makes the first
# one for a new image (tags.create). Both are in the writer role. `gcloud artifacts docker tags add` would delete the
# tag first, which needs tags.delete. Every digest is resolved before any tag moves, each call is retried, and the
# log names what moved, so a failure partway is visible.
set -euo pipefail

PROJECT="${PROJECT:-sippolusi}"
REGION="${REGION:-asia-southeast2}"
REPOSITORY="${REPOSITORY:-sippolusi}"
IMAGES="${IMAGES:-$REGION-docker.pkg.dev/$PROJECT/$REPOSITORY}"
TAG="${IMAGE_TAG:?IMAGE_TAG (the deployed commit SHA) is required}"
ATTEMPTS="${ATTEMPTS:-3}"
NAMES=(model-registry cache-api gateway frontend data-worker)
WHERE=(--project="$PROJECT" --location="$REGION" --repository="$REPOSITORY")

# retry <command...>: up to ATTEMPTS runs, waiting 10 s, then 20 s, ... between them.
retry() {
    local attempt
    for ((attempt = 1; ; attempt++)); do
        if "$@"; then
            return 0
        fi
        if [ "$attempt" -ge "$ATTEMPTS" ]; then
            return 1
        fi
        sleep $((attempt * 10))
    done
}

describe() {
    gcloud artifacts docker images describe "$IMAGES/$1:$TAG" --format='value(image_summary.digest)'
}

# move <name> <digest>: update the tag, or create it when the image has no :latest yet.
move() {
    gcloud artifacts tags update latest "${WHERE[@]}" --package="$1" --version="$2" --quiet ||
        gcloud artifacts tags create latest "${WHERE[@]}" --package="$1" --version="$2" --quiet
}

digests=()
for name in "${NAMES[@]}"; do
    if ! digest="$(retry describe "$name")" || [ -z "$digest" ]; then
        echo "::error::No digest for $name:$TAG; no :latest tag was moved."
        exit 1
    fi
    digests+=("$digest")
done

moved=()
for i in "${!NAMES[@]}"; do
    if ! retry move "${NAMES[$i]}" "${digests[$i]}"; then
        echo "::error::Moving :latest failed at ${NAMES[$i]}. Already moved to $TAG: ${moved[*]:-none}."
        exit 1
    fi
    echo "${NAMES[$i]}:latest -> ${digests[$i]}"
    moved+=("${NAMES[$i]}")
done
echo "::notice::Moved :latest to $TAG for ${moved[*]}."
