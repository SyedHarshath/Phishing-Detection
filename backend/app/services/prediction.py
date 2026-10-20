from ml.prediction import PhishingPredictor
from backend.app.database import log_prediction

class ModelUnavailableError(Exception):
    """Raised only when model artifacts cannot be loaded."""

class PredictionService:
    def __init__(self):
        self._predictor = None
        self._load_error = None

    @property
    def predictor(self):
        if self._predictor is None and self._load_error is None:
            try:
                self._predictor = PhishingPredictor()
            except Exception as exc:
                self._load_error = str(exc)
        return self._predictor

    @property
    def load_error(self):
        self.predictor
        return self._load_error

    def is_model_loaded(self):
        return self.predictor is not None

    def predict(self, url: str):
        predictor = self.predictor
        if predictor is None:
            detail = self.load_error or "Model artifacts are not loaded."
            raise ModelUnavailableError(detail)

        result = predictor.predict(url)
        prediction_id = log_prediction(
            url=url,
            prediction=result["prediction"],
            confidence=result["confidence"],
            model_version=result["model_version"],
        )
        result["prediction_id"] = prediction_id
        return result

prediction_service = PredictionService()
