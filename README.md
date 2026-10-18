# DEPHIDES: Deep Learning Based Phishing URL Detection System

A sequence-based lexical analysis tool for detecting phishing URLs with a
character-level CNN + BiLSTM model.

This project builds on the DEPHIDES research direction by adding bidirectional
sequence modeling so the detector can capture forward and backward URL context,
including typosquatting and phishing-specific lexical patterns.

## Quick Start

Create and activate a Python environment, then install the project dependencies:

```powershell
python -m venv .venv
.\.venv\Scripts\Activate.ps1
python -m pip install --upgrade pip
python -m pip install -r requirements.txt
```

Run the unit tests:

```powershell
python -m unittest discover -s test -v
```

Start the FastAPI service:

```powershell
python -m uvicorn backend.app.main:app --reload
```

The API will be available at:

```text
http://127.0.0.1:8000
```

## Model Artifacts

Inference expects these local artifacts:

```text
test_results/cnn_bilstm/best_model.h5
ml/artifacts/tokenizer.json
```

Large model files are intentionally ignored by Git. Restore them from the
approved DVC/artifact source before running predictions or building the Docker
image. If artifacts are missing, `/health` remains available and reports a
degraded status.

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

Start the backend on port 8000, then load the `extension/` folder as an
unpacked Chrome or Edge extension.
