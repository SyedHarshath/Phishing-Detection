import csv
import json
import math
import sqlite3
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path

import mlflow

from mlops.mlflow_config import configure_mlflow

PROJECT_ROOT = Path(__file__).resolve().parents[2]
DB_PATH = PROJECT_ROOT / "mlops" / "data" / "predictions.db"
BASELINE_PATH = PROJECT_ROOT / "mlops" / "monitoring" / "reference_data.csv"
REPORT_PATH = PROJECT_ROOT / "mlops" / "monitoring" / "drift_summary.json"


def load_production_rows():
    if not DB_PATH.exists():
        return []

    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    try:
        return conn.execute(
            "SELECT url, prediction, confidence FROM predictions"
        ).fetchall()
    finally:
        conn.close()


def load_baseline_rows():
    if not BASELINE_PATH.exists():
        return []

    with BASELINE_PATH.open("r", encoding="utf-8", newline="") as handle:
        return list(csv.DictReader(handle))


def _mean(values):
    return sum(values) / len(values) if values else 0.0


def _psi(expected_dist, actual_dist, epsilon=1e-6):
    categories = sorted(set(expected_dist) | set(actual_dist))
    expected = {category: expected_dist.get(category, 0.0) for category in categories}
    actual = {category: actual_dist.get(category, 0.0) for category in categories}

    score = 0.0
    for category in categories:
        e = max(expected[category], epsilon)
        a = max(actual[category], epsilon)
        score += (a - e) * math.log(a / e)
    return score


def _safe_ratio(numerator, denominator):
    return numerator / denominator if denominator else 0.0


def build_summary():
    production_rows = load_production_rows()
    baseline_rows = load_baseline_rows()

    if not production_rows:
        raise ValueError("No production prediction data found. Run the monitoring job first.")
    if not baseline_rows:
        raise ValueError(f"Baseline dataset not found: {BASELINE_PATH}")

    production_lengths = [len(row["url"]) for row in production_rows]
    baseline_lengths = [len(row["url"]) for row in baseline_rows]

    production_label_counts = Counter(row["prediction"] for row in production_rows)
    baseline_label_counts = Counter(row["label"] for row in baseline_rows)

    total_production = len(production_rows)
    total_baseline = len(baseline_rows)

    production_dist = {
        key: _safe_ratio(count, total_production)
        for key, count in production_label_counts.items()
    }
    baseline_dist = {
        key: _safe_ratio(count, total_baseline)
        for key, count in baseline_label_counts.items()
    }

    prod_avg_length = _mean(production_lengths)
    base_avg_length = _mean(baseline_lengths)
    prod_phishing_rate = _safe_ratio(production_label_counts.get("phishing", 0), total_production)
    base_phishing_rate = _safe_ratio(baseline_label_counts.get("phishing", 0), total_baseline)
    avg_confidence = _mean([row["confidence"] for row in production_rows])
    distribution_psi = _psi(baseline_dist, production_dist)
    length_shift = abs(prod_avg_length - base_avg_length) / max(abs(base_avg_length), 1.0)
    phishing_shift = abs(prod_phishing_rate - base_phishing_rate)

    drift_thresholds = {
        "distribution_psi": 0.25,
        "url_length_shift": 0.15,
        "phishing_rate_shift": 0.10,
    }
    drift_flag = int(
        distribution_psi > drift_thresholds["distribution_psi"]
        or length_shift > drift_thresholds["url_length_shift"]
        or phishing_shift > drift_thresholds["phishing_rate_shift"]
    )

    summary = {
        "baseline_source": str(BASELINE_PATH.relative_to(PROJECT_ROOT)),
        "production_sample_count": total_production,
        "baseline_sample_count": total_baseline,
        "baseline_avg_url_length": round(base_avg_length, 3),
        "production_avg_url_length": round(prod_avg_length, 3),
        "url_length_shift_ratio": round(length_shift, 6),
        "baseline_phishing_rate": round(base_phishing_rate, 6),
        "production_phishing_rate": round(prod_phishing_rate, 6),
        "phishing_rate_shift": round(phishing_shift, 6),
        "baseline_label_distribution": {
            key: round(value, 6) for key, value in baseline_dist.items()
        },
        "production_label_distribution": {
            key: round(value, 6) for key, value in production_dist.items()
        },
        "label_distribution_psi": round(distribution_psi, 6),
        "average_confidence": round(avg_confidence, 3),
        "drift_detected": bool(drift_flag),
        "drift_thresholds": drift_thresholds,
        "generated_at_utc": datetime.now(timezone.utc).isoformat(),
    }

    REPORT_PATH.write_text(json.dumps(summary, indent=2), encoding="utf-8")
    return summary


def main():
    configure_mlflow()
    summary = build_summary()

    with mlflow.start_run(
        run_name=f"drift-{datetime.now(timezone.utc).strftime('%Y%m%d-%H%M%S')}"
    ) as run:
        mlflow.log_metrics({
            "baseline_avg_url_length": summary["baseline_avg_url_length"],
            "production_avg_url_length": summary["production_avg_url_length"],
            "url_length_shift_ratio": summary["url_length_shift_ratio"],
            "baseline_phishing_rate": summary["baseline_phishing_rate"],
            "production_phishing_rate": summary["production_phishing_rate"],
            "phishing_rate_shift": summary["phishing_rate_shift"],
            "label_distribution_psi": summary["label_distribution_psi"],
            "average_confidence": summary["average_confidence"],
            "production_sample_count": summary["production_sample_count"],
            "drift_detected": int(summary["drift_detected"]),
        })
        mlflow.log_param("baseline_dataset", str(BASELINE_PATH))
        mlflow.log_artifact(str(REPORT_PATH))
        mlflow.set_tags({
            "project": "OPTIMUS",
            "run_type": "drift-monitoring",
            "baseline_source": summary["baseline_source"],
            "drift_status": "alert" if summary["drift_detected"] else "stable",
            "method": "baseline-vs-production-distribution-check",
        })

    print("Baseline-vs-production drift comparison logged.")
    print(f"Average URL length [baseline vs production]: {summary['baseline_avg_url_length']:.2f} vs {summary['production_avg_url_length']:.2f}")
    print(f"Phishing rate [baseline vs production]: {summary['baseline_phishing_rate']:.2%} vs {summary['production_phishing_rate']:.2%}")
    print(f"Distribution PSI: {summary['label_distribution_psi']:.4f}")
    print(f"Drift detected: {summary['drift_detected']}")
    print(f"Summary report: {REPORT_PATH}")


if __name__ == "__main__":
    main()
