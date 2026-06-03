# syntax=docker/dockerfile:1
# Options-pricing Streamlit app — minimal image for the compose demo stack.
# Build context is the repo ROOT (so we can copy the package dir as-is).
FROM python:3.11-slim

ENV PYTHONUNBUFFERED=1 PYTHONDONTWRITEBYTECODE=1 \
    PIP_NO_CACHE_DIR=1 PIP_DISABLE_PIP_VERSION_CHECK=1

WORKDIR /app
COPY packages/options-pricing/ ./
RUN pip install -r requirements.txt

EXPOSE 8501
# Headless Streamlit, bound to all interfaces for the published port mapping.
CMD ["streamlit", "run", "app.py", \
     "--server.port", "8501", "--server.address", "0.0.0.0", \
     "--server.headless", "true", "--browser.gatherUsageStats", "false"]
