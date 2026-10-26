# Cache API: /v1/osm (src/backend/osm), /v1/pollution (src/backend/pollution),
# /v1/routes/solver-input (src/backend/planner) and /v1/routes/solve (src/backend/solver,
# with the A* baseline of src/backend/route_model). Build context is src/backend so the
# packages are available; only the files allowed by osm/Dockerfile.dockerignore are sent. From the repo root:
#   docker build -f src/backend/osm/Dockerfile -t sippolusi-backend-osm src/backend
#   docker run --rm -p 8001:8000 --env-file src/backend/.env sippolusi-backend-osm
# Tests run in their own stage:
#   docker build -f src/backend/osm/Dockerfile --target test -t sippolusi-backend-osm-test src/backend
#   docker run --rm sippolusi-backend-osm-test

FROM python:3.11-slim AS base

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1

# Packages are imported as `osm`, `pollution`, `planner`, `solver` and `route_model`,
# so they live under /app with /app as cwd.
WORKDIR /app
COPY osm/requirements.txt osm/requirements.txt
RUN pip install -r osm/requirements.txt


FROM base AS test
COPY osm/requirements-dev.txt osm/requirements-dev.txt
RUN pip install -r osm/requirements-dev.txt
COPY osm/ osm/
COPY pollution/ pollution/
COPY planner/ planner/
COPY solver/ solver/
COPY route_model/*.py route_model/
# Integration tests also run when DATABASE_URL is passed (docker run -e/--env-file).
# Cache-flow tests (TEST_DATABASE_URL + src/data_worker) are skipped in this image.
CMD ["sh", "-c", "s=0; for p in osm pollution planner solver; do cd /app/$p && pytest -p no:cacheprovider || s=1; done; exit $s"]


FROM base AS runtime
COPY osm/*.py osm/
COPY pollution/*.py pollution/
COPY planner/*.py planner/
COPY solver/*.py solver/
COPY route_model/*.py route_model/
RUN useradd --create-home --uid 10001 app
USER app

# Configuration comes from environment variables (DATABASE_URL, OSM_*, POLLUTION_*, ROUTE_SOLVER_*),
# not a baked-in .env. Railway provides PORT.
EXPOSE 8000
HEALTHCHECK --interval=30s --timeout=5s --start-period=10s --retries=3 \
    CMD python -c "import os, urllib.request; urllib.request.urlopen(f'http://127.0.0.1:{os.environ.get(\"PORT\", \"8000\")}/health', timeout=4)"

# Empty host = all interfaces (IPv4 for Railway's public edge, IPv6 for its
# private network), same as src/backend/Dockerfile. exec lets uvicorn receive SIGTERM.
CMD exec uvicorn osm.app:app --host "" --port "${PORT:-8000}"
