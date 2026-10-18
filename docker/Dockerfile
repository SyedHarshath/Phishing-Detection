# syntax=docker/dockerfile:1
FROM python:3.10-slim-bookworm

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1

WORKDIR /app

RUN apt-get update \
    && apt-get install -y --no-install-recommends libgomp1 \
    && rm -rf /var/lib/apt/lists/*

COPY docker/requirements-docker.txt /tmp/requirements-docker.txt
RUN python -m pip install --upgrade pip \
    && python -m pip install -r /tmp/requirements-docker.txt

COPY backend ./backend
COPY ml ./ml
COPY test_results/cnn_bilstm/best_model.h5 ./test_results/cnn_bilstm/best_model.h5

RUN useradd --create-home --uid 10001 appuser \
    && mkdir -p /app/mlops/data \
    && chown -R appuser:appuser /app

USER appuser

EXPOSE 8000

CMD ["python", "-m", "uvicorn", "backend.app.main:app", "--host", "0.0.0.0", "--port", "8000", "--workers", "1"]
