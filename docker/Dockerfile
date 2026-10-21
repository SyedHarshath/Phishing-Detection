# syntax=docker/dockerfile:1
FROM python:3.10-slim-bookworm

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    PHISHGUARD_ENV=production \
    PHISHGUARD_MODEL_PATH=/app/test_results/cnn_bilstm/best_model.h5 \
    PHISHGUARD_TOKENIZER_PATH=/app/ml/artifacts/tokenizer.json \
    PHISHGUARD_MODEL_VERSION=1.0.0

WORKDIR /app

RUN apt-get update \
    && apt-get install -y --no-install-recommends libgomp1 \
    && rm -rf /var/lib/apt/lists/*

COPY docker/requirements-docker.txt /tmp/requirements-docker.txt
RUN python -m pip install -r /tmp/requirements-docker.txt

COPY backend/app/__init__.py ./backend/app/__init__.py
COPY backend/app/config.py ./backend/app/config.py
COPY backend/app/database.py ./backend/app/database.py
COPY backend/app/main.py ./backend/app/main.py
COPY backend/app/schemas.py ./backend/app/schemas.py
COPY backend/app/services/__init__.py ./backend/app/services/__init__.py
COPY backend/app/services/prediction.py ./backend/app/services/prediction.py
COPY ml/__init__.py ./ml/__init__.py
COPY ml/prediction.py ./ml/prediction.py
COPY ml/preprocessing.py ./ml/preprocessing.py
COPY ml/artifacts/tokenizer.json ./ml/artifacts/tokenizer.json
COPY mlops/__init__.py ./mlops/__init__.py
COPY mlops/data/__init__.py ./mlops/data/__init__.py
COPY mlops/data/connection.py ./mlops/data/connection.py
COPY mlops/data/lifecycle.py ./mlops/data/lifecycle.py
COPY src/dl_models.py ./src/dl_models.py
COPY test_results/cnn_bilstm/best_model.h5 ./test_results/cnn_bilstm/best_model.h5

# Validate the configured tokenizer/model pair during the build without training.
RUN python -c "from ml.prediction import PhishingPredictor; PhishingPredictor()"

RUN useradd --create-home --uid 10001 appuser \
    && mkdir -p /var/lib/phishguard \
    && chown -R appuser:appuser /app /var/lib/phishguard

# Fail the image build if the API's startup schema migration cannot import.
# Import-only check; this build-scoped URL is never used to open a connection.
RUN DATABASE_URL=postgresql://127.0.0.1/optimus-build-check python -c "from backend.app.database import initialize_database; from mlops.data.lifecycle import initialize_schema, record_feedback"

USER appuser

EXPOSE 8000

CMD ["sh", "-c", "exec python -m uvicorn backend.app.main:app --host 0.0.0.0 --port \"${PORT:-8000}\" --workers 1 --log-level \"${LOG_LEVEL:-info}\""]
