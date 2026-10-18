import json
import sys
import tempfile
import unittest
from pathlib import Path
from types import ModuleType, SimpleNamespace
from unittest.mock import patch

from src import train_smoke
from src.data_loader import DatasetFormatError, DatasetSplit


class SmokeTrainingSafetyTests(unittest.TestCase):
    def test_candidate_classification_requires_approved_source_registration(self):
        self.assertFalse(train_smoke._is_authoritatively_registered({}))
        self.assertFalse(train_smoke._is_authoritatively_registered({
            "source_provenance_verified": True, "authoritative_registration": True,
            "source_registration_sha256": "short"}))
        self.assertTrue(train_smoke._is_authoritatively_registered({
            "source_provenance_verified": True, "authoritative_registration": True,
            "source_registration_sha256": "a" * 64}))

    def test_tokenizer_is_fitted_only_from_training_urls(self):
        try:
            from tensorflow.keras.preprocessing.text import Tokenizer
        except ImportError:
            self.skipTest("TensorFlow is unavailable in the selected interpreter.")

        tokenizer = train_smoke.fit_training_tokenizer(["abca"])

        self.assertIsInstance(tokenizer, Tokenizer)
        self.assertTrue(tokenizer.char_level)
        self.assertTrue(tokenizer.lower)
        self.assertNotIn("z", tokenizer.word_index)
        self.assertEqual(tokenizer.texts_to_sequences(["z"]), [[tokenizer.word_index["-n-"]]])

    def test_corrupt_training_split_writes_failure_report_without_candidate_model(self):
        with tempfile.TemporaryDirectory() as temporary_directory:
            candidate_directory = Path(temporary_directory) / "candidate_cnn_bilstm"
            hashes = {
                "production_model_sha256": "model-hash",
                "production_tokenizer_sha256": "tokenizer-hash",
            }
            with (
                patch.object(train_smoke, "CANDIDATE_DIR", candidate_directory),
                patch.object(train_smoke, "production_artifact_hashes", return_value=hashes),
                patch.object(
                    train_smoke,
                    "load_split",
                    side_effect=DatasetFormatError("zero-filled training data"),
                ),
            ):
                with self.assertRaisesRegex(DatasetFormatError, "zero-filled"):
                    train_smoke.run_smoke_training()

            report_path = candidate_directory / "smoke_test_report.json"
            self.assertTrue(report_path.is_file())
            report = json.loads(report_path.read_text(encoding="utf-8"))
            self.assertFalse(report["success"])
            self.assertEqual(
                report["production_artifact_hashes_before"],
                report["production_artifact_hashes_after"],
            )
            self.assertEqual(
                {path.name for path in candidate_directory.iterdir()},
                {"smoke_test_report.json"},
            )

    def test_production_hashes_are_stable_without_training(self):
        before = train_smoke.production_artifact_hashes()
        after = train_smoke.production_artifact_hashes()
        self.assertEqual(before, after)

    def test_tiny_fixture_training_writes_candidate_artifacts_and_metrics(self):
        try:
            import tensorflow  # noqa: F401
            import sklearn  # noqa: F401
        except ImportError:
            self.skipTest("TensorFlow and scikit-learn are required for this test.")

        splits = {
            "train": DatasetSplit(
                urls=[
                    "http://bad-login.example/a1",
                    "http://bad-update.example/b2",
                    "http://bad-account.example/c3",
                    "http://fake-auth.example/d4",
                    "https://good-shop.example/a1",
                    "https://good-news.example/b2",
                    "https://good-school.example/c3",
                    "https://good-health.example/d4",
                ],
                labels=[0, 0, 0, 0, 1, 1, 1, 1],
                split="train",
                parsed_records=8,
                malformed_records=0,
                label_counts={"phishing": 4, "legitimate": 4},
                sample_size=8,
            ),
            "val": DatasetSplit(
                urls=[
                    "http://bad-login.example/e5",
                    "http://fake-auth.example/f6",
                    "https://good-shop.example/e5",
                    "https://good-news.example/f6",
                ],
                labels=[0, 0, 1, 1],
                split="val",
                parsed_records=4,
                malformed_records=0,
                label_counts={"phishing": 2, "legitimate": 2},
                sample_size=4,
            ),
            "test": DatasetSplit(
                urls=[
                    "http://bad-update.example/g7",
                    "http://bad-account.example/h8",
                    "https://good-school.example/g7",
                    "https://good-health.example/h8",
                ],
                labels=[0, 0, 1, 1],
                split="test",
                parsed_records=4,
                malformed_records=0,
                label_counts={"phishing": 2, "legitimate": 2},
                sample_size=4,
            ),
        }
        production_hashes = {
            "production_model_sha256": "unchanged-model",
            "production_tokenizer_sha256": "unchanged-tokenizer",
        }

        class RunContext:
            def __enter__(self):
                return SimpleNamespace(info=SimpleNamespace(run_id="test-run-id"))

            def __exit__(self, *args):
                return False

        fake_mlflow = ModuleType("mlflow")
        fake_mlflow.start_run = lambda **kwargs: RunContext()
        fake_mlflow.set_tags = lambda *args, **kwargs: None
        fake_mlflow.log_params = lambda *args, **kwargs: None
        fake_mlflow.log_metrics = lambda *args, **kwargs: None
        fake_mlflow.log_artifacts = lambda *args, **kwargs: None
        fake_mlflow.log_artifact = lambda *args, **kwargs: None
        fake_mlflow_config = ModuleType("mlops.mlflow_config")
        fake_mlflow_config.configure_mlflow = lambda: "http://test-mlflow"

        with tempfile.TemporaryDirectory() as temporary_directory:
            candidate_directory = Path(temporary_directory) / "candidate_cnn_bilstm"
            with (
                patch.object(train_smoke, "CANDIDATE_DIR", candidate_directory),
                patch.object(train_smoke, "production_artifact_hashes", return_value=production_hashes),
                patch.object(train_smoke, "_dataset_version", return_value="fixture-dvc-hash"),
                patch.object(
                    train_smoke,
                    "load_split",
                    side_effect=lambda split, **kwargs: splits[split],
                ),
                patch.dict(
                    sys.modules,
                    {
                        "mlflow": fake_mlflow,
                        "mlops.mlflow_config": fake_mlflow_config,
                    },
                ),
            ):
                report = train_smoke.run_smoke_training(
                    train_samples=8,
                    val_samples=4,
                    test_samples=4,
                    epochs=1,
                    batch_size=4,
                    seed=5,
                )

            expected_files = {
                "best_model.h5",
                "tokenizer.json",
                "metrics.json",
                "classification_report.txt",
                "history.json",
                "smoke_test_report.json",
            }
            self.assertEqual(
                {path.name for path in candidate_directory.iterdir()},
                expected_files,
            )
            self.assertTrue(report["success"])
            self.assertEqual(report["mlflow_run_id"], "test-run-id")
            self.assertEqual(report["sample_sizes"]["train"], 8)
            self.assertIn("accuracy", report["test_metrics"])
            saved_metrics = json.loads(
                (candidate_directory / "metrics.json").read_text(encoding="utf-8")
            )
            self.assertEqual(saved_metrics["test"]["sample_count"], 4)
            self.assertTrue(report["production_artifacts_unchanged"])


if __name__ == "__main__":
    unittest.main()
