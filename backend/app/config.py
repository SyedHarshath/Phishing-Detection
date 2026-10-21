"""Runtime settings shared by the API and its persistence layer."""
from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[2]


@dataclass(frozen=True)
class Settings:
    environment: str
    database_url: str | None
    database_path: Path
    predictions_token: str | None
    active_model_manifest: Path
    model_path: Path | None
    tokenizer_path: Path | None
    model_version: str | None
    log_level: str
    max_url_length: int


def get_settings() -> Settings:
    """Read and validate runtime configuration without loading model artifacts."""
    environment = os.getenv("PHISHGUARD_ENV", "development").strip().lower()
    if environment not in {"development", "test", "production"}:
        raise ValueError("PHISHGUARD_ENV must be development, test, or production")

    database_url = os.getenv("DATABASE_URL", "").strip() or None
    if database_url and not database_url.startswith(("postgres://", "postgresql://")):
        raise ValueError("DATABASE_URL must use the postgres:// or postgresql:// scheme")
    if environment == "production" and not database_url:
        raise ValueError("production requires DATABASE_URL; SQLite is supported for local development and tests")

    default_db = PROJECT_ROOT / "mlops" / "data" / "predictions.db"
    database_path = Path(os.getenv("PHISHGUARD_DATABASE_PATH", str(default_db))).expanduser()
    if environment == "production" and not database_url and not database_path.is_absolute():
        raise ValueError("PHISHGUARD_DATABASE_PATH must be absolute in production")

    model_path_value = os.getenv("PHISHGUARD_MODEL_PATH")
    tokenizer_path_value = os.getenv("PHISHGUARD_TOKENIZER_PATH")
    if bool(model_path_value) != bool(tokenizer_path_value):
        raise ValueError("PHISHGUARD_MODEL_PATH and PHISHGUARD_TOKENIZER_PATH must be set together")
    if environment == "production" and model_path_value and (
        not Path(model_path_value).expanduser().is_absolute()
        or not Path(tokenizer_path_value).expanduser().is_absolute()
    ):
        raise ValueError("production model and tokenizer paths must be absolute")

    log_level = os.getenv("LOG_LEVEL", "INFO").upper()
    if log_level not in {"CRITICAL", "ERROR", "WARNING", "INFO", "DEBUG"}:
        raise ValueError("LOG_LEVEL must be one of CRITICAL, ERROR, WARNING, INFO, or DEBUG")

    try:
        max_url_length = int(os.getenv("PHISHGUARD_MAX_URL_LENGTH", "4096"))
    except ValueError as exc:
        raise ValueError("PHISHGUARD_MAX_URL_LENGTH must be an integer") from exc
    if not 1 <= max_url_length <= 16384:
        raise ValueError("PHISHGUARD_MAX_URL_LENGTH must be between 1 and 16384")

    default_manifest = PROJECT_ROOT / "artifacts" / "model_registry" / "active.json"
    manifest_value = os.getenv("PHISHGUARD_ACTIVE_MODEL_MANIFEST")
    manifest = Path(manifest_value or str(default_manifest)).expanduser()
    if environment == "production" and manifest_value and not manifest.is_absolute():
        raise ValueError("PHISHGUARD_ACTIVE_MODEL_MANIFEST must be absolute in production")
    if environment == "production" and manifest_value and not manifest.is_file():
        raise ValueError("configured PHISHGUARD_ACTIVE_MODEL_MANIFEST does not exist")
    if environment == "production" and not manifest.is_file() and not model_path_value:
        raise ValueError(
            "production requires an existing PHISHGUARD_ACTIVE_MODEL_MANIFEST or an explicit model/tokenizer pair"
        )
    model_version = os.getenv("PHISHGUARD_MODEL_VERSION")
    if environment == "production" and not manifest.is_file() and not model_version:
        raise ValueError("PHISHGUARD_MODEL_VERSION is required with explicit production artifact paths")

    return Settings(
        environment=environment,
        database_url=database_url,
        database_path=database_path,
        predictions_token=os.getenv("PHISHGUARD_PREDICTIONS_TOKEN"),
        active_model_manifest=manifest,
        model_path=Path(model_path_value).expanduser() if model_path_value else None,
        tokenizer_path=Path(tokenizer_path_value).expanduser() if tokenizer_path_value else None,
        model_version=model_version,
        log_level=log_level,
        max_url_length=max_url_length,
    )
