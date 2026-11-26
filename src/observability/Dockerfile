# Railway "observability" service (Root Directory: src/observability) runs Grafana; same image as grafana/Dockerfile.
# Prometheus, Pushgateway and Alertmanager have their own Dockerfiles in ./prometheus, ./pushgateway, ./alertmanager.
FROM grafana/grafana:13.0.12
# Railway: PROMETHEUS_URL=http://<prometheus-service>.railway.internal:9090, likewise ALERTMANAGER_URL.
ENV PROMETHEUS_URL=http://prometheus:9090 \
    ALERTMANAGER_URL=http://alertmanager:9093 \
    GF_USERS_ALLOW_SIGN_UP=false \
    GF_AUTH_ANONYMOUS_ENABLED=false \
    GF_DASHBOARDS_DEFAULT_HOME_DASHBOARD_PATH=/var/lib/grafana/dashboards/sippolusi-overview.json
COPY grafana/provisioning /etc/grafana/provisioning
COPY grafana/dashboards /var/lib/grafana/dashboards
