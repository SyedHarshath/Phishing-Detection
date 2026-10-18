"""Re-evaluate the saved smoke-test candidate on the bounded test sample."""

from __future__ import annotations

import json
from pathlib import Path

from src.data_loader import load_split
from src.train_smoke import (
    CANDIDATE_DIR,
    _evaluation,
    production_artifact_hashes,
)


def main() -> None:
    model_path = CANDIDATE_DIR / "best_model.h5"
    tokenizer_path = CANDIDATE_DIR / "tokenizer.json"
    metrics_path = CANDIDATE_DIR / "metrics.json"
    report_path = CANDIDATE_DIR / "smoke_test_report.json"
    for path in (model_path, tokenizer_path, metrics_path, report_path):
        if not path.is_file():
            raise FileNotFoundError(f"Smoke-test candidate artifact not found: {path}")

    from tensorflow.keras.models import load_model
    from tensorflow.keras.preprocessing.text import tokenizer_from_json

    report = json.loads(report_path.read_text(encoding="utf-8"))
    before_hashes = report["production_artifact_hashes_before"]
    current_hashes = production_artifact_hashes()
    if current_hashes != before_hashes:
        raise RuntimeError("Production model/tokenizer hashes changed since smoke training.")

    test_size = report["sample_sizes"]["test"]
    seed = report["training_configuration"]["seed"]
    test_data = load_split("test", sample_size=test_size, seed=seed)
    tokenizer = tokenizer_from_json(tokenizer_path.read_text(encoding="utf-8"))
    model = load_model(model_path, compile=False)
    metrics, _ = _evaluation(model, test_data, tokenizer)

    stored_metrics = json.loads(metrics_path.read_text(encoding="utf-8"))["test"]
    if metrics != stored_metrics:
        raise RuntimeError(
            "Re-evaluated candidate metrics differ from the saved smoke-test metrics."
        )
    print(f"Candidate evaluation passed: {metrics}")
    print(f"Production artifact hashes unchanged: {current_hashes == before_hashes}")


if __name__ == "__main__":
    main()
