from pathlib import Path
from collections import Counter
from mlops.data.connection import connect_database, default_database_target, is_postgres

PROJECT_ROOT = Path(__file__).resolve().parents[2]
DB_PATH = default_database_target()

def load_predictions():
    if not is_postgres(DB_PATH) and not Path(DB_PATH).is_file():
        return []

    with connect_database(DB_PATH, readonly=True) as conn:
        return conn.execute(
            """
            SELECT id, timestamp, url, prediction, confidence,
                   model_version, feedback
            FROM predictions
            ORDER BY id DESC
            """
        ).fetchall()

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
            f"{row['confidence']:6.2f}% | URL hidden"
        )

if __name__ == "__main__":
    print_dashboard()
