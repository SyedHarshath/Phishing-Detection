# Phishing Detection Project Status Report

Date: 2026-10-05

## 1. Project overview

This repository is a phishing URL detection project built around a deep learning model, a FastAPI backend, and a browser-extension workflow. The system is intended to detect phishing URLs using a CNN + BiLSTM architecture while preserving the existing production inference flow and monitoring stack.

The important project boundaries are:

- Production model and tokenizer path must remain unchanged.
- FastAPI application and extension flow must continue working.
- MLflow tracking must use the local remote server at `http://127.0.0.1:5000`.
- Docker is not part of the active project requirements.
- Full production retraining is intentionally deferred until the dataset is verified and trusted.

## 2. What has been completed

### 2.1 MLflow and monitoring foundation

The MLflow server setup and tracking behavior were validated and stabilized.

Files involved:
- [mlops/mlflow_config.py](./mlops/mlflow_config.py)
- [mlops/monitoring/monitor.py](./mlops/monitoring/monitor.py)
- [mlops/monitoring/drift.py](./mlops/monitoring/drift.py)

Completed work:
- Confirmed that MLflow is tracking to the remote local server at `http://127.0.0.1:5000`.
- Ensured the project uses experiment name `OPTIMUS`.
- Added startup logic so the MLflow server is started automatically when it is not already running.
- Fixed import path issues so monitoring scripts run correctly from the repo root.
- Verified monitoring can read the prediction SQLite database and compute real metrics.
- Logged production monitoring metrics to MLflow.
- Implemented drift monitoring as a baseline-vs-production comparison with thresholds and artifact output.

Observed production monitoring metrics:
- prediction_count: 58
- phishing_count: 34
- legitimate_count: 24
- phishing_rate: 0.5862068965517241
- average_confidence: 97.1091379310345
- feedback_count: 1
- feedback_accuracy: 0.0
- average_url_length: 52.41379310344828

### 2.2 DVC setup and dataset tracking

DVC was initialized and used to track the dataset.

Files involved:
- [dvc.yaml](./dvc.yaml)
- [dataset/big_dataset.dvc](./dataset/big_dataset.dvc)
- [mlops/dvc/validate_dataset.py](./mlops/dvc/validate_dataset.py)
- [mlops/dvc/inspect_dataset.py](./mlops/dvc/inspect_dataset.py)
- [mlops/dvc/README.md](./mlops/dvc/README.md)
- [mlops/dvc/params.yaml](./mlops/dvc/params.yaml)
- [requirements_dvc.txt](./requirements_dvc.txt)

Completed work:
- Installed and initialized DVC in the repo root.
- Added dataset tracking for [dataset/big_dataset](./dataset/big_dataset).
- Created the dataset pointer and DVC metadata.
- Added a proper binary-safe dataset inspector to avoid guessing file formats.
- Added a safe, configurable DVC stage layout with validation and smoke-training stages.

### 2.3 Safe dataset inspection and parser validation

The project’s earlier data assumptions were not trustworthy. We inspected the actual dataset bytes instead of assuming a clean TSV file format.

Key discoveries:
- [dataset/big_dataset/train.txt](./dataset/big_dataset/train.txt) is not a valid text dataset; it is a zero-filled binary file with 100% NUL bytes.
- The Validation and Test splits are valid UTF-8 text files with tab-delimited rows of the format `label<TAB>URL`.
- The inferred schema for parseable splits is:
  - one record per line
  - field delimiter: tab
  - field order: `label`, `url`
  - labels: `phishing`, `legitimate`
  - no header line

This prevented a full training pipeline from being declared valid, because the training split is corrupted or unusable as-is.

Files involved:
- [mlops/dvc/dataset_schema_report.json](./mlops/dvc/dataset_schema_report.json)
- [mlops/dvc/inspect_dataset.py](./mlops/dvc/inspect_dataset.py)

### 2.4 Safe loader and smoke-test workflow

The project now has a safer, bounded training path that does not touch production artifacts.

Files involved:
- [src/data_loader.py](./src/data_loader.py)
- [src/train_smoke.py](./src/train_smoke.py)
- [src/evaluate_smoke.py](./src/evaluate_smoke.py)

Completed work:
- Added a strict dataset loader that validates labels and rejects malformed records safely.
- Added a smoke-test workflow that uses a tiny bounded subset and requires the data to be parseable.
- Ensures the tokenizer is fit only on the training data.
- Keeps all candidate output under [artifacts/candidate_cnn_bilstm](./artifacts/candidate_cnn_bilstm).
- Protects the production model and tokenizer from accidental overwrite.
- Does not allow full-scale training or promotion to production.

### 2.5 Tests added

We added focused tests to protect the safe workflow.

Files involved:
- [test/test_data_loader.py](./test/test_data_loader.py)
- [test/test_smoke_training.py](./test/test_smoke_training.py)

Covered tests:
- dataset parsing
- valid labels
- malformed records
- binary rejection for zero-filled data
- tokenizer fit-on-train-only behavior
- output artifact existence for candidate outputs
- production model paths remaining unchanged
- smoke-test metrics generation and success/failure handling

## 3. Current project state

### Status: stable but incomplete for full production training

The repository is in a safer and more validated state than before.

Successfully working:
- MLflow server and experiment tracking
- monitoring metrics logging
- drift monitoring
- DVC initialization and dataset tracking
- dataset schema inspection
- data-loader validation
- production model and tokenizer preservation
- FastAPI health/model info checks

Blocked or intentionally deferred:
- Full dataset-driven training pipeline
- Full training/evaluation stage using the real dataset
- Promotion or registration of candidate model artifacts to production
- Full retraining on the corrupted or partially untrusted `train.txt`

## 4. Critical issue discovered and preserved

The most important fact found in the project is that the training split is not usable in its current state.

Evidence:
- [dataset/big_dataset/train.txt](./dataset/big_dataset/train.txt) contains only NUL bytes.
- It has no delimiter structure, no record boundaries, and no valid labels/URLs.
- This is confirmed by the binary-safe inspector and by direct byte inspection.

As a result, the project does not yet have a trustworthy full training pipeline, and any full training stage would be unsafe until the actual dataset source is restored or replaced with a valid training file.

## 5. Production safety verification

The production inference path was intentionally checked and preserved.

Verified:
- [test_results/cnn_bilstm/best_model.h5](./test_results/cnn_bilstm/best_model.h5) remains unchanged.
- [ml/artifacts/tokenizer.json](./ml/artifacts/tokenizer.json) remains unchanged.
- FastAPI health endpoint still returns healthy status.
- The model still loads and predicts successfully.

## 6. What has not been completed yet

The following remain intentionally not done or still blocked:

- Full retraining on the complete real dataset
- A final full-scale reproducible training pipeline for production
- Candidate model promotion to production
- Full dataset restoration if the current `train.txt` is actually invalid or corrupted
- End-to-end production deployment validation beyond the model and API safety checks

## 7. Recommended next action

The next step is not a full model retraining. The next step should be:

1. Restore or replace the corrupted `train.txt` with a trusted, valid dataset source.
2. Re-run the dataset inspector to confirm parseability.
3. Validate the loader and safe smoke-test again.
4. Only then run the real candidate smoke-test with a small controlled subset.
5. Keep production model and tokenizer untouched until a verified training artifact is ready.

## 8. Conclusion

The project has moved from a partially copied and unverified MLOps setup into a safer and validated state:

- MLflow monitoring works.
- drift monitoring works.
- DVC is initialized and tracking data.
- dataset format validation is now real and evidence-based.
- safe smoke-test infrastructure exists.
- production artifacts remain untouched.

However, the project is not ready to claim a complete full-scale training pipeline because the real training dataset is currently invalid and must be restored or replaced before any real model training is allowed.
