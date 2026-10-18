# Dockerized PhishGuard API

This packages the existing FastAPI + TensorFlow inference service as a
container.

```text
Chrome Extension -> host port 8000 -> Docker -> FastAPI -> CNN-BiLSTM
                                                   |
                                                   -> SQLite volume
```

The model is copied into the image. The prediction database is kept in a named
Docker volume so container recreation does not erase logs.

## Required Artifacts

Before building, restore the approved inference artifacts:

```text
test_results/cnn_bilstm/best_model.h5
ml/artifacts/tokenizer.json
```

The H5 model is intentionally ignored by Git because it is a large binary. Use
the approved DVC/artifact source to restore it before running `docker compose
build`.

## Build

Run from the PhishGuard project root:

```powershell
docker compose -f docker/compose.yaml build
```

## Start

```powershell
docker compose -f docker/compose.yaml up -d
docker compose -f docker/compose.yaml ps
```

The container exposes host port 8000, so the existing extension endpoint
`http://127.0.0.1:8000/predict` can remain unchanged.

## Test

```powershell
curl http://127.0.0.1:8000/health
```

Open:

```text
http://127.0.0.1:8000/docs
```

Prediction:

```powershell
curl -X POST "http://127.0.0.1:8000/predict" `
  -H "Content-Type: application/json" `
  -d "{\"url\":\"https://example.com\"}"
```

Logs:

```powershell
docker compose -f docker/compose.yaml logs -f api
```

Smoke test:

```powershell
python docker/verify_container.py
```

Stop:

```powershell
docker compose -f docker/compose.yaml down
```

Remove the persisted SQLite volume too:

```powershell
docker compose -f docker/compose.yaml down -v
```

## Why One Worker?

Each Uvicorn worker is a separate process. With the current TensorFlow
predictor, multiple workers can load multiple model copies into memory. One
worker is used until a deliberate model-server/scaling design is introduced.

## CPU Image

This is a CPU-compatible container. It is deliberately not coupled to a
Windows-native CUDA setup. GPU containers are better handled as a separate
Linux/WSL2 + NVIDIA Container Toolkit deployment variant.

## Container Boundary

Included:

- FastAPI
- TensorFlow inference
- tokenizer
- production H5 model

Excluded:

- training dataset
- DVC cache
- MLflow tracking data
- Chrome extension source

This keeps the serving image focused on inference.
