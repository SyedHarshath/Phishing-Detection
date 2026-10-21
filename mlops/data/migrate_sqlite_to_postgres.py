"""Copy an accessible SQLite OPTIMUS database into PostgreSQL, without changing its source."""
from __future__ import annotations

import argparse
import os
from pathlib import Path
import sqlite3
import sys

from mlops.data.connection import connect_database, is_postgres

TABLES = (
    "predictions", "feedback_events", "label_audit", "training_batches",
    "training_batch_records", "retraining_state", "retraining_jobs",
)
IDENTITY_TABLES = ("predictions", "feedback_events", "label_audit")


class MigrationError(RuntimeError):
    pass


def migrate(source_path: Path, destination_url: str) -> dict:
    source_path = source_path.resolve()
    if not source_path.is_file():
        raise MigrationError("Source SQLite file does not exist")
    if not is_postgres(destination_url):
        raise MigrationError("Destination must be configured with a PostgreSQL DATABASE_URL")

    source = sqlite3.connect(f"{source_path.as_uri()}?mode=ro", uri=True)
    source.row_factory = sqlite3.Row
    counts = {}
    try:
        source.execute("BEGIN")
        source_tables = {r[0] for r in source.execute(
            "SELECT name FROM sqlite_master WHERE type='table'")}
        if "predictions" not in source_tables:
            raise MigrationError("Source does not contain a predictions table")
        from backend.app.database import initialize_database
        initialize_database(destination_url)
        with connect_database(destination_url) as destination:
            destination.execute("BEGIN")
            for table in TABLES:
                if table not in source_tables:
                    continue
                info = source.execute(f"PRAGMA table_info({table})").fetchall()
                columns = [row["name"] for row in info]
                primary_keys = [row["name"] for row in info if row["pk"]]
                if not columns or not primary_keys:
                    raise MigrationError(f"Unsupported source schema for {table}")
                rows = source.execute(f'SELECT * FROM "{table}"').fetchall()
                inserted = 0
                for row in rows:
                    values = tuple(row[column] for column in columns)
                    marks = ",".join("?" for _ in columns)
                    names = ",".join(f'"{name}"' for name in columns)
                    cursor = destination.execute(
                        f'INSERT INTO "{table}" ({names}) VALUES ({marks}) ON CONFLICT DO NOTHING',
                        values,
                    )
                    if cursor.rowcount:
                        inserted += 1
                    else:
                        where = " AND ".join(f'"{key}"=?' for key in primary_keys)
                        existing = destination.execute(
                            f'SELECT {names} FROM "{table}" WHERE {where}',
                            tuple(row[key] for key in primary_keys),
                        ).fetchone()
                        if existing is None or tuple(existing) != values:
                            raise MigrationError(
                                f"Destination contains conflicting data for {table} primary key"
                            )
                counts[table] = {"source": len(rows), "inserted": inserted}
            for table in IDENTITY_TABLES:
                if table not in source_tables:
                    continue
                maximum = source.execute(f'SELECT MAX(id) FROM "{table}"').fetchone()[0]
                if maximum is not None:
                    destination.execute(
                        "SELECT setval(pg_get_serial_sequence(?, 'id'), ?, true)",
                        (table, maximum),
                    )
        return {"source": str(source_path), "tables": counts, "source_modified": False}
    finally:
        source.close()


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source", type=Path, required=True, help="Existing SQLite database file (read only)")
    parser.add_argument("--destination-env", default="DATABASE_URL",
                        help="Environment variable containing the destination PostgreSQL URL (default: DATABASE_URL)")
    args = parser.parse_args()
    destination = os.getenv(args.destination_env, "")
    if not destination:
        parser.error(f"environment variable {args.destination_env} is not set")
    try:
        result = migrate(args.source, destination)
    except Exception as exc:
        # Driver exceptions can contain host details; never echo connection values or records.
        print(f"Migration failed: {exc if isinstance(exc, MigrationError) else 'database operation failed; verify connectivity and schema'}",
              file=sys.stderr)
        return 1
    print(f"Migration complete. Source={result['source']} source_modified=False")
    for table, values in result["tables"].items():
        print(f"{table}: source_rows={values['source']} inserted={values['inserted']}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
