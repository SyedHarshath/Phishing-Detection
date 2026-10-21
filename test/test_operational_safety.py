import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from fastapi.testclient import TestClient

from backend.app.main import _is_valid_http_url, app
from backend.app.config import get_settings
from backend.app.services.prediction import ModelUnavailableError, PredictionService, prediction_service


class OperationalSafetyTests(unittest.TestCase):
    def test_liveness_and_readiness_are_distinct(self):
        from backend.app import database
        with tempfile.TemporaryDirectory() as temp:
            with patch.object(database, "DB_URL", None), \
                 patch.object(database, "DB_PATH", Path(temp) / "readiness.sqlite"):
                database.initialize_database()
                with patch.object(prediction_service, "is_model_loaded", return_value=False):
                    client = TestClient(app)
                    self.assertEqual(client.get("/live").status_code, 200)
                    self.assertEqual(client.get("/ready").status_code, 503)
                with patch.object(prediction_service, "is_model_loaded", return_value=True):
                    ready = TestClient(app).get("/ready")
        self.assertEqual(ready.status_code, 200)
        self.assertEqual(ready.json()["status"], "ready")

    def test_readiness_fails_when_database_cannot_be_reached(self):
        with patch("backend.app.main.check_database_connection", side_effect=OSError("unavailable")), \
             patch.object(prediction_service, "is_model_loaded", return_value=True):
            response = TestClient(app).get("/ready")
        self.assertEqual(response.status_code, 503)
        self.assertEqual(response.json()["detail"], "Database is unavailable.")

    def test_health_and_model_info_do_not_disclose_artifact_paths(self):
        internal_error = r"Tokenizer not found C:\private\model-store\tokenizer.json"
        with patch.object(prediction_service, "is_model_loaded", return_value=False), \
             patch.object(prediction_service, "_predictor", None), \
             patch.object(prediction_service, "_load_error", internal_error):
            client = TestClient(app)  # no lifespan: avoid touching the configured production DB
            health = client.get("/health")
            info = client.get("/model-info")
        self.assertEqual(health.status_code, 200)
        self.assertEqual(health.json()["status"], "degraded")
        self.assertEqual(health.json()["model_error"], "model_unavailable")
        self.assertNotIn("private", health.text)
        self.assertEqual(info.status_code, 503)
        self.assertNotIn("private", info.text)
        self.assertNotIn("tokenizer.json", info.text)

    def test_unexpected_inference_failure_is_sanitized(self):
        marker = "internal-stack-path-C:\\private\\request-secret"
        with patch.object(prediction_service, "predict", side_effect=ValueError(marker)):
            client = TestClient(app)  # no lifespan: the mocked failure precedes persistence
            response = client.post("/predict", json={"url": "https://fixture.invalid/"})
        self.assertEqual(response.status_code, 500)
        self.assertNotIn(marker, response.text)
        self.assertEqual(response.json()["detail"], "Inference failed.")

    def test_model_unavailable_is_distinct_from_inference_failure(self):
        marker = "Tokenizer not found C:\\private\\tokenizer.json"
        with patch.object(prediction_service, "predict", side_effect=ModelUnavailableError(marker)):
            client = TestClient(app)
            response = client.post("/predict", json={"url": "https://fixture.invalid/"})
        self.assertEqual(response.status_code, 503)
        self.assertNotIn(marker, response.text)
        self.assertEqual(response.json()["detail"], "Model is unavailable.")

    def test_inference_runtime_error_is_not_reported_as_model_unavailable(self):
        with patch.object(prediction_service, "predict", side_effect=RuntimeError("kernel failed")):
            response = TestClient(app).post("/predict", json={"url": "https://fixture.invalid/"})
        self.assertEqual(response.status_code, 500)
        self.assertEqual(response.json()["detail"], "Inference failed.")

    def test_prediction_history_is_disabled_without_token_and_redacts_urls_by_default(self):
        rows = [{"id": 1, "timestamp": "now", "url": "https://private.invalid/path?token=x",
                 "prediction": "phishing", "confidence": 99.0, "model_version": "1.0.0"}]
        with patch.dict(os.environ, {}, clear=True), patch("backend.app.main.get_predictions") as get_rows:
            response = TestClient(app).get("/predictions")
            get_rows.assert_not_called()
        self.assertEqual(response.status_code, 503)

        with patch.dict(os.environ, {"PHISHGUARD_PREDICTIONS_TOKEN": "test-secret"}), \
             patch("backend.app.main.get_predictions", return_value=rows):
            response = TestClient(app).get("/predictions", headers={"X-PhishGuard-Admin-Token": "test-secret"})
        self.assertEqual(response.status_code, 200)
        self.assertIsNone(response.json()[0]["url"])
        self.assertNotIn("private.invalid", response.text)

    def test_prediction_history_returns_urls_only_for_explicit_authenticated_request(self):
        rows = [{"id": 1, "timestamp": "now", "url": "https://private.invalid/path?token=x",
                 "prediction": "phishing", "confidence": 99.0, "model_version": "1.0.0"}]
        with patch.dict(os.environ, {"PHISHGUARD_PREDICTIONS_TOKEN": "test-secret"}), \
             patch("backend.app.main.get_predictions", return_value=rows) as get_rows:
            response = TestClient(app).get("/predictions?include_urls=true",
                                           headers={"X-PhishGuard-Admin-Token": "test-secret"})
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()[0]["url"], rows[0]["url"])
        get_rows.assert_called_once_with(100)

    def test_prediction_history_rejects_missing_or_wrong_token(self):
        with patch.dict(os.environ, {"PHISHGUARD_PREDICTIONS_TOKEN": "test-secret"}), \
             patch("backend.app.main.get_predictions") as get_rows:
            client = TestClient(app)
            missing = client.get("/predictions")
            wrong = client.get("/predictions", headers={"X-PhishGuard-Admin-Token": "wrong"})
        self.assertEqual(missing.status_code, 401)
        self.assertEqual(wrong.status_code, 401)
        get_rows.assert_not_called()

    def test_http_url_validation_accepts_standard_host_forms(self):
        for url in (
            "https://example.com/path?q=1",
            "http://127.0.0.1:8080/",
            "http://[2001:db8::1]:8080/",
            "https://例え.テスト/",
            "https://example.com./",
        ):
            with self.subTest(url=url):
                self.assertTrue(_is_valid_http_url(url))

    def test_http_url_validation_rejects_missing_hosts_and_invalid_ports(self):
        for url in (
            "http:///missing-host", "http://:80/", "https://example.com:abc/",
            "https://example.com:65536/", "https://example.com:/", "http://[2001:db8::1/", "http://999.1.1.1/",
            "https://bad host.example/", "ftp://example.com/",
            "https://user:password@example.com/private/",
        ):
            with self.subTest(url=url):
                self.assertFalse(_is_valid_http_url(url))

    def test_prediction_service_uses_typed_error_only_when_model_is_unavailable(self):
        service = PredictionService()
        with patch("backend.app.services.prediction.PhishingPredictor", side_effect=OSError("load failed")):
            with self.assertRaises(ModelUnavailableError):
                service.predict("https://fixture.invalid/")

    def test_invalid_url_is_distinguished_before_inference(self):
        with patch.object(prediction_service, "predict") as predict:
            client = TestClient(app)
            response = client.post("/predict", json={"url": "file:///private/path"})
        self.assertEqual(response.status_code, 400)
        self.assertEqual(response.json()["detail"], "URL must be an absolute HTTP or HTTPS URL.")
        predict.assert_not_called()

    def test_oversized_url_is_rejected_before_inference(self):
        with patch.object(prediction_service, "predict") as predict:
            response = TestClient(app).post("/predict", json={"url": "https://a.test/" + "x" * 4096})
        self.assertEqual(response.status_code, 422)
        predict.assert_not_called()

    def test_production_settings_require_absolute_database_and_artifact_pair(self):
        with tempfile.TemporaryDirectory() as tmp:
            with patch.dict(os.environ, {
                "PHISHGUARD_ENV": "production",
                "DATABASE_URL": "postgresql://user:password@localhost:5432/testdb",
                "PHISHGUARD_DATABASE_PATH": str(Path(tmp) / "predictions.sqlite"),
                "PHISHGUARD_MODEL_PATH": str(Path(tmp) / "model.h5"),
                "PHISHGUARD_TOKENIZER_PATH": str(Path(tmp) / "tokenizer.json"),
                "PHISHGUARD_MODEL_VERSION": "reviewed-release-test",
            }, clear=True):
                settings = get_settings()
                self.assertTrue(settings.database_path.is_absolute())
                self.assertEqual(settings.environment, "production")
            with patch.dict(os.environ, {
                "PHISHGUARD_ENV": "production",
                "PHISHGUARD_DATABASE_PATH": str(Path(tmp) / "predictions.sqlite"),
            }, clear=True):
                with self.assertRaisesRegex(ValueError, "requires DATABASE_URL"):
                    get_settings()
            with patch.dict(os.environ, {
                "PHISHGUARD_ENV": "production",
                "DATABASE_URL": "postgresql://user:password@localhost:5432/testdb",
                "PHISHGUARD_DATABASE_PATH": str(Path(tmp) / "predictions.sqlite"),
            }, clear=True):
                with self.assertRaisesRegex(ValueError, "requires an existing"):
                    get_settings()

    def test_database_path_environment_override_is_resolved_without_startup(self):
        with tempfile.TemporaryDirectory() as temp:
            requested = Path(temp) / "isolated" / "predictions.sqlite"
            env = os.environ.copy()
            env["PHISHGUARD_DATABASE_PATH"] = str(requested)
            result = subprocess.run(
                [sys.executable, "-c", "from backend.app.database import DB_PATH; print(DB_PATH.resolve())"],
                check=True, capture_output=True, text=True, env=env,
            )
            self.assertEqual(Path(result.stdout.strip()), requested.resolve())
            self.assertFalse(requested.exists())


if __name__ == "__main__":
    unittest.main()
