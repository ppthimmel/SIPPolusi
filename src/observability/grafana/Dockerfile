FROM grafana/grafana:latest
# Grafana expands ${VAR} in provisioning files; override PROMETHEUS_URL per environment
# (Railway: http://<prometheus-service>.railway.internal:9090).
ENV PROMETHEUS_URL=http://prometheus:9090
COPY provisioning /etc/grafana/provisioning
