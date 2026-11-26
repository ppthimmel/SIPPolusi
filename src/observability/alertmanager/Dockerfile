FROM prom/alertmanager:v0.34.2
# alertmanager.yml (baked in) routes alerts but sends no notification: firing alerts are visible in the
# Alertmanager UI and in Grafana. To notify people, mount a full config at ALERTMANAGER_CONFIG (on Cloud Run: the
# Secret Manager secret alertmanager-config, see scripts/gcp/README.md); alertmanager.example.yml shows one.
ENV ALERTMANAGER_CONFIG=/etc/alertmanager/alertmanager.yml
COPY alertmanager.yml /etc/alertmanager/alertmanager.yml
EXPOSE 9093
ENTRYPOINT ["/bin/sh", "-c", "exec /bin/alertmanager --config.file=\"$ALERTMANAGER_CONFIG\" --storage.path=/alertmanager --web.listen-address=:9093 ${EXTERNAL_URL:+--web.external-url=$EXTERNAL_URL}"]
