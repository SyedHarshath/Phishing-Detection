from pathlib import Path
import sqlite3
from collections import Counter

PROJECT_ROOT = Path(__file__).resolve().parents[2]
DB_PATH = PROJECT_ROOT / "mlops" / "data" / "predictions.db"

def load_predictions():
    if not DB_PATH.exists():
        return []

    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    try:
        return conn.execute(
            """
            SELECT id, timestamp, url, prediction, confidence,
                   model_version, feedback
            FROM predictions
            ORDER BY id DESC
            """
        ).fetchall()
    finally:
        conn.close()

def print_dashboard():
    rows = load_predictions()

    print("\n========== OPTIMUS MONITORING ==========")
    print(f"Total predictions : {len(rows)}")

    if not rows:
        print("No predictions recorded yet.")
        return

    phishing = sum(r["prediction"] == "phishing" for r in rows)
    legitimate = sum(r["prediction"] == "legitimate" for r in rows)
    feedback = [r for r in rows if r["feedback"]]

    avg_conf = sum(r["confidence"] for r in rows) / len(rows)
    accuracy = (
        sum(r["prediction"] == r["feedback"] for r in feedback)
        / len(feedback)
        if feedback else None
    )

    print(f"Phishing predictions: {phishing}")
    print(f"Legitimate predictions: {legitimate}")
    print(f"Phishing rate      : {phishing / len(rows):.2%}")
    print(f"Average confidence: {avg_conf:.2f}%")
    print(f"Feedback records   : {len(feedback)}")
    print(
        f"Feedback accuracy : {accuracy:.2%}"
        if accuracy is not None
        else "Feedback accuracy : N/A"
    )
    print("\nRecent predictions:")

    for row in rows[:10]:
        print(
            f"#{row['id']} | {row['prediction']:10} | "
            f"{row['confidence']:6.2f}% | {row['url']}"
        )

if __name__ == "__main__":
    print_dashboard()
