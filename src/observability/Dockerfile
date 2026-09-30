# Railway "observability" service (Root Directory: src/observability) runs Grafana.
# Prometheus and Pushgateway have their own Dockerfiles in ./prometheus and ./pushgateway.
FROM grafana/grafana:latest
# Grafana expands ${VAR} in provisioning files; override PROMETHEUS_URL per environment
# (Railway: http://<prometheus-service>.railway.internal:9090).
ENV PROMETHEUS_URL=http://prometheus:9090
COPY grafana/provisioning /etc/grafana/provisioning
