FROM prom/prometheus:v3.15.1
# Targets are injected at start (entrypoint.sh) so the same image works in Docker Compose and on Cloud Run.
# Compose defaults below; Cloud Run sets https://<service>-<project number>.<region>.run.app URLs (scripts/gcp/deploy.sh).
ENV BACKEND_URL=http://backend:8000 \
    CACHE_API_URL=http://cache_api:8000 \
    PUSHGATEWAY_URL=http://pushgateway:9091 \
    ALERTMANAGER_URL=http://alertmanager:9093 \
    STACKDRIVER_URL= \
    BLACKBOX_URL=http://blackbox:9115 \
    PROBE_URLS=gateway=http://gateway:8080/healthz
COPY prometheus.yml /etc/prometheus/prometheus.yml
COPY rules/ /etc/prometheus/rules/
COPY entrypoint.sh /etc/prometheus/entrypoint.sh
EXPOSE 9090
ENTRYPOINT ["/bin/sh", "/etc/prometheus/entrypoint.sh"]
