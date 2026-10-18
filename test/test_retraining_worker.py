import json
import sqlite3
import tempfile
import unittest
from pathlib import Path

from mlops.data.lifecycle import initialize_schema
from mlops.retraining.worker import run_worker, verify_batch


class RetrainingWorkerTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.db = self.root / "db.sqlite"
        self.connections = []
        conn = sqlite3.connect(self.db)
        self.connections.append(conn)
        with conn:
            conn.execute("CREATE TABLE predictions(id INTEGER PRIMARY KEY, timestamp TEXT NOT NULL, url TEXT NOT NULL, prediction TEXT NOT NULL, confidence REAL NOT NULL, model_version TEXT NOT NULL, feedback TEXT, feedback_timestamp TEXT)")
        initialize_schema(self.db)

    def tearDown(self):
        for connection in self.connections:
            connection.close()

    def test_dry_run_never_claims_or_mutates_job(self):
        conn = sqlite3.connect(self.db)
        self.connections.append(conn)
        with conn:
            conn.execute("INSERT INTO retraining_jobs VALUES('job','eligible','test','now','now',NULL,NULL)")
        dvc_calls = []
        result = run_worker("job", db_path=self.db, batch_root=self.root / "batches",
            dataset=self.root / "dataset", validation_report=self.root / "missing.json",
            dvc_check=lambda root: dvc_calls.append(root) or {"ok": False, "error": "synthetic"})
        self.assertTrue(result["dry_run"])
        self.assertFalse(result["training_started"])
        self.assertIn("dataset_source_provenance_manifest_missing", result["blockers"])
        self.assertEqual(dvc_calls, [Path(__file__).resolve().parents[1]])
        conn = sqlite3.connect(self.db)
        self.connections.append(conn)
        with conn:
            self.assertEqual(conn.execute("SELECT status FROM retraining_jobs WHERE job_id='job'").fetchone()[0], "eligible")

    def test_batch_manifest_checksum_tampering_is_detected(self):
        # Schema row captures the expected content hash; a changed file must fail closed.
        import hashlib
        batch_root = self.root / "batches"
        directory = batch_root / "batch-x"
        directory.mkdir(parents=True)
        data = b"phishing\thttps://bad.invalid/\n"
        digest = hashlib.sha256(data).hexdigest()
        (directory / "train.tsv").write_bytes(data + b"legitimate\thttps://good.invalid/\n")
        (directory / "manifest.json").write_text(json.dumps({"batch_id":"batch-x", "sha256":digest,
            "verification_status":"verified", "record_count":1, "source_prediction_ids":[],
            "label_distribution":{"phishing":1}}), encoding="utf-8")
        conn = sqlite3.connect(self.db)
        self.connections.append(conn)
        with conn:
            conn.execute("INSERT INTO training_batches VALUES(?,?,?,?,?,?,?,?,?,?)",
                ("batch-x", "now", 1, 1, 0, digest, str(directory/"manifest.json"), 1, 1, 1))
        batch, errors = verify_batch("batch-x", self.db, batch_root)
        self.assertIsNotNone(batch)
        self.assertIn("batch_checksum_mismatch", errors)


if __name__ == "__main__":
    unittest.main()
