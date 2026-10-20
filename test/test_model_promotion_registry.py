import hashlib
import json
import math
import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from mlops.promotion.registry import (
    _exclusive_lock, _policy_hash, _sha, bootstrap_legacy_incumbent, initialize_registry, promote,
    recover_registry, resolve_active_paths, rollback, verify_bundle,
)


class _Tokenizer:
    char_level = True
    lower = True
    oov_token = "-n-"


class _Predictor:
    categories = ["phishing", "legitimate"]
    sequence_length = 200
    tokenizer = _Tokenizer()

    def predict(self, url):
        return {"prediction": "phishing", "confidence": 99.0}


def _loader(model_path, tokenizer_path):
    return _Predictor()


def _bundle(path: Path, version: str, kind="real_candidate"):
    path.mkdir(parents=True)
    (path / "best_model.h5").write_bytes(("model-" + version).encode())
    (path / "tokenizer.json").write_text('{"tokenizer":"fixture"}', encoding="utf-8")
    manifest = {"bundle_schema_version": 1, "candidate_id": version,
        "model_version": version, "bundle_type": kind,
        "role": "candidate-not-production", "test_only": kind == "synthetic_test_candidate",
        "class_order": ["phishing", "legitimate"],
        "label_mapping": {"phishing": 0, "legitimate": 1}, "sequence_length": 200,
        "preprocessing_version": "url-character-lowercase-pad200-v1",
        "training_dataset_fingerprint": "train-fp", "created_at_utc": "now",
        "metadata": {"source_provenance_verified": True, "authoritative_registration": True,
                     "source_registration_sha256": "a" * 64,
                     "candidate_origin": "approved_dataset_import"},
        "compatibility": {"model_format": "keras-h5", "tokenizer_format": "keras-json"},
        "files": {"best_model.h5": _sha(path / "best_model.h5"),
                  "tokenizer.json": _sha(path / "tokenizer.json")}}
    (path / "bundle.json").write_text(json.dumps(manifest), encoding="utf-8")
    return manifest


class ModelPromotionRegistryTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.registry = self.root / "registry"
        self.incumbent = self.root / "incumbent"
        self.candidate = self.root / "candidate"
        _bundle(self.incumbent, "incumbent", "legacy_incumbent")
        self.candidate_manifest = _bundle(self.candidate, "candidate-v2")
        initialize_registry(self.registry, self.incumbent, loader=_loader)
        self.policy_path = self.root / "policy.json"
        self.policy = {"enabled": True, "version": "policy-v1", "thresholds": {
            "max_phishing_fnr": 0.1, "max_legitimate_fpr": 0.1,
            "min_phishing_recall": 0.9, "min_legitimate_recall": 0.9,
            "min_phishing_precision": 0.9, "min_legitimate_precision": 0.9,
            "max_phishing_fnr_regression": 0.05, "max_legitimate_fpr_regression": 0.05,
            "max_latency_ratio": 2.0}}
        self.policy_path.write_text(json.dumps(self.policy), encoding="utf-8")
        active_incumbent = self.registry / "versions" / "incumbent"
        self.report_path = self.root / "evaluation.json"
        report = {"decision": "PASS", "candidate_id": "candidate-v2",
            "incumbent_id": "incumbent", "dataset_fingerprint": "independent-eval-fp",
            "training_dataset_fingerprint": "train-fp",
            "training_validation_fingerprint": "validation-fp",
            "policy_version": "policy-v1", "policy_sha256": _policy_hash(self.policy),
            "evaluation_manifest_sha256": "manifest-fp",
            "candidate_bundle_sha256": _sha(self.candidate / "bundle.json"),
            "incumbent_bundle_sha256": _sha(active_incumbent / "bundle.json"),
            "evaluation_independence": {"trusted_ground_truth": True, "independent": True,
                                         "domain_overlap_reviewed": True,
                                         "incumbent_training_history_verified": True},
            "candidate_metrics": {"accuracy": .95, "balanced_accuracy": .95,
                "phishing_false_negative_rate": .05, "legitimate_false_positive_rate": .05,
                "mean_max_confidence": .95, "latency_seconds": .04,
                "latency_seconds_per_example": .01, "model_size_bytes": 100,
                "confusion_matrix_labels_phishing_legitimate": [[20, 1], [1, 20]],
                "per_class": {"phishing": {"precision": .95, "recall": .95, "f1": .95, "support": 21},
                              "legitimate": {"precision": .95, "recall": .95, "f1": .95, "support": 21}}}}
        self.report_path.write_text(json.dumps(report), encoding="utf-8")
        self.provenance_path = self.root / "provenance.json"
        self.provenance_path.write_text(json.dumps({"passed": True, "dataset_fingerprint": "train-fp",
            "training_validation_fingerprint": "validation-fp", "validation_passed": True,
            "dvc_clean": True, "source_provenance_verified": True,
            "authoritative_registration": True, "source_registration_sha256": "a" * 64}), encoding="utf-8")
        self.dvc = {"ok": True, "stdout": "", "stderr": ""}

    def _promote(self, **kwargs):
        return promote(self.candidate, self.report_path, self.policy_path, self.provenance_path,
            root=self.registry, enabled=True, confirm=True, dvc_status=self.dvc,
            loader=_loader, **kwargs)

    def test_bundle_integrity_and_test_only_gate(self):
        self.assertEqual(verify_bundle(self.candidate, loader=_loader)["manifest"]["model_version"], "candidate-v2")
        (self.candidate / "best_model.h5").write_bytes(b"corrupt")
        with self.assertRaisesRegex(ValueError, "corrupt"):
            verify_bundle(self.candidate, loader=_loader)
        (self.candidate / "best_model.h5").write_bytes(b"model-candidate-v2")
        test_dir = self.root / "test-only"
        _bundle(test_dir, "test-v1", "synthetic_test_candidate")
        with self.assertRaisesRegex(ValueError, "test-only"):
            verify_bundle(test_dir, loader=_loader)

    def test_unregistered_real_candidate_bundle_is_rejected(self):
        self.candidate_manifest["metadata"] = {}
        (self.candidate / "bundle.json").write_text(json.dumps(self.candidate_manifest), encoding="utf-8")
        with self.assertRaisesRegex(ValueError, "approved dataset source registration"):
            verify_bundle(self.candidate, require_real=True, loader=_loader)

    def test_disabled_and_unconfirmed_promotion_are_blocked(self):
        self.assertEqual(promote(self.candidate, self.report_path, self.policy_path, self.provenance_path,
            root=self.registry, enabled=False, confirm=True)["decision"], "BLOCKED")
        self.assertEqual(promote(self.candidate, self.report_path, self.policy_path, self.provenance_path,
            root=self.registry, enabled=True, confirm=False)["decision"], "BLOCKED")
        self.assertEqual(json.loads((self.registry / "active.json").read_text())["model_version"], "incumbent")

    def test_stale_incumbent_or_untrusted_evaluation_blocks(self):
        report = json.loads(self.report_path.read_text()); report["incumbent_id"] = "other"
        self.report_path.write_text(json.dumps(report))
        result = self._promote()
        self.assertEqual(result["decision"], "BLOCKED")
        self.assertEqual(json.loads((self.registry / "active.json").read_text())["model_version"], "incumbent")

    def test_missing_authoritative_dataset_registration_blocks_promotion(self):
        provenance = json.loads(self.provenance_path.read_text())
        provenance["authoritative_registration"] = False
        self.provenance_path.write_text(json.dumps(provenance))
        result = self._promote()
        self.assertEqual(result["decision"], "BLOCKED")
        self.assertEqual(result["reason"], "approved_dataset_source_registration_missing_or_mismatched")
        self.assertEqual(json.loads((self.registry / "active.json").read_text())["model_version"], "incumbent")

    def test_successful_promotion_and_verified_rollback(self):
        result = self._promote()
        self.assertEqual(result["decision"], "PROMOTED")
        self.assertTrue(result["restart_required"])
        self.assertEqual(json.loads((self.registry / "active.json").read_text())["model_version"], "candidate-v2")
        reverted = rollback(root=self.registry, reason="synthetic health check failed", confirm=True, loader=_loader)
        self.assertEqual(reverted["decision"], "ROLLED_BACK")
        self.assertEqual(reverted["active_version"], "incumbent")
        registry = json.loads((self.registry / "registry.json").read_text())
        self.assertEqual([event["action"] for event in registry["history"]], ["promote", "rollback"])

    def test_interrupted_registry_metadata_can_be_recovered(self):
        self.assertEqual(self._promote()["decision"], "PROMOTED")
        path = self.registry / "registry.json"
        registry = json.loads(path.read_text())
        registry["versions"].pop("candidate-v2")
        registry["history"] = []
        path.write_text(json.dumps(registry))
        result = recover_registry(self.registry, loader=_loader)
        self.assertEqual(result["decision"], "RECOVERED")
        updated = json.loads(path.read_text())
        self.assertIn("candidate-v2", updated["versions"])
        self.assertTrue(updated["history"][0]["recovered"])

    def test_registry_lock_prevents_concurrent_switch(self):
        with _exclusive_lock(self.registry):
            with self.assertRaisesRegex(RuntimeError, "another registry"):
                with _exclusive_lock(self.registry):
                    pass

    def test_active_manifest_resolves_matched_pair_for_startup_loader(self):
        active = json.loads((self.registry / "active.json").read_text())
        model, tokenizer, version = resolve_active_paths(self.registry / "active.json")
        self.assertEqual(version, "incumbent")
        self.assertEqual(model, self.registry / active["bundle_path"] / "best_model.h5")
        self.assertEqual(tokenizer, self.registry / active["bundle_path"] / "tokenizer.json")

    def test_predictor_uses_active_pair_at_initialization(self):
        from unittest.mock import patch
        from ml.prediction import PhishingPredictor
        with patch.dict("os.environ", {"PHISHGUARD_ACTIVE_MODEL_MANIFEST": str(self.registry / "active.json")}), \
             patch.object(PhishingPredictor, "_load_model", return_value=object()), \
             patch.object(PhishingPredictor, "_load_tokenizer", return_value=_Tokenizer()):
            predictor = PhishingPredictor()
        self.assertEqual(predictor.model_version, "incumbent")
        self.assertEqual(predictor.model_path, self.registry / "versions" / "incumbent" / "best_model.h5")

    def test_actual_production_pair_loads_from_temporary_active_manifest(self):
        model_path = Path("test_results/cnn_bilstm/best_model.h5").resolve()
        tokenizer_path = Path("ml/artifacts/tokenizer.json").resolve()
        if not model_path.is_file() or not tokenizer_path.is_file():
            self.skipTest("current production model/tokenizer pair is unavailable")
        before = (_sha(model_path), _sha(tokenizer_path))
        isolated_registry = self.root / "actual-load-registry"
        bootstrap_legacy_incumbent(isolated_registry, model_path=model_path,
                                   tokenizer_path=tokenizer_path)
        from ml.prediction import PhishingPredictor
        with patch.dict(os.environ, {
            "PHISHGUARD_ACTIVE_MODEL_MANIFEST": str(isolated_registry / "active.json")}):
            predictor = PhishingPredictor()
            result = predictor.predict("https://optimus-active-check.invalid/path")
        self.assertEqual(predictor.model_version, "legacy-incumbent")
        self.assertIn(result["prediction"], {"phishing", "legitimate"})
        self.assertTrue(math.isfinite(result["confidence"]))
        from fastapi.testclient import TestClient
        from backend.app import database
        from backend.app.main import app
        from backend.app.services.prediction import prediction_service
        api_db = self.root / "api-test.sqlite"
        old_predictor, old_error = prediction_service._predictor, prediction_service._load_error
        prediction_service._predictor = None
        prediction_service._load_error = None
        try:
            with patch.object(database, "DB_PATH", api_db), patch.object(database, "DB_DIR", api_db.parent), \
                 patch.dict(os.environ, {"PHISHGUARD_ACTIVE_MODEL_MANIFEST": str(isolated_registry / "active.json")}):
                with TestClient(app) as client:
                    self.assertEqual(client.get("/health").json()["model_loaded"], True)
                    self.assertEqual(client.get("/model-info").json()["model_version"], "legacy-incumbent")
                    response = client.post("/predict", json={"url": "https://api-active-check.invalid/"})
                    self.assertEqual(response.status_code, 200)
                    self.assertEqual(response.json()["model_version"], "legacy-incumbent")
                    prediction_id = response.json()["prediction_id"]
                    feedback = client.post("/feedback", json={
                        "prediction_id": prediction_id, "actual_label": "phishing"})
                    self.assertEqual(feedback.status_code, 200)
                    from mlops.data.lifecycle import preview_batch
                    preview = preview_batch(api_db)
                    self.assertFalse(preview["eligible"])
                    self.assertEqual(preview["record_count"], 0)
        finally:
            prediction_service._predictor, prediction_service._load_error = old_predictor, old_error
        self.assertEqual(before, (_sha(model_path), _sha(tokenizer_path)))


if __name__ == "__main__":
    unittest.main()
