FROM grafana/grafana:13.0.12
# Grafana expands ${VAR} in provisioning files; override per environment
# (Cloud Run: https://prometheus-<project number>.<region>.run.app, see scripts/gcp/deploy.sh).
ENV PROMETHEUS_URL=http://prometheus:9090 \
    ALERTMANAGER_URL=http://alertmanager:9093 \
    GF_USERS_ALLOW_SIGN_UP=false \
    GF_AUTH_ANONYMOUS_ENABLED=false \
    GF_DASHBOARDS_DEFAULT_HOME_DASHBOARD_PATH=/var/lib/grafana/dashboards/sippolusi-overview.json
COPY provisioning /etc/grafana/provisioning
COPY dashboards /var/lib/grafana/dashboards
