# Planned DVC pipeline

Do not wire `dvc.yaml` to the current Kaggle-specific `src/dl.py` yet.

## Target pipeline

```yaml
stages:
  validate:
    cmd: python -m mlops.dvc.validate_dataset
    deps:
      - dataset/big_dataset
      - mlops/dvc/validate_dataset.py
    outs:
      - mlops/dvc/dataset_report.json

  train:
    cmd: python -m src.train
    deps:
      - dataset/big_dataset
      - src/train.py
      - mlops/dvc/params.yaml
    params:
      - mlops/dvc/params.yaml
    outs:
      - artifacts/model
      - artifacts/tokenizer.json

  evaluate:
    cmd: python -m src.evaluate
    deps:
      - artifacts/model
      - dataset/big_dataset
      - src/evaluate.py
    metrics:
      - metrics.json
```

The exact stage names/paths should be created after the training code is refactored from Kaggle paths into a local CLI/config-driven implementation.
