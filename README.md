# DEPHIDES: Deep Learning Based Phishing URL Detection System

A sequence-based lexical analysis tool for detecting phishing URLs with a
character-level CNN + BiLSTM model.

This project builds on the DEPHIDES research direction by adding bidirectional
sequence modeling so the detector can capture forward and backward URL context,
including typosquatting and phishing-specific lexical patterns.

## Quick Start

Create and activate a Python environment, then install the project dependencies.
The general development requirements are not fully pinned; use the Docker
runtime constraints for a repeatable inference container. This repository does
not automatically load `.env` files.

```powershell
python -m venv .venv
.\.venv\Scripts\Activate.ps1
python -m pip install -r requirements.txt
```

Run the unit tests:

```powershell
python -m unittest discover -s test -v
```

The suite includes a tiny synthetic TensorFlow fit and temporary registry
promotion tests; those do not use the incumbent or production registry but do
exercise isolated training/promotion code paths.

Start FastAPI with an isolated database (PowerShell):

```powershell
$env:PHISHGUARD_DATABASE_PATH = Join-Path $env:TEMP 'optimus-local\predictions.sqlite'
python -m uvicorn backend.app.main:app --host 127.0.0.1 --port 8000
```

The API will be available at:

```text
http://127.0.0.1:8000
```

## Model Artifacts and Data Readiness

Inference expects these local artifacts:

```text
test_results/cnn_bilstm/best_model.h5
ml/artifacts/tokenizer.json
```

Large model files are intentionally ignored by Git. Obtain them through an
authorized artifact source and verify them before running predictions/building
the Docker image. The local dataset/DVC mismatch and incumbent training history
remain unresolved; current artifact hashes do not prove historical provenance.
If artifacts are missing, `/health` remains available and reports degraded;
`/ready` returns 503 until model inference is ready.

## Architecture

```text
Input URL
-> character tokenization
-> embedding layer
-> CNN feature extraction
-> batch normalization + dropout
-> BiLSTM layer
-> dense softmax classifier
-> prediction
```

## Repository Structure

```text
assets/                  diagrams and presentation graphics
backend/                 FastAPI inference API
dataset/                 DVC-tracked dataset metadata
docker/                  container build and runtime files
extension/               Chrome/Edge extension
ml/                      shared preprocessing and prediction code
mlops/                   DVC, MLflow, and monitoring helpers
src/                     training, evaluation, and CLI scripts
test/                    unit and smoke tests
test_results/            local evaluation outputs and model artifacts
```

## CLI Prediction

```powershell
python -m src.predict
```

## Browser Extension

See [the professor demo](docs/PROFESSOR_DEMO_README.md), [deployment preparation](docs/PRODUCTION_DEPLOYMENT.md), and [extension release procedure](docs/EXTENSION_RELEASE.md). The extension requires a first-use data notice acceptance before sending URLs. Public deployment and Chrome Web Store release remain blocked by the readiness report.

## Deployment and Privacy

- [Production readiness report](docs/PRODUCTION_READINESS_REPORT.md)
- [Privacy policy draft — requires completion and hosting](docs/PRIVACY_POLICY_DRAFT.md)
- [Chrome Web Store checklist](docs/CHROME_WEB_STORE_CHECKLIST.md)
- [Data handling notes](docs/DATA_AND_PRIVACY.md)
