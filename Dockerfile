FROM python:3.11-slim

WORKDIR /app

# Install system deps
RUN apt-get update && apt-get install -y --no-install-recommends \
    ffmpeg \
 && rm -rf /var/lib/apt/lists/*

# Copy project
COPY . /app

# Install python deps
RUN pip install --no-cache-dir fastapi "uvicorn[standard]" yt-dlp diskcache python-dotenv pydantic

EXPOSE 8000

CMD ["uvicorn", "src.api:app", "--host", "0.0.0.0", "--port", "8000"]
