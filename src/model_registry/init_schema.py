"""Create the isolated `mlflow` schema before MLflow connects with search_path=mlflow."""
import os

from sqlalchemy import create_engine, text

engine = create_engine(os.environ["MLFLOW_BACKEND_STORE_URI"].split("?")[0])
with engine.begin() as conn:
    conn.execute(text("CREATE SCHEMA IF NOT EXISTS mlflow"))
