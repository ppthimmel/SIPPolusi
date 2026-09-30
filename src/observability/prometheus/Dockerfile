FROM prom/prometheus:latest
# Scrape targets are injected at start so the same image works locally and on Railway
# (Railway: <service>.railway.internal:<port>).
ENV BACKEND_TARGET=backend:8000 \
    PUSHGATEWAY_TARGET=pushgateway:9091
COPY prometheus.yml /etc/prometheus/prometheus.yml.tmpl
EXPOSE 9090
ENTRYPOINT ["/bin/sh", "-c", "sed -e \"s|__BACKEND_TARGET__|$BACKEND_TARGET|g\" -e \"s|__PUSHGATEWAY_TARGET__|$PUSHGATEWAY_TARGET|g\" /etc/prometheus/prometheus.yml.tmpl > /tmp/prometheus.yml && exec /bin/prometheus --config.file=/tmp/prometheus.yml --storage.tsdb.path=/prometheus"]
