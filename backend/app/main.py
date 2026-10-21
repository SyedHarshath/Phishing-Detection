from contextlib import asynccontextmanager
import hmac
import ipaddress
import os
import re
import secrets
from pathlib import Path
from urllib.parse import urlsplit

from fastapi import FastAPI, Header, HTTPException, Query

from backend.app.config import get_settings
from backend.app.database import (
    add_feedback,
    check_database_connection,
    get_database_target,
    get_predictions,
    initialize_database,
)
from mlops.data.connection import connect_database, table_names
from backend.app.schemas import (
    FeedbackRequest,
    FeedbackResponse,
    HealthResponse,
    ModelInfoResponse,
    PredictionLog,
    PredictionRequest,
    PredictionResponse,
)
from backend.app.services.prediction import ModelUnavailableError, prediction_service

@asynccontextmanager
async def lifespan(app: FastAPI):
    initialize_database()
    yield

app = FastAPI(
    title="OPTIMUS",
    description="Deep Learning based phishing URL detection API",
    version="1.1.0",
    lifespan=lifespan,
)

@app.get("/admin/db-status")
def database_status(
    x_optimus_diagnostic_token: str | None = Header(default=None),
):
    expected = os.getenv("OPTIMUS_DIAGNOSTIC_TOKEN", "")

    if not expected or not x_optimus_diagnostic_token:
        raise HTTPException(status_code=404, detail="Not found")

    if not hmac.compare_digest(
        x_optimus_diagnostic_token, expected
    ):
        raise HTTPException(status_code=404, detail="Not found")

    target = get_database_target()
    if isinstance(target, str) and target.startswith(("postgres://", "postgresql://")):
        try:
            with connect_database(target, readonly=True) as conn:
                tables = table_names(conn)
                if "predictions" not in tables:
                    return {"database_backend": "postgresql", "database_exists": True,
                            "predictions_table_exists": False, "prediction_count": None}
                count = conn.execute("SELECT COUNT(*) FROM predictions").fetchone()[0]
                return {"database_backend": "postgresql", "database_exists": True,
                        "predictions_table_exists": True, "prediction_count": count}
        except Exception:
            raise HTTPException(status_code=503, detail="Database check failed")

    db_path = Path(target)

    if not db_path.is_file():
        return {
            "database_exists": False,
            "prediction_count": None,
        }

    try:
        # as_uri() correctly escapes spaces and URI-reserved path characters.
        with connect_database(db_path, readonly=True) as conn:
            tables = table_names(conn)

            if "predictions" not in tables:
                return {
                    "database_exists": True,
                    "predictions_table_exists": False,
                    "prediction_count": None,
                }

            count = conn.execute(
                "SELECT COUNT(*) FROM predictions"
            ).fetchone()[0]

        return {
            "database_backend": "sqlite",
            "database_exists": True,
            "predictions_table_exists": True,
            "prediction_count": count,
        }

    except Exception:
        raise HTTPException(
            status_code=500,
            detail="Database check failed",
        )


def _valid_hostname(hostname: str) -> bool:
    if not hostname or any(char.isspace() for char in hostname):
        return False
    try:
        ipaddress.ip_address(hostname)
        return True
    except ValueError:
        pass
    if re.fullmatch(r"[0-9.]+", hostname):
        return False
    domain = hostname[:-1] if hostname.endswith(".") else hostname
    try:
        ascii_domain = domain.encode("idna").decode("ascii")
    except UnicodeError:
        return False
    if len(ascii_domain) > 253:
        return False
    labels = ascii_domain.split(".")
    return all(
        1 <= len(label) <= 63
        and re.fullmatch(r"[A-Za-z0-9](?:[A-Za-z0-9-]*[A-Za-z0-9])?", label)
        for label in labels
    )

def _is_valid_http_url(url: str) -> bool:
    try:
        parsed = urlsplit(url)
        hostname = parsed.hostname
        port = parsed.port  # raises ValueError for malformed or out-of-range ports
    except ValueError:
        return False
    return (
        parsed.scheme.lower() in {"http", "https"}
        and bool(parsed.netloc)
        and parsed.username is None
        and parsed.password is None
        and not parsed.netloc.rsplit("@", 1)[-1].endswith(":")
        and _valid_hostname(hostname or "")
        and (port is None or 1 <= port <= 65535)
    )

def _authorize_predictions(token: str | None) -> None:
    configured_token = get_settings().predictions_token
    if not configured_token:
        raise HTTPException(status_code=503, detail="Prediction history access is disabled.")
    if token is None or not secrets.compare_digest(
        token.encode("utf-8"), configured_token.encode("utf-8")
    ):
        raise HTTPException(status_code=401, detail="A valid history access token is required.")

@app.get("/health", response_model=HealthResponse)
def health_check():
    model_loaded = prediction_service.is_model_loaded()
    return {
        "status": "healthy" if model_loaded else "degraded",
        "model_loaded": model_loaded,
        # Keep local artifact paths and deserializer details out of a public
        # health response. Detailed failure state remains in the process.
        "model_error": None if model_loaded else "model_unavailable",
    }

@app.get("/live")
def liveness_check():
    """Process liveness only; does not require model artifacts."""
    return {"status": "alive"}

@app.get("/ready", response_model=HealthResponse)
def readiness_check():
    """Serving readiness; hosting health checks should use this route."""
    model_loaded = prediction_service.is_model_loaded()
    try:
        check_database_connection()
    except Exception as exc:
        raise HTTPException(status_code=503, detail="Database is unavailable.") from exc
    if not model_loaded:
        raise HTTPException(status_code=503, detail="Model is unavailable.")
    return {"status": "ready", "model_loaded": True, "model_error": None}

@app.get("/model-info", response_model=ModelInfoResponse)
def model_info():
    predictor = prediction_service.predictor
    if predictor is None:
        raise HTTPException(
            status_code=503,
            detail="Model artifacts are unavailable.",
        )
    return {
        "model_name": predictor.model_name,
        "model_version": predictor.model_version,
        "sequence_length": predictor.sequence_length,
    }

@app.post("/predict", response_model=PredictionResponse)
def predict(request: PredictionRequest):
    url = request.url.strip()
    if not _is_valid_http_url(url):
        raise HTTPException(status_code=400, detail="URL must be an absolute HTTP or HTTPS URL.")
    try:
        return prediction_service.predict(url)
    except ModelUnavailableError as exc:
        raise HTTPException(status_code=503, detail="Model is unavailable.") from exc
    except Exception as exc:
        # Avoid reflecting request data, local paths, or runtime internals.
        raise HTTPException(status_code=500, detail="Inference failed.") from exc

@app.post("/feedback", response_model=FeedbackResponse)
def feedback(request: FeedbackRequest):
    updated = add_feedback(request.prediction_id, request.actual_label)
    if not updated:
        raise HTTPException(status_code=404, detail="Prediction record not found.")
    return {
        "message": "Feedback recorded successfully.",
        "prediction_id": request.prediction_id,
        "actual_label": request.actual_label,
    }

@app.get("/predictions", response_model=list[PredictionLog])
def predictions(
    limit: int = Query(default=100, ge=1, le=1000),
    include_urls: bool = Query(default=False),
    token: str | None = Header(default=None, alias="X-PhishGuard-Admin-Token"),
):
    _authorize_predictions(token)
    rows = get_predictions(limit)
    if not include_urls:
        for row in rows:
            row["url"] = None
    return rows

@app.get("/")
def root():
    return {
        "service": "PhishGuard API",
        "status": "running",
        "docs": "/docs",
        "endpoints": ["/health", "/model-info", "/predict", "/feedback", "/predictions"],
    }
