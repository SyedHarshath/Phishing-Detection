from contextlib import asynccontextmanager
from fastapi import FastAPI, HTTPException, Query

from backend.app.database import (
    initialize_database, add_feedback, get_predictions
)
from backend.app.schemas import (
    PredictionRequest, PredictionResponse, HealthResponse,
    ModelInfoResponse, FeedbackRequest, FeedbackResponse, PredictionLog
)
from backend.app.services.prediction import prediction_service

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

@app.get("/health", response_model=HealthResponse)
def health_check():
    model_loaded = prediction_service.is_model_loaded()
    return {
        "status": "healthy" if model_loaded else "degraded",
        "model_loaded": model_loaded,
        "model_error": prediction_service.load_error,
    }

@app.get("/model-info", response_model=ModelInfoResponse)
def model_info():
    predictor = prediction_service.predictor
    if predictor is None:
        raise HTTPException(
            status_code=503,
            detail=prediction_service.load_error or "Model artifacts are not loaded.",
        )
    return {
        "model_name": predictor.model_name,
        "model_version": predictor.model_version,
        "sequence_length": predictor.sequence_length,
    }

@app.post("/predict", response_model=PredictionResponse)
def predict(request: PredictionRequest):
    try:
        return prediction_service.predict(request.url)
    except (ValueError, TypeError) as exc:
        raise HTTPException(status_code=400, detail=str(exc))
    except RuntimeError as exc:
        raise HTTPException(status_code=503, detail=str(exc))
    except Exception as exc:
        raise HTTPException(status_code=500, detail=f"Prediction failed: {exc}")

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
def predictions(limit: int = Query(default=100, ge=1, le=1000)):
    return get_predictions(limit)

@app.get("/")
def root():
    return {
        "service": "PhishGuard API",
        "status": "running",
        "docs": "/docs",
        "endpoints": ["/health", "/model-info", "/predict", "/feedback", "/predictions"],
    }
