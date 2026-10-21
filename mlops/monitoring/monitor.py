from __future__ import annotations

import json
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[2]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

import mlflow

from mlops.mlflow_config import configure_mlflow
from mlops.data.connection import connect_database, default_database_target, is_postgres

DB_PATH = default_database_target()
REPORT_PATH = PROJECT_ROOT / "mlops" / "monitoring" / "monitoring_run.json"


def collect_metrics() -> dict:
    if not is_postgres(DB_PATH) and not Path(DB_PATH).is_file():
        raise FileNotFoundError(f"Prediction database not found: {DB_PATH}")

    with connect_database(DB_PATH, readonly=True) as conn:
        total = conn.execute("SELECT COUNT(*) AS count FROM predictions").fetchone()["count"]
        phishing = conn.execute(
            "SELECT COUNT(*) AS count FROM predictions WHERE prediction = 'phishing'"
        ).fetchone()["count"]
        legitimate = conn.execute(
            "SELECT COUNT(*) AS count FROM predictions WHERE prediction = 'legitimate'"
        ).fetchone()["count"]
        average_confidence = conn.execute(
            "SELECT AVG(confidence) AS avg_confidence FROM predictions"
        ).fetchone()["avg_confidence"]
        feedback_count = conn.execute(
            "SELECT COUNT(*) AS count FROM predictions WHERE feedback IS NOT NULL"
        ).fetchone()["count"]
        feedback_correct = conn.execute(
            """
            SELECT COUNT(*) AS count
            FROM predictions
            WHERE feedback IS NOT NULL
              AND feedback = prediction
            """
        ).fetchone()["count"]
        average_url_length = conn.execute(
            "SELECT AVG(LENGTH(url)) AS avg_length FROM predictions"
        ).fetchone()["avg_length"]

    phishing_rate = phishing / total if total > 0 else 0.0
    feedback_accuracy = feedback_correct / feedback_count if feedback_count > 0 else 0.0

    return {
        "prediction_count": total,
        "phishing_count": phishing,
        "legitimate_count": legitimate,
        "phishing_rate": phishing_rate,
        "average_confidence": float(average_confidence or 0.0),
        "feedback_count": feedback_count,
        "feedback_accuracy": feedback_accuracy,
        "average_url_length": float(average_url_length or 0.0),
    }


def print_dashboard(metrics: dict | None = None):
    metrics = metrics or collect_metrics()
    print("\nProduction monitoring metrics:")
    print("--------------------------------")
    for key, value in metrics.items():
        print(f"{key}: {value}")


def main():
    tracking_uri = configure_mlflow()
    metrics = collect_metrics()
    print_dashboard(metrics)

    REPORT_PATH.parent.mkdir(parents=True, exist_ok=True)
    REPORT_PATH.write_text(json.dumps(metrics, indent=2), encoding="utf-8")

    with mlflow.start_run(run_name="OPTIMUS-production-monitoring") as run:
        mlflow.set_tags(
            {
                "project": "OPTIMUS",
                "stage": "production-monitoring",
                "monitor_type": "prediction-feedback",
            }
        )
        for name, value in metrics.items():
            mlflow.log_metric(name, float(value))
        mlflow.log_param("database_backend", "postgresql" if is_postgres(DB_PATH) else "sqlite")
        mlflow.log_artifact(str(REPORT_PATH))

    print("\nMonitoring run created.")
    print(f"Run ID: {run.info.run_id}")
    print(f"Tracking URI: {tracking_uri}")
    print(f"Report: {REPORT_PATH}")


if __name__ == "__main__":
    main()
