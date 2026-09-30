FROM python:3.11-slim
WORKDIR /app

# Install system dependencies for spatial libraries (gdal, etc)
RUN apt-get update && apt-get install -y libgdal-dev && rm -rf /var/lib/apt/lists/*

COPY requirements.txt .
# Railway has no GPU (training runs separately on the Tambora GPU server), so
# pin the CPU-only PyTorch build here instead of pulling multi-GB CUDA wheels.
RUN pip install --no-cache-dir torch --index-url https://download.pytorch.org/whl/cpu
RUN pip install --no-cache-dir -r requirements.txt
COPY . .

CMD ["python", "main.py"]
