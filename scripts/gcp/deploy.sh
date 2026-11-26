#!/usr/bin/env bash
# Deploy the SIPPolusi services to Cloud Run (project sippolusi, asia-southeast2). See scripts/gcp/README.md.
# Usage: scripts/gcp/deploy.sh <registry|cache-api|gateway|frontend|data-worker|model-register|all>
#        scripts/gcp/deploy.sh <observability-setup|observability>   (by hand; not part of `all`, see below)
# Needs: gcloud signed in, images built with scripts/gcp/cloudbuild.yaml, secrets database-url,
# mlflow-backend-store-uri and earthdata-token in Secret Manager. No secret value is written here.
set -euo pipefail
# Git Bash on Windows rewrites arguments that look like Unix paths (/etc/alertmanager/... becomes
# C:/Program Files/Git/etc/...); container paths passed to Cloud Run must stay as written. Only these flags are
# exempt: gcloud's own Windows wrapper needs the conversion for its paths. No effect outside Git Bash.
export MSYS2_ARG_CONV_EXCL="--set-env-vars=;--set-secrets="

GCLOUD="${GCLOUD:-gcloud}"
PROJECT="${PROJECT:-sippolusi}"
REGION="${REGION:-asia-southeast2}"
NUMBER="${PROJECT_NUMBER:-$("$GCLOUD" projects describe "$PROJECT" --format='value(projectNumber)')}"
TAG="${IMAGE_TAG:-latest}"   # CI deploys the image built from the commit (its SHA)
IMAGES="$REGION-docker.pkg.dev/$PROJECT/sippolusi"
BUCKET="${BUCKET:-sippolusi-mlflow-$NUMBER}"
APP_SA="sippolusi-app@$PROJECT.iam.gserviceaccount.com"
REGISTRY_SA="sippolusi-registry@$PROJECT.iam.gserviceaccount.com"
OBS_SA="sippolusi-observability@$PROJECT.iam.gserviceaccount.com"
# Observability images are built by hand, tagged with the commit they come from (scripts/gcp/README.md).
OBS_TAG="${OBS_IMAGE_TAG:-}"
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
        --set-env-vars="MLFLOW_TRACKING_URI=$(url model-registry),SPATIAL_PM25_MODEL=spatial-downscaling-pm25,SPATIAL_NO2_MODEL=spatial-downscaling-no2,SPATIAL_PM25_VERSION=production,SPATIAL_NO2_VERSION=production,SPATIAL_MODEL_CACHE=/tmp/model-cache,DOWNSCALE_WRITE_CACHE=1,DOWNSCALE_KEEP_WINDOWS=2,THREADS=2,OMP_NUM_THREADS=2,MKL_NUM_THREADS=2,ENVIRONMENT=DEV,SPKU_DB_SCHEMA=ground_truth,LOG_FORMAT=json,PUSHGATEWAY_URL=$(url pushgateway)"
}

model_register() {
    # TI-DO-10b job. Run: gcloud run jobs execute model-register --update-env-vars REGISTER_PREFIX=incoming/.../
    # (or PROMOTE=route:<version>); the override applies to that execution only.
    "$GCLOUD" run jobs deploy model-register "${COMMON[@]}" --image="$IMAGES/data-worker:$TAG" \
        --service-account="$APP_SA" "${VPC[@]}" --cpu=1 --memory=2Gi --max-retries=0 --task-timeout=1200 \
        --command=python --args=-m,spatial_model.training,railway-job \
        --set-env-vars="MLFLOW_TRACKING_URI=$(url model-registry)"
}

# --- Observability (issue #112): Prometheus, Alertmanager, Pushgateway, Grafana. See src/observability/README.md.
# Deployed by hand, never from `all`: a new revision restarts Prometheus, whose data lives in the instance's memory.

observability_setup() {
    # Idempotent; needs a project admin. Service account that reads Cloud Monitoring, Grafana admin password
    # (random, never printed), and the log-based metrics in scripts/gcp/monitoring/.
    "$GCLOUD" iam service-accounts describe "$OBS_SA" --project="$PROJECT" >/dev/null 2>&1 ||
        "$GCLOUD" iam service-accounts create sippolusi-observability --project="$PROJECT" \
            --display-name="SIPPolusi observability (Prometheus, Grafana)"
    "$GCLOUD" projects add-iam-policy-binding "$PROJECT" --member="serviceAccount:$OBS_SA" \
        --role=roles/monitoring.viewer --condition=None --quiet >/dev/null
    if ! "$GCLOUD" secrets describe grafana-admin-password --project="$PROJECT" >/dev/null 2>&1; then
        head -c 64 /dev/urandom | base64 | tr -dc 'A-Za-z0-9' | head -c 32 |
            "$GCLOUD" secrets create grafana-admin-password --project="$PROJECT" --replication-policy=automatic \
                --data-file=-
    fi
    local secret
    for secret in grafana-admin-password alertmanager-config alertmanager-smtp-password; do
        "$GCLOUD" secrets describe "$secret" --project="$PROJECT" >/dev/null 2>&1 || continue
        "$GCLOUD" secrets add-iam-policy-binding "$secret" --project="$PROJECT" \
            --member="serviceAccount:$OBS_SA" --role=roles/secretmanager.secretAccessor --quiet >/dev/null
    done
    local file metric
    for file in "$(dirname "$0")"/monitoring/*.yaml; do
        metric="$(basename "$file" .yaml)"
        if "$GCLOUD" logging metrics describe "$metric" --project="$PROJECT" >/dev/null 2>&1; then
            "$GCLOUD" logging metrics update "$metric" --project="$PROJECT" --config-from-file="$file" --quiet
        else
            "$GCLOUD" logging metrics create "$metric" --project="$PROJECT" --config-from-file="$file" --quiet
        fi
    done
}

require_obs_tag() {
    if [ -z "$OBS_TAG" ]; then
        echo "set OBS_IMAGE_TAG to the commit the observability images were built from (scripts/gcp/README.md)" >&2
        exit 2
    fi
}

pushgateway() {
    require_obs_tag
    # One always-present instance holds the last push of each Data Worker cycle in memory. Internal only.
    "$GCLOUD" run deploy pushgateway "${COMMON[@]}" --image="$IMAGES/pushgateway:$OBS_TAG" \
        --service-account="$OBS_SA" --ingress=internal --allow-unauthenticated \
        --port=9091 --cpu=1 --memory=128Mi --min-instances=1 --max-instances=1 --timeout=60
}

observability() {
    require_obs_tag
    pushgateway
    # One always-on instance (CPU always allocated: Prometheus scrapes and Alertmanager notifies between requests).
    # 1.125 vCPU / 1.5 GiB in total; 26 Nov 2026 at 1.75 vCPU / 1.6 GiB used 5.7 % CPU and 41 % memory (p95).
    # Grafana is the public ingress container (login required); Prometheus, Alertmanager and the exporters are
    # sidecars on localhost and cannot be reached from outside. CACHE_API_SCRAPE=1 also scrapes the Cache API's
    # /metrics; that keeps the Cache API warm (cost), so use it only during load tests (issue #111).
    local cache_api="" probes
    local alertmanager_secret=() mounts=""
    if [ "${CACHE_API_SCRAPE:-0}" = 1 ]; then cache_api="$(url cache-api)"; fi
    # Notification receiver (alertmanager.example.yml): the config, and the SMTP password it reads from a file, so
    # the password is never part of the config text. Without alertmanager-config, alerts are only shown in Grafana.
    if "$GCLOUD" secrets describe alertmanager-config --project="$PROJECT" >/dev/null 2>&1; then
        mounts="/etc/alertmanager/secret/alertmanager.yml=alertmanager-config:latest"
        if "$GCLOUD" secrets describe alertmanager-smtp-password --project="$PROJECT" >/dev/null 2>&1; then
            mounts="$mounts,/etc/alertmanager/smtp/password=alertmanager-smtp-password:latest"
        fi
        alertmanager_secret=(--set-secrets="$mounts"
                             --set-env-vars=ALERTMANAGER_CONFIG=/etc/alertmanager/secret/alertmanager.yml)
    fi
    probes="gateway=$(url gateway)/gateway-health,frontend=$(url frontend)/"
    local prefixes=(run.googleapis.com/request_count run.googleapis.com/request_latencies
                    run.googleapis.com/container/instance_count run.googleapis.com/container/cpu/utilizations
                    run.googleapis.com/container/memory/utilizations run.googleapis.com/job/completed_execution_count
                    cloudsql.googleapis.com/database/up cloudsql.googleapis.com/database/cpu/utilization
                    cloudsql.googleapis.com/database/postgresql/num_backends logging.googleapis.com/user/sippolusi_)
    local args="--google.project-ids=$PROJECT,--monitoring.aggregate-deltas,--monitoring.metrics-interval=5m" prefix
    for prefix in "${prefixes[@]}"; do args="$args,--monitoring.metrics-prefixes=$prefix"; done
    "$GCLOUD" run deploy observability "${COMMON[@]}" \
        --service-account="$OBS_SA" --ingress=all --allow-unauthenticated "${VPC[@]}" \
        --min-instances=1 --max-instances=1 --no-cpu-throttling --timeout=300 \
        --container=grafana --image="$IMAGES/grafana:$OBS_TAG" --port=3000 --cpu=0.25 --memory=384Mi \
        --set-secrets=GF_SECURITY_ADMIN_PASSWORD=grafana-admin-password:latest \
        --set-env-vars="GF_SECURITY_ADMIN_USER=admin,GF_SERVER_ROOT_URL=$(url observability),PROMETHEUS_URL=http://localhost:9090,ALERTMANAGER_URL=http://localhost:9093" \
        --container=prometheus --image="$IMAGES/prometheus:$OBS_TAG" --cpu=0.5 --memory=768Mi \
        --set-env-vars="^@^BACKEND_URL=@CACHE_API_URL=$cache_api@PUSHGATEWAY_URL=$(url pushgateway)@ALERTMANAGER_URL=http://localhost:9093@STACKDRIVER_URL=http://localhost:9255@BLACKBOX_URL=http://localhost:9115@PROBE_URLS=$probes@RETENTION_SIZE=256MB@EXTERNAL_URL=$(url observability)" \
        --container=alertmanager --image="$IMAGES/alertmanager:$OBS_TAG" --cpu=0.125 --memory=128Mi \
        "${alertmanager_secret[@]}" \
        --container=stackdriver --image=docker.io/prometheuscommunity/stackdriver-exporter:v0.19.0 \
        --cpu=0.125 --memory=128Mi --args="$args" \
        --container=blackbox --image=docker.io/prom/blackbox-exporter:v0.29.0 --cpu=0.125 --memory=128Mi
}

case "${1:-}" in
    registry) registry ;;
    cache-api) cache_api ;;
    gateway) gateway ;;
    frontend) frontend ;;
    data-worker) data_worker ;;
    model-register) model_register ;;
    observability-setup) observability_setup ;;
    observability) observability ;;
    all) registry; cache_api; gateway; frontend; data_worker; model_register ;;
    *) echo "usage: $0 <registry|cache-api|gateway|frontend|data-worker|model-register|all|observability-setup|observability>" >&2
       exit 2 ;;
esac
