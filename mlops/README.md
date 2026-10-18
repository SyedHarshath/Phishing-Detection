# Phase 5 — MLflow + Monitoring

## Install

```powershell
pip install mlflow
```

## 1. Log the existing production model

From the project root:

```powershell
python -m mlops.log_model
```

This creates a local MLflow experiment named `PhishGuard` and logs:

- model parameters
- model version
- CNN-BiLSTM architecture metadata
- `best_model.h5`
- tokenizer
- classification report
- confusion matrix
- accuracy plot

## 2. Start the MLflow UI

```powershell
mlflow ui --backend-store-uri ./mlruns --port 5000
```

Open:

```text
http://127.0.0.1:5000
```

## 3. Log a monitoring snapshot

```powershell
python -m mlops.monitoring.monitor
```

Metrics include:

- prediction count
- phishing count
- legitimate count
- phishing rate
- average confidence
- feedback count
- feedback accuracy
- average URL length

## 4. View the local monitoring summary

```powershell
python -m mlops.monitoring.dashboard_data
```

## 5. Distribution monitoring

```powershell
python -m mlops.monitoring.drift
```

This records production distribution indicators in MLflow. It is intentionally not called a statistical drift detector yet; a training baseline should be added before declaring drift.

## Recommended cadence

For development, run the monitor manually after a batch of predictions.

Later, schedule:

```text
every hour -> collect monitoring snapshot
daily      -> evaluate feedback accuracy
weekly     -> compare production distribution to training baseline
```

## MLOps flow

```text
Production predictions
        |
        v
SQLite prediction log
        |
        +----> MLflow monitoring runs
        |
        +----> feedback accuracy
        |
        +----> distribution metrics
        |
        v
Drift / quality alert
        |
        v
Future retraining
```
