FROM python:3.11-slim

WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY init_schema.py .

EXPOSE 5000

# MLFLOW_BACKEND_STORE_URI: postgresql://.../sippolusi?options=-csearch_path=mlflow (schema mlflow)
# MLFLOW_ARTIFACT_ROOT: s3://<bucket>/artifacts (S3-compatible object storage)
CMD python init_schema.py && \
    mlflow server \
    --host 0.0.0.0 \
    --port 5000 \
    --backend-store-uri "$MLFLOW_BACKEND_STORE_URI" \
    --default-artifact-root "$MLFLOW_ARTIFACT_ROOT"
