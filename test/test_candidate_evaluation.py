import unittest

from mlops.evaluation.compare import compare_probabilities, dataset_fingerprint


class CandidateEvaluationTests(unittest.TestCase):
    def setUp(self):
        self.labels = [0, 0, 1, 1]
        self.urls = ["https://phish-a.invalid", "https://phish-b.invalid",
                     "https://legit-a.invalid", "https://legit-b.invalid"]
        self.manifest = {"trusted_ground_truth": True, "independent": True,
                         "domain_overlap_reviewed": True,
                         "dataset_fingerprint": dataset_fingerprint(self.labels, self.urls),
                         "training_dataset_fingerprint": "training-fp",
                         "training_validation_fingerprint": "validation-fp",
                         "source_reference": "reviewed-fixture", "frozen_at_utc": "2026-01-01T00:00:00Z",
                         "label_provenance": "independent-review",
                         "training_url_overlap_count": 0, "training_domain_overlap_count": 0,
                         "overlap_audit_sha256": "audit-hash",
                         "incumbent_training_history_verified": True,
                         "incumbent_training_history_reference": "verified-fixture-run"}
        self.policy = {"enabled": True, "version": "test-v1", "thresholds": {
            "max_phishing_fnr": 0.0, "max_legitimate_fpr": 0.0,
            "min_phishing_recall": 1.0, "min_legitimate_recall": 1.0,
            "min_phishing_precision": 1.0, "min_legitimate_precision": 1.0,
            "max_phishing_fnr_regression": 0.0, "max_legitimate_fpr_regression": 0.0,
            "max_latency_ratio": 2.0}}

    def test_passes_only_with_provenance_policy_and_both_classes(self):
        probs = [[.9,.1],[.8,.2],[.1,.9],[.2,.8]]
        result = compare_probabilities(labels=self.labels, urls=self.urls,
            candidate_probabilities=probs, incumbent_probabilities=probs,
            candidate_id="cand", incumbent_id="prod", evaluation_manifest=self.manifest,
            policy=self.policy, candidate_latency_seconds=.4, incumbent_latency_seconds=.4,
            training_dataset_fingerprint="training-fp")
        self.assertEqual(result["decision"], "PASS")
        self.assertEqual(result["candidate_metrics"]["confusion_matrix_labels_phishing_legitimate"], [[2,0],[0,2]])
        self.assertFalse(result["promotion_enabled"])

    def test_phishing_miss_rejects_and_missing_trust_blocks(self):
        probs = [[.1,.9],[.8,.2],[.1,.9],[.2,.8]]
        result = compare_probabilities(labels=self.labels, urls=self.urls,
            candidate_probabilities=probs, incumbent_probabilities=probs,
            candidate_id="cand", incumbent_id="prod", evaluation_manifest=self.manifest,
            policy=self.policy, candidate_latency_seconds=.4, incumbent_latency_seconds=.4,
            training_dataset_fingerprint="training-fp")
        self.assertEqual(result["decision"], "REJECT")
        blocked = compare_probabilities(labels=self.labels, urls=self.urls,
            candidate_probabilities=probs, incumbent_probabilities=probs,
            candidate_id="cand", incumbent_id="prod", evaluation_manifest=None,
            policy=self.policy, training_dataset_fingerprint="training-fp")
        self.assertEqual(blocked["decision"], "BLOCKED")

    def test_unknown_incumbent_training_history_blocks_even_zero_overlap(self):
        manifest = dict(self.manifest, incumbent_training_history_verified=False)
        result = compare_probabilities(labels=self.labels, urls=self.urls,
            candidate_probabilities=[[.9,.1],[.8,.2],[.1,.9],[.2,.8]],
            incumbent_probabilities=[[.9,.1],[.8,.2],[.1,.9],[.2,.8]],
            candidate_id="cand", incumbent_id="prod", evaluation_manifest=manifest,
            policy=self.policy, training_dataset_fingerprint="training-fp")
        self.assertEqual(result["decision"], "BLOCKED")
        self.assertIn("evaluation_manifest_missing_or_untrusted", result["blocking_reasons"])


if __name__ == "__main__":
    unittest.main()
