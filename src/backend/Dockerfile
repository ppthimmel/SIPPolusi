FROM python:3.11-slim

WORKDIR /app

# Copy requirements and install dependencies. Railway has no GPU (training
# runs separately on the Tambora GPU server), so pin the CPU-only PyTorch
# build here instead of pulling multi-GB CUDA wheels.
COPY requirements.txt .
RUN pip install --no-cache-dir torch --index-url https://download.pytorch.org/whl/cpu
RUN pip install --no-cache-dir -r requirements.txt

# Copy the rest of the backend source code
COPY . .

# Expose port (Railway provides PORT env variable)
EXPOSE 8000

# Command to run the application using uvicorn
CMD ["uvicorn", "main:app", "--host", "0.0.0.0", "--port", "8000"]
