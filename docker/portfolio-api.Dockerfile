# syntax=docker/dockerfile:1
# Portfolio-optimization FastAPI demo — minimal image for the compose demo stack.
# Build context is the repo ROOT.
FROM python:3.11-slim

ENV PYTHONUNBUFFERED=1 PYTHONDONTWRITEBYTECODE=1 \
    PIP_NO_CACHE_DIR=1 PIP_DISABLE_PIP_VERSION_CHECK=1

WORKDIR /app
COPY packages/portfolio-optimization/ ./
# requirements-api.txt pulls in the core requirements + fastapi/uvicorn/pydantic.
RUN pip install -r requirements-api.txt && pip install -e .

EXPOSE 8000
CMD ["uvicorn", "api.app:app", "--host", "0.0.0.0", "--port", "8000"]
