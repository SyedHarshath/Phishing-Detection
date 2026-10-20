from typing import Literal, Optional
from pydantic import BaseModel, Field
from backend.app.config import get_settings

MAX_URL_LENGTH = get_settings().max_url_length

class PredictionRequest(BaseModel):
    url: str = Field(..., min_length=1, max_length=MAX_URL_LENGTH)

class PredictionResponse(BaseModel):
    prediction_id: int
    prediction: str
    confidence: float
    model_version: str

class HealthResponse(BaseModel):
    status: str
    model_loaded: bool
    model_error: Optional[str] = None

class ModelInfoResponse(BaseModel):
    model_name: str
    model_version: str
    sequence_length: int

class FeedbackRequest(BaseModel):
    prediction_id: int = Field(..., gt=0)
    actual_label: Literal["phishing", "legitimate"]

class FeedbackResponse(BaseModel):
    message: str
    prediction_id: int
    actual_label: str

class PredictionLog(BaseModel):
    id: int
    timestamp: str
    url: Optional[str] = None
    prediction: str
    confidence: float
    model_version: str
    feedback: Optional[str] = None
    feedback_timestamp: Optional[str] = None
