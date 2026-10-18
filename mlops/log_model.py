from pathlib import Path
import re
import mlflow

from mlops.mlflow_config import configure_mlflow


PROJECT_ROOT = Path(__file__).resolve().parent.parent

MODEL_PATH = (
    PROJECT_ROOT
    / "test_results"
    / "cnn_bilstm"
    / "best_model.h5"
)

TOKENIZER_PATH = (
    PROJECT_ROOT
    / "ml"
    / "artifacts"
    / "tokenizer.json"
)

REPORT_PATH = (
    PROJECT_ROOT
    / "test_results"
    / "cnn_bilstm"
    / "classification_report.txt"
)

CONFUSION_MATRIX = (
    PROJECT_ROOT
    / "test_results"
    / "cnn_bilstm"
    / "confusion_matrix.png"
)

ACCURACY_PLOT = (
    PROJECT_ROOT
    / "test_results"
    / "cnn_bilstm"
    / "accuracy.png"
)


def parse_classification_report(report_path):
    """
    Parse the standard sklearn classification_report text file.
    """

    if not report_path.exists():
        return {}

    text = report_path.read_text(encoding="utf-8")

    metrics = {}

    for line in text.splitlines():
        stripped = line.strip()

        # Example:
        # accuracy                           0.9876     10000

        if stripped.startswith("accuracy"):
            parts = stripped.split()

            if len(parts) >= 2:
                try:
                    metrics["test_accuracy"] = float(parts[1])
                except ValueError:
                    pass

        # Example:
        # phishing       0.98      0.99      0.99      5000
        # legitimate     0.99      0.98      0.98      5000

        for label in ["phishing", "legitimate"]:
            if stripped.startswith(label):
                parts = stripped.split()

                if len(parts) >= 4:
                    try:
                        metrics[f"{label}_precision"] = float(parts[1])
                        metrics[f"{label}_recall"] = float(parts[2])
                        metrics[f"{label}_f1"] = float(parts[3])

                        if len(parts) >= 5:
                            metrics[f"{label}_support"] = int(parts[4])

                    except ValueError:
                        pass

    # Parse macro avg / weighted avg
    for avg_name in ["macro avg", "weighted avg"]:
        pattern = rf"^{re.escape(avg_name)}\s+(\d+\.\d+)\s+(\d+\.\d+)\s+(\d+\.\d+)"
        match = re.search(pattern, text, re.MULTILINE)

        if match:
            metrics[f"{avg_name.replace(' ', '_')}_precision"] = float(match.group(1))
            metrics[f"{avg_name.replace(' ', '_')}_recall"] = float(match.group(2))
            metrics[f"{avg_name.replace(' ', '_')}_f1"] = float(match.group(3))

    return metrics


def main():

    tracking_uri = configure_mlflow()

    print(f"Tracking URI: {tracking_uri}")

    if not MODEL_PATH.exists():
        raise FileNotFoundError(
            f"Model not found: {MODEL_PATH}"
        )

    metrics = parse_classification_report(REPORT_PATH)

    print("Metrics parsed from classification report:")
    for key, value in metrics.items():
        print(f"  {key}: {value}")

    with mlflow.start_run(
        run_name="OPTIMUS-cnn-bilstm-v1-metrics"
    ) as run:

        # --------------------------------------------------
        # PARAMETERS
        # --------------------------------------------------

        mlflow.log_params({

            "model_name": "phishing-detector",
            "model_version": "1.0.0",

            "architecture": "CNN-BiLSTM",

            "sequence_length": 200,
            "embedding_dimension": 128,

            "optimizer": "Adam",
            "learning_rate": 0.0003,
            "loss": "categorical_crossentropy",

            "tokenizer_type": "character-level",
            "tokenizer_vocabulary_size": 71,

            "categories": "phishing,legitimate",
        })

        # --------------------------------------------------
        # TAGS
        # --------------------------------------------------

        mlflow.set_tags({

            "project": "OPTIMUS",

            "stage": "production-baseline",

            "framework": "TensorFlow",

            "model_type": "URL-classification",

        })

        # --------------------------------------------------
        # METRICS
        # --------------------------------------------------

        if metrics:

            for name, value in metrics.items():

                # MLflow metrics must be numeric
                if isinstance(value, (int, float)):
                    mlflow.log_metric(name, float(value))

        # --------------------------------------------------
        # MODEL
        # --------------------------------------------------

        mlflow.log_artifact(
            str(MODEL_PATH),
            artifact_path="model"
        )

        # --------------------------------------------------
        # TOKENIZER
        # --------------------------------------------------

        if TOKENIZER_PATH.exists():

            mlflow.log_artifact(
                str(TOKENIZER_PATH),
                artifact_path="preprocessing"
            )

        # --------------------------------------------------
        # EVALUATION ARTIFACTS
        # --------------------------------------------------

        evaluation_files = [
            REPORT_PATH,
            CONFUSION_MATRIX,
            ACCURACY_PLOT,
        ]

        for path in evaluation_files:

            if path.exists():

                mlflow.log_artifact(
                    str(path),
                    artifact_path="evaluation"
                )

        print()
        print("MLflow run created successfully.")
        print(f"Run ID: {run.info.run_id}")
        print(f"Tracking URI: {mlflow.get_tracking_uri()}")


if __name__ == "__main__":
    main()