# Dockerized PhishGuard API

This packages the existing FastAPI + TensorFlow inference service as a
single-worker container. The container is deployment preparation, not an
authorization to deploy publicly.

```text
Chrome Extension -> host port 8000 -> Docker -> FastAPI -> CNN-BiLSTM
                                                   |
                                                   -> SQLite volume
```

The model is copied into the image. The prediction database is kept in a named
Docker volume so container recreation does not erase logs.

## Required Artifacts

Before building, obtain the inference artifacts from an authorized source and
review their integrity/provenance. The project has not established that its
current local model/tokenizer pair has trustworthy historical training
provenance. The image build requires:

```text
test_results/cnn_bilstm/best_model.h5
ml/artifacts/tokenizer.json
```

The H5 model is intentionally ignored by Git because it is a large binary. Do
not use a DVC restore while the pointer/workspace mismatch is unresolved.

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

The local Compose service binds only to host loopback port 8000. Its startup
uses `PORT` when provided and defaults to 8000 for local use. The platform
health check uses `/ready`, which returns 503 if the model is unavailable.

## Test

```powershell
curl http://127.0.0.1:8000/live
curl http://127.0.0.1:8000/ready
```

Open:

```text
http://127.0.0.1:8000/docs
```

Prediction:

The Compose stack writes this synthetic prediction to its persistent named
volume. Use the isolated readiness smoke section below when you do not want a
persistent test row.

```powershell
curl -X POST "http://127.0.0.1:8000/predict" `
  -H "Content-Type: application/json" `
  -d "{\"url\":\"https://optimus-smoke.invalid/check\"}"
```

Logs:

```powershell
docker compose -f docker/compose.yaml logs -f api
```

## Isolated Readiness Smoke

This path uses an ephemeral container database on `tmpfs`, not the Compose named
volume. The image checks the runtime lifecycle imports and loads the configured
model/tokenizer during its build. Run from the repository root:

```powershell
docker build -t optimus-api:readiness -f docker/Dockerfile .
docker run --rm --name optimus-readiness-test `
  -p 127.0.0.1:18000:8000 `
  --tmpfs /tmp/optimus-readiness-db:rw,noexec,nosuid,size=32m `
  -e PHISHGUARD_DATABASE_PATH=/tmp/optimus-readiness-db/predictions.sqlite `
  optimus-api:readiness
```

In another terminal, while that exact container owns port 18000:

```powershell
Invoke-RestMethod http://127.0.0.1:18000/live
Invoke-RestMethod http://127.0.0.1:18000/ready
python docker/verify_container.py
```

The verification script sends one reserved `.invalid` prediction and writes a
row only to the temporary database. Stop the container with `Ctrl+C`; `--rm`
removes it and its `tmpfs` database. Do not mount a host database or the Compose
named volume for this isolated check.

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

For hosting-platform settings, persistent storage, security prerequisites,
rollback, and current deployment blockers, see
[`docs/PRODUCTION_DEPLOYMENT.md`](../docs/PRODUCTION_DEPLOYMENT.md).
