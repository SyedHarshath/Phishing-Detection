import unittest

from mlops.evaluation.thresholds import guarded_threshold_tradeoff, threshold_tradeoff


class ThresholdTradeoffTests(unittest.TestCase):
    def test_confusion_rates_use_phishing_as_positive(self):
        row = threshold_tradeoff([0, 0, 1, 1], [.9, .4, .7, .1], [.5])[0]
        self.assertEqual(row["confusion_matrix_labels_phishing_legitimate"], [[1, 1], [1, 1]])
        self.assertEqual(row["phishing_recall"], .5)
        self.assertEqual(row["phishing_fnr"], .5)
        self.assertEqual(row["legitimate_specificity"], .5)
        self.assertEqual(row["legitimate_fpr"], .5)
        self.assertEqual(row["phishing_support"], 2)
        self.assertEqual(row["legitimate_support"], 2)

    def test_unverified_validation_is_blocked(self):
        result = guarded_threshold_tradeoff(labels=[0, 1], phishing_scores=[.9, .1],
            thresholds=[.5], validation_manifest=None)
        self.assertEqual(result["decision"], "BLOCKED")
        self.assertIsNone(result["threshold_recommendation"])


if __name__ == "__main__":
    unittest.main()
