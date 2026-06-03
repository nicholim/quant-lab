# syntax=docker/dockerfile:1
# Backtesting Dash dashboard — minimal image for the compose demo stack.
# Build context is the repo ROOT: backtesting installs the co-located
# portfolio-optimization package via `-e ../portfolio-optimization`, so both
# package dirs must be present at the expected relative path.
FROM python:3.11-slim

ENV PYTHONUNBUFFERED=1 PYTHONDONTWRITEBYTECODE=1 \
    PIP_NO_CACHE_DIR=1 PIP_DISABLE_PIP_VERSION_CHECK=1

WORKDIR /app
# Copy both packages preserving the ../portfolio-optimization relative layout.
COPY packages/portfolio-optimization/ ./packages/portfolio-optimization/
COPY packages/backtesting/ ./packages/backtesting/

WORKDIR /app/packages/backtesting
RUN pip install -r requirements.txt

EXPOSE 8050
# Serve the Dash app via gunicorn (dashboard.py exposes `server`), matching render.yaml.
CMD ["gunicorn", "dashboard:server", "--bind", "0.0.0.0:8050", "--workers", "2", "--timeout", "120"]
