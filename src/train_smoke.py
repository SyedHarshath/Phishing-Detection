"""Train and evaluate a bounded candidate without touching production artifacts."""

from __future__ import annotations

import argparse
import hashlib
import importlib.util
import json
import random
import re
import sys
import time
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path

import numpy as np

from mlops.dvc.inspect_dataset import PARSER_VERSION
from src.data_loader import DatasetFormatError, DatasetSplit, load_split

PROJECT_ROOT = Path(__file__).resolve().parents[1]
CANDIDATE_DIR = PROJECT_ROOT / "artifacts" / "candidate_cnn_bilstm"
PRODUCTION_MODEL = PROJECT_ROOT / "test_results" / "cnn_bilstm" / "best_model.h5"
PRODUCTION_TOKENIZER = PROJECT_ROOT / "ml" / "artifacts" / "tokenizer.json"
SEQUENCE_LENGTH = 200
CATEGORIES = ("phishing", "legitimate")
DEFAULTS = {
    "train_samples": 4000,
    "val_samples": 500,
    "test_samples": 500,
    "epochs": 1,
    "batch_size": 64,
    "learning_rate": 0.0003,
    "seed": 42,
}


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def production_artifact_hashes() -> dict[str, str]:
    missing = [str(path) for path in (PRODUCTION_MODEL, PRODUCTION_TOKENIZER) if not path.is_file()]
    if missing:
        raise FileNotFoundError(
            "Production artifacts required for safety verification are missing: "
            + ", ".join(missing)
        )
    return {
        "production_model_sha256": sha256_file(PRODUCTION_MODEL),
        "production_tokenizer_sha256": sha256_file(PRODUCTION_TOKENIZER),
    }


def _dataset_version() -> str | None:
    pointer = PROJECT_ROOT / "dataset" / "big_dataset.dvc"
    if not pointer.is_file():
        return None
    match = re.search(r"(?m)^\s*-\s*md5:\s*([0-9a-f]+)", pointer.read_text(encoding="utf-8"))
    return match.group(1) if match else None


def _load_mlflow():
    if "mlflow" not in sys.modules and importlib.util.find_spec("mlflow") is None:
        venv_site_packages = PROJECT_ROOT / "mlflow_venv" / "Lib" / "site-packages"
        if not venv_site_packages.is_dir():
            raise ModuleNotFoundError(
                "MLflow is unavailable in this interpreter and the project "
                f"environment was not found at {venv_site_packages}."
            )
        sys.path.insert(0, str(venv_site_packages))

    import mlflow
    from mlops.mlflow_config import configure_mlflow

    return mlflow, configure_mlflow


def _class_distribution(split: DatasetSplit) -> dict[str, int]:
    counts = Counter(split.labels)
    return {
        "phishing": counts.get(0, 0),
        "legitimate": counts.get(1, 0),
    }


def fit_training_tokenizer(train_urls: list[str]):
    """Fit the project's character tokenizer on training URLs only."""
    from tensorflow.keras.preprocessing.text import Tokenizer

    tokenizer = Tokenizer(lower=True, char_level=True, oov_token="-n-")
    tokenizer.fit_on_texts(train_urls)
    return tokenizer


def _encode(urls: list[str], tokenizer):
    from tensorflow.keras.preprocessing.sequence import pad_sequences

    sequences = tokenizer.texts_to_sequences(urls)
    return pad_sequences(sequences, maxlen=SEQUENCE_LENGTH)


def _is_authoritatively_registered(metadata: dict | None) -> bool:
    metadata = metadata or {}
    digest = metadata.get("source_registration_sha256")
    return (metadata.get("source_provenance_verified") is True
        and metadata.get("authoritative_registration") is True
        and isinstance(digest, str) and len(digest) == 64
        and all(char in "0123456789abcdef" for char in digest.lower()))


def train_candidate_from_splits(train_split: DatasetSplit, val_split: DatasetSplit,
                                output_dir: Path, *, seed: int = 42,
                                epochs: int = 1, batch_size: int = 64,
                                learning_rate: float = 0.0003,
                                metadata: dict | None = None) -> dict:
    """Train and package an isolated candidate using train/validation only.

    This entry point deliberately has no test split and never uses the smoke
    trainer's shared output directory or MLflow auto-start helper.
    """
    if not train_split.urls or not val_split.urls:
        raise ValueError("non-empty train and validation splits are required")
    if epochs < 1 or batch_size < 1 or learning_rate <= 0:
        raise ValueError("invalid training configuration")
    metadata = metadata or {}
    real_candidate = _is_authoritatively_registered(metadata) and metadata.get("test_only") is not True
    import tensorflow as tf
    from tensorflow.keras.callbacks import ModelCheckpoint
    from tensorflow.keras.models import load_model
    from tensorflow.keras.optimizers import Adam
    from src.dl_models import DlModels

    random.seed(seed); np.random.seed(seed); tf.random.set_seed(seed)
    output_dir = Path(output_dir)
    output_dir.mkdir(parents=True, exist_ok=False)
    tokenizer = fit_training_tokenizer(train_split.urls)
    train_x, val_x = _encode(train_split.urls, tokenizer), _encode(val_split.urls, tokenizer)
    model = DlModels(categories=list(CATEGORIES), embed_dim=128,
                     sequence_length=SEQUENCE_LENGTH).cnn_complex(tokenizer.word_index)
    model.compile(optimizer=Adam(learning_rate=learning_rate),
                  loss="sparse_categorical_crossentropy", metrics=["accuracy"])
    model_path, tokenizer_path = output_dir / "best_model.h5", output_dir / "tokenizer.json"
    started = time.perf_counter()
    model.fit(train_x, np.asarray(train_split.labels, dtype=np.int32),
              validation_data=(val_x, np.asarray(val_split.labels, dtype=np.int32)),
              epochs=epochs, batch_size=batch_size,
              callbacks=[ModelCheckpoint(str(model_path), monitor="val_loss", save_best_only=True)],
              verbose=0, shuffle=True)
    duration = time.perf_counter() - started
    tokenizer_path.write_text(tokenizer.to_json(), encoding="utf-8")
    # Ensure checkpoint is a readable complete model before publishing bundle.
    load_model(model_path, compile=False)
    bundle = {"bundle_schema_version": 1, "candidate_id": output_dir.name,
              "model_version": output_dir.name, "bundle_type": "real_candidate" if real_candidate else "synthetic_test_candidate",
              "role": "candidate-not-production",
              "test_only": not real_candidate,
              "class_order": list(CATEGORIES), "label_mapping": {"phishing": 0, "legitimate": 1},
              "sequence_length": SEQUENCE_LENGTH, "preprocessing_version": "url-character-lowercase-pad200-v1",
              "architecture": "src.dl_models.DlModels.cnn_complex",
              "training_dataset_fingerprint": metadata.get("training_dataset_fingerprint"),
              "training_run_id": metadata.get("training_run_id"),
              "created_at_utc": datetime.now(timezone.utc).isoformat(),
              "compatibility": {"python": sys.version.split()[0], "sequence_length": SEQUENCE_LENGTH,
                                "tokenizer_format": "keras-json", "model_format": "keras-h5"},
              "training": {"seed": seed, "epochs": epochs, "batch_size": batch_size,
                           "learning_rate": learning_rate, "train_count": len(train_split.urls),
                           "validation_count": len(val_split.urls), "test_split_used": False,
                           "duration_seconds": duration},
              "metadata": {**metadata, "candidate_origin": "approved_dataset_import" if real_candidate else "synthetic_or_unverified_fixture"},
              "files": {"best_model.h5": sha256_file(model_path),
                        "tokenizer.json": sha256_file(tokenizer_path)}}
    _save_json(output_dir / "bundle.json", bundle)
    return bundle


def _save_json(path: Path, value) -> None:
    path.write_text(json.dumps(value, indent=2), encoding="utf-8")


def _failure_report(
    reason: str,
    requested_samples: dict[str, int],
    production_hashes: dict[str, str],
    split_details: dict | None = None,
) -> dict:
    return {
        "success": False,
        "status": "blocked",
        "failure_reason": reason,
        "dataset_parser": PARSER_VERSION,
        "dataset_schema": {
            "record_format": "UTF-8 line records: label<TAB>url",
            "labels": {"phishing": 0, "legitimate": 1},
        },
        "dataset_version": _dataset_version(),
        "requested_sample_sizes": requested_samples,
        "sample_sizes": {},
        "split_details": split_details or {},
        "production_artifact_hashes_before": production_hashes,
        "production_artifact_hashes_after": production_artifact_hashes(),
        "production_artifacts_unchanged": (
            production_hashes == production_artifact_hashes()
        ),
        "created_at_utc": datetime.now(timezone.utc).isoformat(),
    }


def _report_split(split: DatasetSplit) -> dict:
    return {
        "parsed_records": split.parsed_records,
        "malformed_records": split.malformed_records,
        "sample_size": split.sample_size,
        "sample_class_distribution": _class_distribution(split),
        "full_split_class_distribution": split.label_counts,
    }


def _evaluation(model, split: DatasetSplit, tokenizer) -> tuple[dict, str]:
    from sklearn.metrics import accuracy_score, classification_report, precision_recall_fscore_support

    x_values = _encode(split.urls, tokenizer)
    y_true = np.asarray(split.labels, dtype=np.int32)
    probabilities = model.predict(x_values, verbose=0)
    y_pred = np.argmax(probabilities, axis=1)
    precision, recall, f1, _ = precision_recall_fscore_support(
        y_true,
        y_pred,
        labels=[0],
        average="binary",
        pos_label=0,
        zero_division=0,
    )
    report_text = classification_report(
        y_true,
        y_pred,
        labels=[0, 1],
        target_names=list(CATEGORIES),
        zero_division=0,
    )
    metrics = {
        "accuracy": float(accuracy_score(y_true, y_pred)),
        "precision": float(precision),
        "recall": float(recall),
        "f1": float(f1),
        "sample_count": int(len(y_true)),
    }
    return metrics, report_text


def _run_smoke_training(
    train_samples: int = DEFAULTS["train_samples"],
    val_samples: int = DEFAULTS["val_samples"],
    test_samples: int = DEFAULTS["test_samples"],
    epochs: int = DEFAULTS["epochs"],
    batch_size: int = DEFAULTS["batch_size"],
    learning_rate: float = DEFAULTS["learning_rate"],
    seed: int = DEFAULTS["seed"],
    before_hashes: dict[str, str] | None = None,
) -> dict:
    if any(size < 1 for size in (train_samples, val_samples, test_samples)):
        raise ValueError("Each split sample size must be at least 1.")
    if not 1 <= epochs <= 3:
        raise ValueError("Smoke training is limited to 1-3 epochs.")
    if batch_size < 1 or learning_rate <= 0:
        raise ValueError("batch_size and learning_rate must be positive.")

    requested_samples = {
        "train": train_samples,
        "val": val_samples,
        "test": test_samples,
        "total": train_samples + val_samples + test_samples,
    }
    before_hashes = before_hashes or production_artifact_hashes()
    split_data: dict[str, DatasetSplit] = {}

    try:
        for split, size in (
            ("train", train_samples),
            ("val", val_samples),
            ("test", test_samples),
        ):
            split_data[split] = load_split(
                split,
                sample_size=size,
                seed=seed,
            )
    except (DatasetFormatError, FileNotFoundError) as exc:
        CANDIDATE_DIR.mkdir(parents=True, exist_ok=True)
        report = _failure_report(
            str(exc),
            requested_samples,
            before_hashes,
            {name: _report_split(data) for name, data in split_data.items()},
        )
        _save_json(CANDIDATE_DIR / "smoke_test_report.json", report)
        raise

    CANDIDATE_DIR.mkdir(parents=True, exist_ok=True)

    import tensorflow as tf
    from tensorflow.keras.callbacks import ModelCheckpoint
    from tensorflow.keras.models import load_model
    from tensorflow.keras.optimizers import Adam
    from src.dl_models import DlModels

    random.seed(seed)
    np.random.seed(seed)
    tf.random.set_seed(seed)

    tokenizer = fit_training_tokenizer(split_data["train"].urls)
    training_vocabulary = dict(tokenizer.word_index)
    x_train = _encode(split_data["train"].urls, tokenizer)
    x_val = _encode(split_data["val"].urls, tokenizer)
    x_test = _encode(split_data["test"].urls, tokenizer)
    if tokenizer.word_index != training_vocabulary:
        raise RuntimeError("Tokenizer vocabulary changed after fitting on train only.")

    tokenizer_path = CANDIDATE_DIR / "tokenizer.json"
    tokenizer_path.write_text(tokenizer.to_json(), encoding="utf-8")

    model = DlModels(
        categories=list(CATEGORIES),
        embed_dim=128,
        sequence_length=SEQUENCE_LENGTH,
    ).cnn_complex(tokenizer.word_index)
    model.compile(
        optimizer=Adam(learning_rate=learning_rate),
        loss="sparse_categorical_crossentropy",
        metrics=["accuracy"],
    )
    model_path = CANDIDATE_DIR / "best_model.h5"
    checkpoint = ModelCheckpoint(
        filepath=str(model_path),
        monitor="val_loss",
        save_best_only=True,
        verbose=0,
    )

    started_at = time.perf_counter()
    history = model.fit(
        x_train,
        np.asarray(split_data["train"].labels, dtype=np.int32),
        validation_data=(
            x_val,
            np.asarray(split_data["val"].labels, dtype=np.int32),
        ),
        epochs=epochs,
        batch_size=batch_size,
        callbacks=[checkpoint],
        verbose=2,
        shuffle=True,
    )
    training_seconds = time.perf_counter() - started_at

    candidate_model = load_model(model_path, compile=False)
    validation_metrics, _ = _evaluation(
        candidate_model, split_data["val"], tokenizer
    )
    test_metrics, report_text = _evaluation(
        candidate_model, split_data["test"], tokenizer
    )

    history_data = {
        key: [float(value) for value in values]
        for key, values in history.history.items()
    }
    _save_json(CANDIDATE_DIR / "history.json", history_data)
    _save_json(
        CANDIDATE_DIR / "metrics.json",
        {"validation": validation_metrics, "test": test_metrics},
    )
    (CANDIDATE_DIR / "classification_report.txt").write_text(
        report_text, encoding="utf-8"
    )

    config = {
        **DEFAULTS,
        "train_samples": train_samples,
        "val_samples": val_samples,
        "test_samples": test_samples,
        "epochs": epochs,
        "batch_size": batch_size,
        "learning_rate": learning_rate,
        "seed": seed,
        "sequence_length": SEQUENCE_LENGTH,
        "tokenizer": {
            "char_level": True,
            "lowercase": True,
            "oov_token": "-n-",
            "fitted_on": "train sample only",
        },
        "architecture": "existing src.dl_models.DlModels.cnn_complex",
    }
    report = {
        "success": True,
        "status": "success",
        "dataset_parser": PARSER_VERSION,
        "dataset_schema": {
            "record_format": "UTF-8 line records: label<TAB>url",
            "labels": {"phishing": 0, "legitimate": 1},
        },
        "dataset_version": _dataset_version(),
        "sample_sizes": {
            split: data.sample_size for split, data in split_data.items()
        },
        "split_details": {
            split: _report_split(data) for split, data in split_data.items()
        },
        "tokenizer_vocabulary_size": len(tokenizer.word_index),
        "sequence_length": SEQUENCE_LENGTH,
        "training_configuration": config,
        "training_seconds": round(training_seconds, 3),
        "validation_metrics": validation_metrics,
        "test_metrics": test_metrics,
        "candidate_directory": str(CANDIDATE_DIR),
        "production_artifact_hashes_before": before_hashes,
        "created_at_utc": datetime.now(timezone.utc).isoformat(),
    }

    mlflow, configure_mlflow = _load_mlflow()
    tracking_uri = configure_mlflow()
    with mlflow.start_run(run_name="OPTIMUS-smoke-test-candidate") as run:
        mlflow.set_tags(
            {
                "project": "OPTIMUS",
                "stage": "smoke-test-candidate",
                "model_role": "candidate-not-production",
                "dataset_parser": PARSER_VERSION,
                "dataset_version": str(_dataset_version() or "unversioned"),
            }
        )
        mlflow.log_params(
            {
                "sample_size": sum(data.sample_size for data in split_data.values()),
                "train_sample_size": train_samples,
                "validation_sample_size": val_samples,
                "test_sample_size": test_samples,
                "tokenizer_vocabulary_size": len(tokenizer.word_index),
                "sequence_length": SEQUENCE_LENGTH,
                "epochs": epochs,
                "batch_size": batch_size,
                "learning_rate": learning_rate,
                "seed": seed,
                "parser_version": PARSER_VERSION,
                "dataset_version": str(_dataset_version() or "unversioned"),
            }
        )
        mlflow.log_metrics(
            {
                "test_accuracy": test_metrics["accuracy"],
                "test_precision": test_metrics["precision"],
                "test_recall": test_metrics["recall"],
                "test_f1": test_metrics["f1"],
                "validation_accuracy": validation_metrics["accuracy"],
                "validation_f1": validation_metrics["f1"],
            }
        )
        mlflow.log_artifacts(str(CANDIDATE_DIR), artifact_path="candidate_model")
        report["mlflow_run_id"] = run.info.run_id
        report["mlflow_tracking_uri"] = tracking_uri
        report["success"] = True
        report["production_artifact_hashes_after"] = production_artifact_hashes()
        report["production_artifacts_unchanged"] = (
            before_hashes == report["production_artifact_hashes_after"]
        )
        if not report["production_artifacts_unchanged"]:
            raise RuntimeError("A production artifact changed during smoke training.")
        _save_json(CANDIDATE_DIR / "smoke_test_report.json", report)
        mlflow.log_artifact(
            str(CANDIDATE_DIR / "smoke_test_report.json"),
            artifact_path="candidate_model",
        )

    print(f"Smoke-test candidate saved to: {CANDIDATE_DIR}")
    print(f"MLflow run ID: {report['mlflow_run_id']}")
    return report


def run_smoke_training(
    train_samples: int = DEFAULTS["train_samples"],
    val_samples: int = DEFAULTS["val_samples"],
    test_samples: int = DEFAULTS["test_samples"],
    epochs: int = DEFAULTS["epochs"],
    batch_size: int = DEFAULTS["batch_size"],
    learning_rate: float = DEFAULTS["learning_rate"],
    seed: int = DEFAULTS["seed"],
) -> dict:
    before_hashes = production_artifact_hashes()
    try:
        return _run_smoke_training(
            train_samples=train_samples,
            val_samples=val_samples,
            test_samples=test_samples,
            epochs=epochs,
            batch_size=batch_size,
            learning_rate=learning_rate,
            seed=seed,
            before_hashes=before_hashes,
        )
    finally:
        after_hashes = production_artifact_hashes()
        if after_hashes != before_hashes:
            raise RuntimeError(
                "Production model or tokenizer changed during smoke training."
            )


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--train-samples", type=int, default=DEFAULTS["train_samples"])
    parser.add_argument("--val-samples", type=int, default=DEFAULTS["val_samples"])
    parser.add_argument("--test-samples", type=int, default=DEFAULTS["test_samples"])
    parser.add_argument("--epochs", type=int, default=DEFAULTS["epochs"])
    parser.add_argument("--batch-size", type=int, default=DEFAULTS["batch_size"])
    parser.add_argument("--learning-rate", type=float, default=DEFAULTS["learning_rate"])
    parser.add_argument("--seed", type=int, default=DEFAULTS["seed"])
    args = parser.parse_args()
    run_smoke_training(
        train_samples=args.train_samples,
        val_samples=args.val_samples,
        test_samples=args.test_samples,
        epochs=args.epochs,
        batch_size=args.batch_size,
        learning_rate=args.learning_rate,
        seed=args.seed,
    )


if __name__ == "__main__":
    main()
