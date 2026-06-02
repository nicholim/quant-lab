# syntax=docker/dockerfile:1
# Real-Time Market Data Pipeline — streaming ingestion daemon (background worker).
# Not a web service: it consumes a websocket feed and writes to Redis + TimescaleDB.

FROM python:3.11-slim

# Faster, cleaner Python in containers.
ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1

WORKDIR /app

# Install dependencies first to leverage Docker layer caching.
COPY requirements.txt ./
RUN pip install --no-cache-dir -r requirements.txt

# Application code.
COPY main.py ./
COPY src ./src

# Run as a non-root user.
RUN useradd --create-home --uid 10001 appuser
USER appuser

# Daemon entry point. Config is read from environment variables (see .env.example).
CMD ["python", "main.py"]
