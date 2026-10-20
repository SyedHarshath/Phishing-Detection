import sys
import pickle
from pathlib import Path

import numpy as np
from tensorflow.keras.models import load_model
from tensorflow.keras.preprocessing.text import tokenizer_from_json

from backend.app.config import get_settings
from ml.preprocessing import SEQUENCE_LENGTH, preprocess_url

MODEL_NAME = "phishing-detector"
MODEL_VERSION = "1.0.0"

class PhishingPredictor:
    def __init__(self, model_path=None, tokenizer_path=None):
        PROJECT_ROOT = Path(__file__).resolve().parent.parent
        settings = get_settings()
        active_version = None
        if model_path is None and tokenizer_path is None:
            active_manifest = settings.active_model_manifest
            if active_manifest.is_file():
                from mlops.promotion.registry import resolve_active_paths
                model_path, tokenizer_path, active_version = resolve_active_paths(active_manifest)
            elif settings.model_path and settings.tokenizer_path:
                model_path, tokenizer_path = settings.model_path, settings.tokenizer_path
            elif settings.environment == "production":
                raise FileNotFoundError("Configured production model manifest or artifact pair is unavailable")
        if model_path is None:
            model_path = (
                PROJECT_ROOT
                / "test_results"
                / "cnn_bilstm"
                / "best_model.h5"
            )

        if tokenizer_path is None:
            tokenizer_path = (
                PROJECT_ROOT
                / "ml"
                / "artifacts"
                / "tokenizer.json"
            )

        self.model_path = Path(model_path)
        self.tokenizer_path = Path(tokenizer_path)
        self.model_name = MODEL_NAME
        self.model_version = active_version or settings.model_version or MODEL_VERSION
        self.sequence_length = SEQUENCE_LENGTH
        self.categories = ['phishing','legitimate']

        if not self.tokenizer_path.exists():
            raise FileNotFoundError(f"Tokenizer not found {self.tokenizer_path}")

        if not self.model_path.exists():
            raise FileNotFoundError(f"Model not found {self.model_path}")

        self.tokenizer = self._load_tokenizer()

        self.model = self._load_model()

    def _load_model(self):
        try:
            return load_model(self.model_path, compile=False)
        except (TypeError, ValueError) as exc:
            # Older Keras versions cannot deserialize newer H5 InputLayer
            # configs. Rebuild the project architecture and load its weights.
            if "batch_shape" not in str(exc):
                raise

            from src.dl_models import DlModels

            model = DlModels(
                self.categories,
                embed_dim=128,
                sequence_length=self.sequence_length,
            ).cnn_complex(self.tokenizer.word_index)
            model.load_weights(self.model_path)
            return model

    def _load_tokenizer(self):
        if self.tokenizer_path.suffix.lower() == ".json":
            return tokenizer_from_json(
                self.tokenizer_path.read_text(encoding="utf-8")
            )

        try:
            with self.tokenizer_path.open("rb") as f:
                return pickle.load(f)
        except ModuleNotFoundError as exc:
            json_path = self.tokenizer_path.with_suffix(".json")
            if json_path.exists():
                return tokenizer_from_json(
                    json_path.read_text(encoding="utf-8")
                )
            raise RuntimeError(
                "The tokenizer pickle was created with an incompatible "
                "Keras version. Use a tokenizer.json file instead."
            ) from exc

    def predict(self,url):
        """
        Predict whether a URL is phishing or legitimate.

        Returns:
            dict containing prediction and confidence.
        """
        if not isinstance(url,str):
            raise TypeError(f"URL must be a string")

        url = url.strip()

        if not url:
            raise ValueError("URL cannot be empty")

        padded_sequence = preprocess_url(url,self.tokenizer)
        probabilities = self.model.predict(padded_sequence,verbose=0)[0]

        prediction_index = np.argmax(probabilities)

        result = self.categories[prediction_index]

        confidence = (float(probabilities[prediction_index])*100)

        return {
            "prediction": result,
            "confidence": round(confidence, 2),
            "model_version": self.model_version
        }


if __name__ == '__main__':
    predictor = PhishingPredictor()
    test_url = "https://mitindia.edu"
    result = predictor.predict(test_url)
    print(result)
