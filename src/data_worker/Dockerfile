FROM python:3.11-slim
WORKDIR /app

# Install system dependencies for spatial libraries (gdal, etc)
RUN apt-get update && apt-get install -y libgdal-dev && rm -rf /var/lib/apt/lists/*

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt
COPY . .

# Satu siklus akuisisi lalu keluar; dijadwalkan oleh cron (railway.json: 15 * * * *).
# Untuk proses menetap tanpa cron: python main.py serve
CMD ["python", "main.py", "cycle"]
