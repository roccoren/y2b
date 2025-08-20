FROM python:3.11-slim

# Environment configuration (avoid .pyc, unbuffered logs)
ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1

# Default download directory baked into the image (override with ENV if needed)
ENV YT_DLP_OUTPUT_DIR=/app/downloads

# System dependencies (ffmpeg for audio extraction, curl for healthcheck)
RUN apt-get update && apt-get install -y --no-install-recommends \
    ffmpeg \
    curl \
  && rm -rf /var/lib/apt/lists/*

# Create non-root user
RUN groupadd -r appuser && useradd -r -g appuser -u 1000 appuser

WORKDIR /app

# Leverage Docker layer caching for dependencies
COPY requirements.txt .
RUN pip install --upgrade --force-reinstall --no-cache-dir -r requirements.txt

# Copy application package
COPY app ./app
# (Optional) keep legacy entrypoint for backward compatibility if needed
# COPY yt-dlp-server.py .

# Create output directory and adjust ownership
RUN mkdir -p /app/downloads && \
    chown -R appuser:appuser /app

USER appuser

EXPOSE 8080

LABEL maintainer="your-email@domain.com" \
      description="YouTube Audio Downloader API Server (FastAPI / yt-dlp)" \
      version="1.1.0"

# Health check (liveness). Readiness served at /readiness
HEALTHCHECK --interval=30s --timeout=10s --start-period=25s --retries=3 \
    CMD curl -fsS http://localhost:8080/health || exit 1

# Run via uvicorn (faster startup & proper signal handling)
CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8080"]