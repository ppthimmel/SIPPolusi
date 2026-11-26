FROM prom/pushgateway:v1.11.4
# Holds the last push of each Data Worker cycle (src/data_worker/metrics_push.py) in memory. On Cloud Run it runs as a
# single always-on instance so the last push survives between hourly cycles (scripts/gcp/deploy.sh observability).
EXPOSE 9091
ENTRYPOINT ["/bin/sh", "-c", "exec /bin/pushgateway --web.listen-address=:${PORT:-9091}"]
