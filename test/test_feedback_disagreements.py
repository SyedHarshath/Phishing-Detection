import csv
import json
import sqlite3
import tempfile
import unittest
from pathlib import Path

from mlops.data.disagreements import FIELDS, extract, normalize_label


class FeedbackDisagreementTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.db = self.root / "predictions.sqlite"
        self.splits = self.root / "splits"
        self.splits.mkdir()
        (self.splits / "train.txt").write_text("phishing\thttps://train.example/path\n", encoding="utf-8")
        (self.splits / "val.txt").write_text("legitimate\thttps://val.example/path\n", encoding="utf-8")
        (self.splits / "test.txt").write_text("legitimate\thttps://test.example/path\n", encoding="utf-8")
        self._create_db()

    def tearDown(self):
        self.temp.cleanup()

    def _create_db(self):
        conn = sqlite3.connect(self.db)
        fields = ",".join(f'"{name}" TEXT' for name in FIELDS)
        conn.execute(f"CREATE TABLE predictions ({fields})")

        def add(row_id, url, prediction, feedback, *, status="pending_verification",
                provenance=None, verified_label=None, eligible=0):
            values = {
                "id": row_id, "url": url, "prediction": prediction, "feedback": feedback,
                "confidence": "0.9", "model_version": "fixture-v1", "timestamp": "t1",
                "feedback_timestamp": "t2", "verification_status": status,
                "label_provenance": provenance, "verified_label": verified_label,
                "verified_by": "reviewer" if verified_label else None,
                "verified_at": "t3" if verified_label else None,
                "training_eligible": str(eligible),
            }
            conn.execute(f"INSERT INTO predictions VALUES ({','.join('?' for _ in FIELDS)})",
                         [values[name] for name in FIELDS])

        add(1, "https://pending.example/a", " phishing ", "LEGITIMATE")
        add(2, "https://approved.example/a", "phishing", "legitimate",
            status="verified", provenance="reviewed-source", verified_label="LEGITIMATE", eligible=1)
        # Same canonical URL: retain both for review, exclude both from training.
        add(3, "https://approved.example/a#fragment", "phishing", "legitimate",
            status="verified", provenance="reviewed-source", verified_label="legitimate", eligible=1)
        add(4, "https://val.example/a", "phishing", "legitimate",
            status="verified", provenance="reviewed-source", verified_label="legitimate", eligible=1)
        add(5, "https://train.example/path", "phishing", "legitimate",
            status="verified", provenance="reviewed-source", verified_label="legitimate", eligible=1)
        add(6, "not a url", "phishing", "legitimate")
        add(7, "https://invalid-label.example", "malware", "phishing")
        add(8, "https://same.example", "legitimate", "legitimate")
        conn.commit()
        conn.close()

    def test_normalization_accepts_only_existing_canonical_labels(self):
        self.assertEqual(normalize_label(" PhIsHiNg "), "phishing")
        self.assertEqual(normalize_label("LEGITIMATE"), "legitimate")
        self.assertIsNone(normalize_label("malware"))
        self.assertIsNone(normalize_label(None))

    def test_disagreement_filter_eligibility_dedup_and_safe_export(self):
        before = sqlite3.connect(self.db)
        before_count = before.execute("SELECT COUNT(*) FROM predictions").fetchone()[0]
        before_tables = before.execute("SELECT name FROM sqlite_master WHERE type='table'").fetchall()
        before.close()

        output = self.root / "new-stage"
        summary = extract(self.db, output, dataset_dir=self.splits)
        self.assertEqual(summary["total_records"], 8)
        self.assertEqual(summary["records_with_feedback"], 8)
        self.assertEqual(summary["disagreements"], 5)
        self.assertEqual(summary["invalid_feedback_rows"], 2)
        self.assertEqual(summary["duplicate_feedback_urls"], 1)
        self.assertEqual(summary["pending_verification_feedback_records"], 4)
        self.assertEqual(summary["pending_verification_disagreements"], 1)
        self.assertEqual(summary["approved_training_examples"], 0)

        with (output / "disagreement-review.csv").open(encoding="utf-8", newline="") as stream:
            review = list(csv.DictReader(stream))
        self.assertEqual([row["id"] for row in review], ["1", "2", "3", "4", "5"])
        self.assertEqual(review[0]["normalized_feedback"], "legitimate")
        self.assertTrue((output / "approved-training-eligible.tsv").read_text(encoding="utf-8") == "")
        self.assertTrue((output / "approved-training-provenance.jsonl").read_text(encoding="utf-8") == "")
        self.assertEqual(json.loads((output / "summary.json").read_text())["table"], "predictions")

        after = sqlite3.connect(self.db)
        self.assertEqual(after.execute("SELECT COUNT(*) FROM predictions").fetchone()[0], before_count)
        self.assertEqual(after.execute("SELECT name FROM sqlite_master WHERE type='table'").fetchall(), before_tables)
        after.close()

    def test_valid_unique_verified_example_is_exported_with_provenance(self):
        conn = sqlite3.connect(self.db)
        conn.execute("UPDATE predictions SET url='https://unique.example/a' WHERE id=2")
        conn.execute("UPDATE predictions SET url='https://unique2.example/a' WHERE id=3")
        conn.commit()
        conn.close()
        summary = extract(self.db, self.root / "stage2", dataset_dir=self.splits)
        self.assertEqual(summary["approved_training_examples"], 2)
        lines = (self.root / "stage2" / "approved-training-eligible.tsv").read_text(encoding="utf-8").splitlines()
        self.assertEqual(lines, ["legitimate\thttps://unique.example/a", "legitimate\thttps://unique2.example/a"])
        provenance = [json.loads(line) for line in (self.root / "stage2" / "approved-training-provenance.jsonl").read_text().splitlines()]
        self.assertEqual([r["id"] for r in provenance], ["2", "3"])
        self.assertTrue(all(r["label_provenance"] == "reviewed-source" for r in provenance))

    def test_split_check_failure_blocks_all_approved_rows(self):
        (self.splits / "test.txt").unlink()
        summary = extract(self.db, self.root / "stage3", dataset_dir=self.splits)
        self.assertTrue(summary["approved_subset_blocked_by_split_check"])
        self.assertEqual(summary["approved_training_examples"], 0)

    def test_existing_output_directory_is_never_overwritten(self):
        output = self.root / "existing"
        output.mkdir()
        marker = output / "keep.txt"
        marker.write_text("preserve", encoding="utf-8")
        with self.assertRaises(FileExistsError):
            extract(self.db, output, dataset_dir=self.splits)
        self.assertEqual(marker.read_text(encoding="utf-8"), "preserve")


if __name__ == "__main__":
    unittest.main()
