FROM python:3.11-slim

WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY init_schema.py .

EXPOSE 5000

# MLFLOW_BACKEND_STORE_URI: postgresql://.../sippolusi?options=-csearch_path=mlflow (schema mlflow)
# MLFLOW_ARTIFACT_ROOT: mlflow-artifacts:/ . Clients upload and download through this server
# (proxied artifacts), so only this service holds object-storage credentials.
# MLFLOW_ARTIFACTS_DESTINATION: s3://<bucket>/artifacts (S3-compatible object storage; defaults to
# MLFLOW_ARTIFACT_ROOT, which keeps the direct-to-S3 setup of the compose files working; the bucket,
# MLFLOW_S3_ENDPOINT_URL, AWS_* keys and AWS_DEFAULT_REGION are set on this service only).
# [::] listens on IPv6 and IPv4: callers reach it over Railway private
# networking (modelregistry.railway.internal for the service model_registry,
# IPv6) as well as in compose.
CMD python init_schema.py && \
    mlflow server \
    --host "[::]" \
    --port 5000 \
    --backend-store-uri "$MLFLOW_BACKEND_STORE_URI" \
    --serve-artifacts \
    --default-artifact-root "$MLFLOW_ARTIFACT_ROOT" \
    --artifacts-destination "${MLFLOW_ARTIFACTS_DESTINATION:-$MLFLOW_ARTIFACT_ROOT}"
