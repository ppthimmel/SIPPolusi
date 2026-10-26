FROM python:3.11-slim

# Unbuffered output so logs reach Railway as they happen, not when the buffer fills.
ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1

WORKDIR /app

# Copy requirements and install dependencies. Railway has no GPU (training
# runs separately on the Tambora GPU server), so pin the CPU-only PyTorch
# build here instead of pulling multi-GB CUDA wheels.
COPY requirements.txt .
RUN pip install --no-cache-dir torch --index-url https://download.pytorch.org/whl/cpu
RUN pip install --no-cache-dir -r requirements.txt

# Copy the rest of the backend source code (.env and caches excluded by .dockerignore)
COPY . .

# Fail the build, not the deploy, when a module or dependency is missing.
RUN python -c "import main" \
    || { echo "build check failed: 'import main' raised; see the traceback above (missing package in requirements.txt?)" >&2; exit 1; }

# Railway provides PORT; 8000 is the local default.
EXPOSE 8000

# serve.py validates configuration (exit 2 with the reason on failure), then
# starts uvicorn on $PORT. `python serve.py --check` only validates.
CMD ["python", "serve.py"]
