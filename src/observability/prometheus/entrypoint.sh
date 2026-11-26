#!/bin/sh
# Write Prometheus file_sd target files from env, then start Prometheus. An empty URL leaves the job without targets.
#   BACKEND_URL, CACHE_API_URL, PUSHGATEWAY_URL, ALERTMANAGER_URL, STACKDRIVER_URL, BLACKBOX_URL  e.g. https://x.run.app
#   PROBE_URLS  comma-separated name=url pairs probed through BLACKBOX_URL, e.g. gateway=https://gw/gateway-health
#   RETENTION_TIME (15d), RETENTION_SIZE (512MB), EXTERNAL_URL (optional, used in alert links)
# Listens on 9090, not $PORT: on Cloud Run it is a sidecar and $PORT belongs to the ingress container (Grafana).
set -eu
SD=/tmp/sd
mkdir -p "$SD"

# "https://host[:port][/path]" -> {"targets":["host:port"],"labels":{"__scheme__":"https"<extra>}}
entry() {
    url="$1"; extra="${2:-}"
    scheme="${url%%://*}"; rest="${url#*://}"; host="${rest%%/*}"
    case "$host" in
        *:*) ;;
        *) if [ "$scheme" = https ]; then host="$host:443"; else host="$host:80"; fi ;;
    esac
    printf '{"targets":["%s"],"labels":{"__scheme__":"%s"%s}}' "$host" "$scheme" "$extra"
}

job() {  # job <file> <url>
    if [ -n "$2" ]; then printf '[%s]\n' "$(entry "$2")" > "$SD/$1.json"; else echo '[]' > "$SD/$1.json"; fi
}

job backend "${BACKEND_URL:-}"
job cache_api "${CACHE_API_URL:-}"
job pushgateway "${PUSHGATEWAY_URL:-}"
job alertmanager "${ALERTMANAGER_URL:-}"
job stackdriver "${STACKDRIVER_URL:-}"

# Probes: the target is the probed URL; the scrape goes to blackbox_exporter (relabelled in prometheus.yml).
probes=""
if [ -n "${BLACKBOX_URL:-}" ] && [ -n "${PROBE_URLS:-}" ]; then
    blackbox="${BLACKBOX_URL#*://}"; blackbox="${blackbox%%/*}"
    old_ifs="$IFS"; IFS=','
    for pair in $PROBE_URLS; do
        name="${pair%%=*}"; url="${pair#*=}"
        item=$(printf '{"targets":["%s"],"labels":{"__blackbox_address__":"%s","probe":"%s"}}' "$url" "$blackbox" "$name")
        probes="${probes:+$probes,}$item"
    done
    IFS="$old_ifs"
fi
printf '[%s]\n' "$probes" > "$SD/probes.json"

set -- --config.file=/etc/prometheus/prometheus.yml \
    --storage.tsdb.path=/prometheus \
    --storage.tsdb.retention.time="${RETENTION_TIME:-15d}" \
    --storage.tsdb.retention.size="${RETENTION_SIZE:-512MB}" \
    --web.listen-address=":9090"
[ -n "${EXTERNAL_URL:-}" ] && set -- "$@" --web.external-url="$EXTERNAL_URL"
exec /bin/prometheus "$@"
