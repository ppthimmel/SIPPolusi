FROM python:3.11-slim

# Unbuffered output so logs reach Railway as they happen, not when the buffer fills.
ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1

WORKDIR /app

# Install system dependencies for spatial libraries (gdal, etc)
RUN apt-get update && apt-get install -y libgdal-dev libgomp1 && rm -rf /var/lib/apt/lists/*

COPY requirements.txt .
# Railway has no GPU (training runs separately on the Tambora GPU server), so
# pin the CPU-only PyTorch build here instead of pulling multi-GB CUDA wheels.
RUN pip install --no-cache-dir torch --index-url https://download.pytorch.org/whl/cpu
RUN pip install --no-cache-dir -r requirements.txt
# .env, data latih (dataset_processed/), dan venv lokal dikecualikan oleh .dockerignore
COPY . .

# Gagalkan build, bukan cron berikutnya, bila modul atau dependensi hilang.
# (Konfigurasi dan basis data baru diperiksa saat start; variabel tidak ada saat build.)
RUN python -c "import sys, preflight; p = preflight.import_problems(); [print('build check gagal:', x, file=sys.stderr) for x in p]; sys.exit(1 if p else 0)"

# Satu siklus akuisisi lalu keluar; dijadwalkan oleh cron (railway.json: 15 * * * *).
# Untuk proses menetap tanpa cron: python main.py serve
# Pemeriksaan awal (konfigurasi, impor, basis data): python main.py check
CMD ["python", "main.py", "cycle"]
