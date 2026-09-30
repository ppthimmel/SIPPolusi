# Model Registry

MLflow tracking server and model registry (PNF-04, PNF-10). Stores experiment
runs, versions, and aliases (`production`) for two registered models:

- `spatial-downscaling` — ST-GNN used by the Spatial Downscaling Model
- `morl-route-policy` — actor-critic policy used by the Multi-Objective Route Model

Backend store: schema `mlflow` on the shared PostgreSQL/PostGIS instance
(the schema is created by the model registry container on start; cache schemas are SQLAlchemy models in the Data Worker). Artifact store: S3-compatible object
storage (MinIO locally, Railway-compatible bucket in production).

Inference services (Backend, Data Worker) only read artifacts at a pinned
version/alias via the MLflow REST API; only the Model Training Pipeline
writes new versions.
