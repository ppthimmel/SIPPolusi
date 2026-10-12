# OSM Cache DB API (src/backend/osm). Build context is this directory:
#   docker build -t sippolusi-backend-osm src/backend/osm
#   docker run --rm -p 8001:8000 --env-file src/backend/.env sippolusi-backend-osm
# Tests run in their own stage:
#   docker build --target test -t sippolusi-backend-osm-test src/backend/osm
#   docker run --rm sippolusi-backend-osm-test

FROM python:3.11-slim AS base

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1

# The package is imported as `osm`, so it lives at /app/osm with /app as cwd.
WORKDIR /app
COPY requirements.txt osm/requirements.txt
RUN pip install -r osm/requirements.txt


FROM base AS test
COPY requirements-dev.txt osm/requirements-dev.txt
RUN pip install -r osm/requirements-dev.txt
COPY . osm/
WORKDIR /app/osm
# Integration tests also run when DATABASE_URL is passed (docker run -e/--env-file).
CMD ["pytest", "-p", "no:cacheprovider"]


FROM base AS runtime
COPY *.py osm/
RUN useradd --create-home --uid 10001 app
USER app

# Configuration comes from environment variables (DATABASE_URL, OSM_*), not a
# baked-in .env. Railway provides PORT.
EXPOSE 8000
HEALTHCHECK --interval=30s --timeout=5s --start-period=10s --retries=3 \
    CMD python -c "import os, urllib.request; urllib.request.urlopen(f'http://127.0.0.1:{os.environ.get(\"PORT\", \"8000\")}/health', timeout=4)"

# Empty host = all interfaces (IPv4 for Railway's public edge, IPv6 for its
# private network), same as src/backend/Dockerfile. exec lets uvicorn receive SIGTERM.
CMD exec uvicorn osm.app:app --host "" --port "${PORT:-8000}"
