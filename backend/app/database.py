import sqlite3
from datetime import datetime, timezone
from pathlib import Path
from backend.app.config import get_settings

DEFAULT_DB_PATH = Path(__file__).resolve().parents[2] / "mlops" / "data" / "predictions.db"
DB_PATH = get_settings().database_path
DB_DIR = DB_PATH.parent

def get_connection():
    DB_DIR.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    return conn

def initialize_database():
    with get_connection() as conn:
        conn.execute("""
            CREATE TABLE IF NOT EXISTS predictions (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                timestamp TEXT NOT NULL,
                url TEXT NOT NULL,
                prediction TEXT NOT NULL,
                confidence REAL NOT NULL,
                model_version TEXT NOT NULL,
                feedback TEXT,
                feedback_timestamp TEXT
            )
        """)
        conn.commit()
    # Additive lifecycle migration; old prediction rows and IDs are retained.
    from mlops.data.lifecycle import initialize_schema
    initialize_schema(DB_PATH)

def log_prediction(url, prediction, confidence, model_version):
    timestamp = datetime.now(timezone.utc).isoformat()
    with get_connection() as conn:
        cur = conn.execute(
            """INSERT INTO predictions
            (timestamp, url, prediction, confidence, model_version)
            VALUES (?, ?, ?, ?, ?)""",
            (timestamp, url, prediction, confidence, model_version)
        )
        conn.commit()
        return cur.lastrowid

def add_feedback(prediction_id, actual_label):
    # API feedback is only a user claim; it enters pending verification.
    from mlops.data.lifecycle import record_feedback
    return record_feedback(prediction_id, actual_label, DB_PATH)

def get_predictions(limit=100):
    limit = max(1, min(int(limit), 1000))
    with get_connection() as conn:
        rows = conn.execute(
            """SELECT id, timestamp, url, prediction, confidence,
            model_version, feedback, feedback_timestamp
            FROM predictions ORDER BY id DESC LIMIT ?""",
            (limit,)
        ).fetchall()
    return [dict(row) for row in rows]
