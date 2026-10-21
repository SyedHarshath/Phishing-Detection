import json
import hashlib
import os
import sqlite3
import tempfile
import unittest
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest.mock import patch

from mlops.data.lifecycle import (
    check_eligibility, claim_job, connect, export_batch, finish_job,
    initialize_schema, record_feedback, retry_job, review_label, scheduled_review,
)


class LabelLifecycleTests(unittest.TestCase):
    def setUp(self):
        # Explicitly opt into job creation only in this isolated test fixture.
        self.dispatch_env = patch.dict(os.environ, {"RETRAIN_AUTO_ENABLED": "true"})
        self.dispatch_env.start()
        self.addCleanup(self.dispatch_env.stop)
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.db = self.root / "predictions.db"
        with sqlite3.connect(self.db) as conn:
            conn.execute("""CREATE TABLE predictions (
                id INTEGER PRIMARY KEY AUTOINCREMENT, timestamp TEXT NOT NULL,
                url TEXT NOT NULL, prediction TEXT NOT NULL, confidence REAL NOT NULL,
                model_version TEXT NOT NULL, feedback TEXT, feedback_timestamp TEXT)""")
            conn.executemany("INSERT INTO predictions(timestamp,url,prediction,confidence,model_version) VALUES(?,?,?,?,?)", [
                ("2026-01-01T00:00:00+00:00", f"https://site{i}.example/path", "legitimate", 90.0, "v1")
                for i in range(4)
            ])
        self._init()

    def _init(self):
        initialize_schema(self.db)

    def test_migration_preserves_rows_and_feedback_becomes_pending(self):
        with sqlite3.connect(self.db) as conn:
            conn.execute("UPDATE predictions SET feedback='phishing' WHERE id=1")
        initialize_schema(self.db)
        with connect(self.db) as conn:
            self.assertEqual(conn.execute("SELECT COUNT(*) FROM predictions").fetchone()[0], 4)
            row = conn.execute("SELECT verification_status,training_eligible FROM predictions WHERE id=1").fetchone()
            self.assertEqual(tuple(row), ("pending_verification", 0))
            self.assertEqual(conn.execute("SELECT action FROM label_audit WHERE prediction_id=1").fetchone()[0], "legacy_feedback_migrated")
            self.assertEqual(conn.execute("SELECT COUNT(*) FROM feedback_events WHERE prediction_id=1").fetchone()[0], 1)

    def test_existing_backend_database_interfaces_remain_compatible(self):
        from unittest.mock import patch
        from backend.app import database
        db = self.root / "backend-interface.db"
        with patch.object(database, "DB_URL", None), patch.object(database, "DB_PATH", db), patch.object(database, "DB_DIR", db.parent):
            database.initialize_database()
            event_id = database.log_prediction("https://fixture.example/", "legitimate", 91.0, "test-v1")
            self.assertTrue(database.add_feedback(event_id, "phishing"))
            row = database.get_predictions()[0]
        self.assertEqual(row["id"], event_id)
        self.assertEqual(row["feedback"], "phishing")
        with connect(db) as conn:
            self.assertEqual(conn.execute("SELECT verification_status,training_eligible FROM predictions WHERE id=?", (event_id,)).fetchone()[:], ("pending_verification", 0))

    def test_feedback_is_pending_then_explicit_verify_is_audited(self):
        self.assertTrue(record_feedback(1, "phishing", self.db))
        self.assertTrue(record_feedback(1, "phishing", self.db))
        with connect(self.db) as conn:
            self.assertEqual(conn.execute("SELECT training_eligible FROM predictions WHERE id=1").fetchone()[0], 0)
            self.assertEqual(conn.execute("SELECT COUNT(*) FROM label_audit WHERE prediction_id=1").fetchone()[0], 1)
        review_label(1, "verify", "analyst-a", "independent analyst review", "phishing", "confirmed", self.db)
        record_feedback(1, "phishing", self.db)
        with connect(self.db) as conn:
            row = conn.execute("SELECT verification_status,verified_label,verified_by,training_eligible FROM predictions WHERE id=1").fetchone()
            self.assertEqual(tuple(row), ("verified", "phishing", "analyst-a", 1))
            self.assertEqual(conn.execute("SELECT COUNT(*) FROM label_audit WHERE prediction_id=1").fetchone()[0], 2)

    def test_rejected_disputed_and_unverified_rows_are_ineligible(self):
        record_feedback(1, "phishing", self.db)
        review_label(1, "reject", "reviewer", "analyst review", reason="unsupported", db_path=self.db)
        record_feedback(2, "legitimate", self.db)
        review_label(2, "dispute", "reviewer", "analyst review", reason="conflicting evidence", db_path=self.db)
        with connect(self.db) as conn:
            self.assertEqual(conn.execute("SELECT COUNT(*) FROM predictions WHERE training_eligible=1").fetchone()[0], 0)
        self.assertEqual(export_batch(self.db, self.root / "batches")["reason"], "no_new_verified_records")

    def test_conflicting_feedback_disputes_verified_label_without_erasing_audit(self):
        review_label(1, "verify", "analyst", "trusted source", "phishing", db_path=self.db)
        self.assertTrue(record_feedback(1, "legitimate", self.db))
        with connect(self.db) as conn:
            row = conn.execute("SELECT verification_status,verified_label,training_eligible FROM predictions WHERE id=1").fetchone()
            self.assertEqual(tuple(row), ("disputed", "phishing", 0))
            self.assertEqual(conn.execute("SELECT COUNT(*) FROM feedback_events WHERE prediction_id=1").fetchone()[0], 1)
            self.assertEqual(conn.execute("SELECT action FROM label_audit ORDER BY id DESC LIMIT 1").fetchone()[0], "conflicting_feedback")

    def test_feedback_agreeing_with_verified_label_does_not_downgrade_it(self):
        review_label(1, "verify", "analyst", "trusted source", "phishing", db_path=self.db)
        self.assertTrue(record_feedback(1, "phishing", self.db))
        with connect(self.db) as conn:
            self.assertEqual(tuple(conn.execute("SELECT verification_status,training_eligible FROM predictions WHERE id=1").fetchone()), ("verified", 1))

    def test_verification_requires_label_and_provenance(self):
        with self.assertRaisesRegex(ValueError, "requires phishing"):
            review_label(1, "verify", "reviewer", "source", db_path=self.db)
        with self.assertRaisesRegex(ValueError, "reviewer and provenance"):
            review_label(1, "verify", "", "source", "phishing", db_path=self.db)

    def test_batch_export_is_manifested_and_idempotent(self):
        for i, label in enumerate(("phishing", "legitimate"), 1):
            review_label(i, "verify", "reviewer", "independent source", label, db_path=self.db)
        output = self.root / "batches"
        from mlops.data.lifecycle import preview_batch
        preview = preview_batch(self.db)
        self.assertTrue(preview["dry_run"])
        self.assertEqual(preview["record_count"], 2)
        with connect(self.db) as conn:
            self.assertEqual(conn.execute("SELECT COUNT(*) FROM training_batches").fetchone()[0], 0)
        first = export_batch(self.db, output)
        self.assertTrue(first["created"])
        manifest = json.loads(Path(first["directory"], "manifest.json").read_text(encoding="utf-8"))
        self.assertEqual(manifest["record_count"], 2)
        self.assertEqual(manifest["label_distribution"], {"phishing": 1, "legitimate": 1})
        self.assertEqual(export_batch(self.db, output)["reason"], "no_new_verified_records")
        self.assertEqual(Path(first["directory"], "train.tsv").read_text(encoding="utf-8").count("\n"), 2)

    def test_batch_export_rejects_duplicate_urls(self):
        with connect(self.db) as conn:
            conn.execute("UPDATE predictions SET url='https://duplicate.example/' WHERE id IN (1,2)")
        review_label(1, "verify", "reviewer", "source", "phishing", db_path=self.db)
        review_label(2, "verify", "reviewer", "source", "legitimate", db_path=self.db)
        with self.assertRaisesRegex(ValueError, "duplicate URLs"):
            export_batch(self.db, self.root / "batches")

    def test_batch_export_rejects_invalid_url(self):
        with connect(self.db) as conn:
            conn.execute("UPDATE predictions SET url='not-a-url' WHERE id=1")
        review_label(1, "verify", "reviewer", "source", "phishing", db_path=self.db)
        with self.assertRaisesRegex(ValueError, "invalid label, provenance, or URL"):
            export_batch(self.db, self.root / "batches")

    def test_threshold_and_class_counts_gate_batch_trigger(self):
        review_label(1, "verify", "reviewer", "source", "phishing", db_path=self.db)
        review_label(2, "verify", "reviewer", "source", "legitimate", db_path=self.db)
        with connect(self.db) as conn:
            conn.execute("INSERT INTO retraining_state(key,value) VALUES('last_success_at',?)",
                         ((datetime.now(timezone.utc) - timedelta(days=1)).isoformat(),))
        result = check_eligibility(self.db, min_records=2, min_per_class=1, review_days=365, cooldown_days=0)
        self.assertTrue(result["eligible"])
        self.assertFalse(result["dry_run"] is False)
        blocked = check_eligibility(self.db, min_records=2, min_per_class=2, review_days=365, cooldown_days=0)
        self.assertFalse(blocked["eligible"])
        self.assertIn("minimum_per_class_not_met", blocked["reasons"])

    def test_time_review_noops_without_new_data_and_never_trains(self):
        before = hashlib.sha256(self.db.read_bytes()).hexdigest()
        result = check_eligibility(self.db, review_days=0, cooldown_days=0)
        self.assertFalse(result["eligible"])
        self.assertEqual(result["reasons"], ["no_new_verified_data"])
        self.assertTrue(result["dry_run"])
        self.assertEqual(hashlib.sha256(self.db.read_bytes()).hexdigest(), before)
        review = scheduled_review(self.db, review_days=0, cooldown_days=0)
        self.assertFalse(review["eligible"])
        self.assertFalse(review["training_started"])

    def test_time_trigger_still_requires_minimum_for_each_class(self):
        review_label(1, "verify", "reviewer", "source", "phishing", db_path=self.db)
        result = check_eligibility(self.db, min_records=1000, min_per_class=1, review_days=0, cooldown_days=0)
        self.assertFalse(result["eligible"])
        self.assertIn("minimum_per_class_not_met", result["reasons"])

    def test_time_trigger_persists_single_manual_job_and_prevents_concurrent_job(self):
        for i, label in ((1, "phishing"), (2, "legitimate")):
            review_label(i, "verify", "reviewer", "source", label, db_path=self.db)
        first = scheduled_review(self.db, min_records=100, min_per_class=1, review_days=0, cooldown_days=0)
        self.assertEqual(first["decision"], "eligible_manual_worker_required")
        self.assertFalse(first["training_started"])
        second = scheduled_review(self.db, min_records=100, min_per_class=1, review_days=0, cooldown_days=0)
        self.assertFalse(second["eligible"])
        self.assertIn("active_job_exists", second["reasons"])

    def test_atomic_job_claim_failure_and_retry(self):
        for i, label in ((1, "phishing"), (2, "legitimate")):
            review_label(i, "verify", "reviewer", "source", label, db_path=self.db)
        job = scheduled_review(self.db, min_records=100, min_per_class=1, review_days=0, cooldown_days=0)
        job_id = job["job_id"]
        with ThreadPoolExecutor(max_workers=2) as executor:
            claims = list(executor.map(lambda _: claim_job(job_id, self.db), range(2)))
        self.assertEqual(sum(claims), 1)
        finish_job(job_id, False, self.db, error="fixture failure")
        self.assertTrue(retry_job(job_id, self.db))
        self.assertTrue(claim_job(job_id, self.db))
        finish_job(job_id, True, self.db, snapshot_id="fingerprint-fixture")
        self.assertFalse(retry_job(job_id, self.db))
        with connect(self.db) as conn:
            states = dict(conn.execute("SELECT key,value FROM retraining_state"))
        self.assertEqual(states["last_snapshot_at"], "fingerprint-fixture")


if __name__ == "__main__":
    unittest.main()
