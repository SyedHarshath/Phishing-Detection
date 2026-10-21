"""Small SQLite/PostgreSQL connection adapter for the API and lifecycle."""
from __future__ import annotations

from contextlib import contextmanager
from pathlib import Path
import sqlite3
from typing import Any


def is_postgres(target: str | Path) -> bool:
    return isinstance(target, str) and target.startswith(("postgres://", "postgresql://"))


def normalize_postgres_url(url: str) -> str:
    return "postgresql://" + url[len("postgres://"):] if url.startswith("postgres://") else url


def default_database_target() -> str | Path:
    import os
    raw_url = os.getenv("DATABASE_URL")
    url = raw_url.strip() if raw_url else ""
    if url:
        if not url.startswith(("postgres://", "postgresql://")):
            raise ValueError("DATABASE_URL must use the postgres:// or postgresql:// scheme")
        return url
    return Path(os.getenv(
        "PHISHGUARD_DATABASE_PATH",
        str(Path(__file__).resolve().parents[2] / "mlops" / "data" / "predictions.db"),
    )).expanduser()


class CompatRow:
    """Row supporting sqlite.Row-style name and integer indexing."""
    def __init__(self, names: list[str], values: tuple[Any, ...]):
        self._names = names
        self._values = values
        self._mapping = dict(zip(names, values))

    def __getitem__(self, key: int | str) -> Any:
        return self._values[key] if isinstance(key, int) else self._mapping[key]

    def __iter__(self):
        return iter(self._values)

    def __len__(self) -> int:
        return len(self._values)

    def keys(self):
        return self._names


class ConnectionAdapter:
    def __init__(self, raw, postgres: bool):
        self.raw = raw
        self.postgres = postgres

    @staticmethod
    def _convert(sql: str) -> str:
        # Lifecycle SQL uses qmark parameters; none of its statements contain
        # question marks inside string literals.
        return sql.replace("?", "%s")

    def execute(self, sql: str, params=()):
        if self.postgres:
            if sql.strip().upper() == "BEGIN IMMEDIATE":
                sql = "BEGIN"
            return self.raw.execute(self._convert(sql), params)
        return self.raw.execute(sql, params)

    def executemany(self, sql: str, params):
        if self.postgres:
            return self.raw.cursor().executemany(self._convert(sql), params)
        return self.raw.executemany(sql, params)

    def commit(self):
        self.raw.commit()

    def rollback(self):
        self.raw.rollback()

    def close(self):
        self.raw.close()


@contextmanager
def connect_database(target: str | Path, *, readonly: bool = False):
    postgres = is_postgres(target)
    if isinstance(target, str) and "://" in target and not postgres:
        raise ValueError("Database URL must use the postgres:// or postgresql:// scheme")
    if postgres:
        try:
            import psycopg
        except ImportError as exc:
            raise RuntimeError("PostgreSQL is configured but the psycopg driver is not installed") from exc

        def row_factory(cursor):
            names = [column.name for column in cursor.description]
            return lambda values: CompatRow(names, values)

        raw = psycopg.connect(
            normalize_postgres_url(str(target)),
            row_factory=row_factory,
            connect_timeout=10,
        )
        conn = ConnectionAdapter(raw, True)
        try:
            if readonly:
                conn.execute("SET TRANSACTION READ ONLY")
            yield conn
            conn.commit()
        except Exception:
            conn.rollback()
            raise
        finally:
            conn.close()
        return

    path = Path(target).expanduser()
    if readonly:
        uri = f"{path.resolve().as_uri()}?mode=ro"
        raw = sqlite3.connect(uri, uri=True, timeout=30, isolation_level="DEFERRED")
    else:
        path.parent.mkdir(parents=True, exist_ok=True)
        raw = sqlite3.connect(str(path), timeout=30, isolation_level="DEFERRED")
    raw.row_factory = sqlite3.Row
    raw.execute("PRAGMA busy_timeout=30000")
    raw.execute("PRAGMA foreign_keys=ON")
    conn = ConnectionAdapter(raw, False)
    try:
        yield conn
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


def table_columns(conn: ConnectionAdapter, table: str) -> set[str]:
    if conn.postgres:
        rows = conn.execute(
            "SELECT column_name FROM information_schema.columns "
            "WHERE table_schema=current_schema() AND table_name=?", (table,)
        ).fetchall()
        return {row[0] for row in rows}
    return {row[1] for row in conn.execute(f"PRAGMA table_info({table})")}


def table_names(conn: ConnectionAdapter) -> set[str]:
    if conn.postgres:
        rows = conn.execute(
            "SELECT table_name FROM information_schema.tables "
            "WHERE table_schema=current_schema()"
        ).fetchall()
        return {row[0] for row in rows}
    return {row[0] for row in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")}
