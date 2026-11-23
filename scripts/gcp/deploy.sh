#!/usr/bin/env bash
# Deploy the SIPPolusi services to Cloud Run (project sippolusi, asia-southeast2). See scripts/gcp/README.md.
# Usage: scripts/gcp/deploy.sh <registry|cache-api|gateway|frontend|data-worker|model-register|all>
# Needs: gcloud signed in, images built with scripts/gcp/cloudbuild.yaml, secrets database-url,
# mlflow-backend-store-uri and earthdata-token in Secret Manager. No secret value is written here.
set -euo pipefail

GCLOUD="${GCLOUD:-gcloud}"
PROJECT="${PROJECT:-sippolusi}"
REGION="${REGION:-asia-southeast2}"
NUMBER="${PROJECT_NUMBER:-$("$GCLOUD" projects describe "$PROJECT" --format='value(projectNumber)')}"
TAG="${IMAGE_TAG:-latest}"   # CI deploys the image built from the commit (its SHA)
IMAGES="$REGION-docker.pkg.dev/$PROJECT/sippolusi"
BUCKET="${BUCKET:-sippolusi-mlflow-$NUMBER}"
APP_SA="sippolusi-app@$PROJECT.iam.gserviceaccount.com"
REGISTRY_SA="sippolusi-registry@$PROJECT.iam.gserviceaccount.com"
# Deterministic Cloud Run URL: https://<service>-<project number>.<region>.run.app
url() { echo "https://$1-$NUMBER.$REGION.run.app"; }
# Direct VPC egress: private ranges (Cloud SQL private IP) and, through the private run.app DNS zone and
# Private Google Access, the internal-only Cloud Run services. Other traffic leaves directly to the internet.
VPC=(--network=default --subnet=default --vpc-egress=private-ranges-only)
COMMON=(--project="$PROJECT" --region="$REGION" --quiet)

registry() {
    # MLflow has no authentication: ingress internal, never public (as on Railway private networking).
    "$GCLOUD" run deploy model-registry "${COMMON[@]}" --image="$IMAGES/model-registry:$TAG" \
        --service-account="$REGISTRY_SA" --ingress=internal --allow-unauthenticated "${VPC[@]}" \
        --port=5000 --cpu=1 --memory=1Gi --min-instances=0 --max-instances=1 --timeout=600 \
        --set-secrets=MLFLOW_BACKEND_STORE_URI=mlflow-backend-store-uri:latest \
        --set-env-vars="MLFLOW_ARTIFACT_ROOT=mlflow-artifacts:/,MLFLOW_ARTIFACTS_DESTINATION=gs://$BUCKET/artifacts,ENVIRONMENT=DEV"
}

cache_api() {
    "$GCLOUD" run deploy cache-api "${COMMON[@]}" --image="$IMAGES/cache-api:$TAG" \
        --service-account="$APP_SA" --ingress=internal --allow-unauthenticated "${VPC[@]}" \
        --cpu=2 --memory=4Gi --min-instances=0 --max-instances=2 --timeout=120 --no-cpu-throttling \
        --set-secrets=DATABASE_URL=database-url:latest \
        --set-env-vars="ROUTE_SOLVER_PROVIDER=morl,MLFLOW_TRACKING_URI=$(url model-registry),ROUTE_SOLVER_BUDGET_MS=40000,ROUTE_SOLVER_FALLBACK_TIMEOUT_MS=20000,ROUTE_SOLVER_GATEWAY_TIMEOUT_MS=43000,OSM_COMMAND_TIMEOUT_S=30,OSM_STATEMENT_TIMEOUT_MS=25000,API_GEOCODING_URL=https://photon.komoot.io,ENVIRONMENT=DEV"
}

gateway() {
    "$GCLOUD" run deploy gateway "${COMMON[@]}" --image="$IMAGES/gateway:$TAG" \
        --service-account="$APP_SA" --ingress=all --allow-unauthenticated "${VPC[@]}" \
        --cpu=1 --memory=256Mi --min-instances=0 --max-instances=2 --timeout=60 \
        --set-env-vars="CACHE_API_UPSTREAM=$(url cache-api):443,GATEWAY_UPSTREAM_TIMEOUT_S=43"
}

frontend() {
    "$GCLOUD" run deploy frontend "${COMMON[@]}" --image="$IMAGES/frontend:$TAG" \
        --service-account="$APP_SA" --ingress=all --allow-unauthenticated \
        --cpu=1 --memory=256Mi --min-instances=0 --max-instances=2 --timeout=60 \
        --set-env-vars="GATEWAY_UPSTREAM=$(url gateway),ENVIRONMENT=DEV"
}

data_worker() {
    # One acquisition cycle per execution (image CMD: python main.py cycle); Cloud Scheduler runs it at minute 15.
    "$GCLOUD" run jobs deploy data-worker "${COMMON[@]}" --image="$IMAGES/data-worker:$TAG" \
        --service-account="$APP_SA" "${VPC[@]}" --cpu=2 --memory=4Gi --max-retries=0 --task-timeout=3000 \
        --set-secrets=DATABASE_URL=database-url:latest,EARTHDATA_TOKEN=earthdata-token:latest \
        --set-env-vars="MLFLOW_TRACKING_URI=$(url model-registry),SPATIAL_PM25_MODEL=spatial-downscaling-pm25,SPATIAL_NO2_MODEL=spatial-downscaling-no2,SPATIAL_PM25_VERSION=production,SPATIAL_NO2_VERSION=production,SPATIAL_MODEL_CACHE=/tmp/model-cache,DOWNSCALE_WRITE_CACHE=1,DOWNSCALE_KEEP_WINDOWS=2,THREADS=2,OMP_NUM_THREADS=2,MKL_NUM_THREADS=2,ENVIRONMENT=DEV,SPKU_DB_SCHEMA=ground_truth,LOG_FORMAT=json"
}

model_register() {
    # TI-DO-10b job. Run: gcloud run jobs execute model-register --update-env-vars REGISTER_PREFIX=incoming/.../
    # (or PROMOTE=route:<version>); the override applies to that execution only.
    "$GCLOUD" run jobs deploy model-register "${COMMON[@]}" --image="$IMAGES/data-worker:$TAG" \
        --service-account="$APP_SA" "${VPC[@]}" --cpu=1 --memory=2Gi --max-retries=0 --task-timeout=1200 \
        --command=python --args=-m,spatial_model.training,railway-job \
        --set-env-vars="MLFLOW_TRACKING_URI=$(url model-registry)"
}

case "${1:-}" in
    registry) registry ;;
    cache-api) cache_api ;;
    gateway) gateway ;;
    frontend) frontend ;;
    data-worker) data_worker ;;
    model-register) model_register ;;
    all) registry; cache_api; gateway; frontend; data_worker; model_register ;;
    *) echo "usage: $0 <registry|cache-api|gateway|frontend|data-worker|model-register|all>" >&2; exit 2 ;;
esac
