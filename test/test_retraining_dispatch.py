import sqlite3
import tempfile
import unittest
import json
import os
import threading
from pathlib import Path
from unittest.mock import patch

from mlops.data import lifecycle
from mlops.retraining import worker


class RetrainingDispatchTests(unittest.TestCase):
    def setUp(self):
        # Exercise dispatch only in these isolated synthetic database fixtures.
        self.dispatch_env = patch.dict(os.environ, {"RETRAIN_AUTO_ENABLED": "true"})
        self.dispatch_env.start()
        self.addCleanup(self.dispatch_env.stop)
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.db = self.root / "db.sqlite"
        with sqlite3.connect(self.db) as conn:
            conn.execute("CREATE TABLE predictions(id INTEGER PRIMARY KEY, timestamp TEXT NOT NULL, url TEXT NOT NULL, prediction TEXT NOT NULL, confidence REAL NOT NULL, model_version TEXT NOT NULL, feedback TEXT, feedback_timestamp TEXT)")
            conn.executemany("INSERT INTO predictions(timestamp,url,prediction,confidence,model_version) VALUES(?,?,?,?,?)", [
                ("2026-01-01T00:00:00+00:00", f"https://fixture{i}.example/", "legitimate", 90, "fixture") for i in range(2)])
        lifecycle.initialize_schema(self.db)
        lifecycle.review_label(1, "verify", "reviewer", "fixture provenance", "phishing", db_path=self.db)
        lifecycle.review_label(2, "verify", "reviewer", "fixture provenance", "legitimate", db_path=self.db)

    def test_default_configuration_disables_scheduler_and_worker_dispatch(self):
        batch_root = self.root / "batches-default-disabled"
        with patch.dict(os.environ, {}, clear=True), \
             patch.object(worker, "run_worker") as run_worker:
            scheduled = lifecycle.scheduled_review(self.db, min_records=2, min_per_class=1,
                interval_hours=10000, cooldown_days=0)
            dispatched = worker.dispatch_scheduled_job(db_path=self.db, batch_root=batch_root,
                min_records=2, min_per_class=1, interval_hours=10000, cooldown_days=0)
        self.assertEqual(scheduled["decision"], "disabled")
        self.assertFalse(scheduled["training_started"])
        self.assertEqual(dispatched["decision"], "disabled")
        self.assertFalse(dispatched["training_started"])
        self.assertFalse(batch_root.exists())
        run_worker.assert_not_called()
        with lifecycle.connect(self.db) as conn:
            self.assertEqual(conn.execute("SELECT COUNT(*) FROM retraining_jobs").fetchone()[0], 0)


    def test_dispatch_calls_existing_worker_and_links_batch(self):
        with patch.object(worker, "run_worker", return_value={"decision": "FAILED"}) as run:
            result = worker.dispatch_scheduled_job(db_path=self.db, batch_root=self.root / "batches",
                min_records=2, min_per_class=1, interval_hours=10000, cooldown_days=0)
        self.assertEqual(result["worker"]["decision"], "FAILED")
        args, kwargs = run.call_args
        self.assertEqual(kwargs["execute"], True)
        with lifecycle.connect(self.db) as conn:
            row = conn.execute("SELECT status,batch_id FROM retraining_jobs WHERE job_id=?", (args[0],)).fetchone()
        self.assertEqual(row["status"], "eligible")  # mock owns no lifecycle transitions
        self.assertTrue(row["batch_id"].startswith("batch-"))

    def test_repeat_dispatch_does_not_duplicate_batch_or_job(self):
        with patch.object(worker, "run_worker", return_value={"decision": "COMPLETED_CANDIDATE"}):
            first = worker.dispatch_scheduled_job(db_path=self.db, batch_root=self.root / "batches",
                min_records=2, min_per_class=1, interval_hours=10000, cooldown_days=0)
            second = worker.dispatch_scheduled_job(db_path=self.db, batch_root=self.root / "batches",
                min_records=2, min_per_class=1, interval_hours=10000, cooldown_days=0)
        self.assertIn("job_id", first)
        self.assertNotIn("job_id", second)
        with lifecycle.connect(self.db) as conn:
            self.assertEqual(conn.execute("SELECT COUNT(*) FROM retraining_jobs").fetchone()[0], 1)
            self.assertEqual(conn.execute("SELECT COUNT(*) FROM training_batches").fetchone()[0], 1)

    def test_interrupted_job_can_be_explicitly_recovered(self):
        result = lifecycle.scheduled_review(self.db, min_records=2, min_per_class=1,
            interval_hours=10000, cooldown_days=0)
        self.assertTrue(lifecycle.claim_job(result["job_id"], self.db))
        self.assertTrue(lifecycle.recover_interrupted_job(result["job_id"], self.db))
        self.assertFalse(lifecycle.claim_job("missing", self.db))

    def test_evaluation_and_approval_states_keep_global_job_lock(self):
        job = lifecycle.scheduled_review(self.db, min_records=2, min_per_class=1,
            interval_hours=10000, cooldown_days=0)
        lifecycle.update_job(job["job_id"], "evaluating", db_path=self.db)
        with sqlite3.connect(self.db) as conn:
            with self.assertRaises(sqlite3.IntegrityError):
                conn.execute("INSERT INTO retraining_jobs(job_id,status,reason,created_at,updated_at) VALUES('second','eligible','test','now','now')")
        decision = lifecycle.check_eligibility(self.db, min_records=2, min_per_class=1,
            interval_hours=10000, cooldown_days=0)
        self.assertFalse(decision["eligible"])
        self.assertEqual(decision["active_job"]["job_id"], job["job_id"])

    def test_missing_provenance_blocks_and_persists_job(self):
        result = worker.dispatch_scheduled_job(db_path=self.db, batch_root=self.root / "batches",
            dataset=self.root / "no-dataset", validation_report=self.root / "no-report",
            dvc_check=lambda _: {"ok": False}, min_records=2, min_per_class=1,
            interval_hours=10000, cooldown_days=0)
        self.assertEqual(result["worker"]["decision"], "BLOCKED")
        with lifecycle.connect(self.db) as conn:
            row = conn.execute("SELECT status,error FROM retraining_jobs WHERE job_id=?", (result["job_id"],)).fetchone()
        self.assertEqual(row["status"], "blocked")
        self.assertIn("dataset_source_provenance_manifest_missing", row["error"])

    def test_missing_evaluation_inputs_block_after_isolated_candidate(self):
        with patch.dict(os.environ, {"RETRAIN_AUTO_ENABLED": "true"}), \
             patch.object(worker, "run_worker", return_value={"decision": "COMPLETED_CANDIDATE",
                 "candidate_dir": str(self.root / "candidate"), "candidate_bundle": {"model_version": "candidate-v1"},
                 "batch": {"batch_id": "fixture-batch", "fingerprint": "fixture-hash"}}):
            result = worker.dispatch_scheduled_job(db_path=self.db, batch_root=self.root / "batches",
                min_records=2, min_per_class=1, interval_hours=10000, cooldown_days=0,
                provenance_manifest=self.root / "source.json")
        with lifecycle.connect(self.db) as conn:
            row = conn.execute("SELECT status,error,details FROM retraining_jobs WHERE job_id=?", (result["job_id"],)).fetchone()
        self.assertEqual(row["status"], "blocked")
        self.assertIn("evaluation_inputs_missing", row["error"])
        self.assertEqual(json.loads(row["details"])["candidate_version"], "candidate-v1")

    def test_rejected_candidate_persists_evaluation_and_never_promotes(self):
        evaluation = {"evaluation_decision": "REJECT", "evaluation_reasons": ["recall_below_policy"],
            "evaluation_report_path": str(self.root / "report.json"), "evaluation_report": {"decision": "REJECT"},
            "policy_path": str(self.root / "policy.json"), "registry_root": str(self.root / "registry")}
        completed = {"decision": "COMPLETED_CANDIDATE", "candidate_dir": str(self.root / "candidate"),
            "candidate_bundle": {"model_version": "candidate-v1"}, "batch": {"batch_id": "batch-a", "fingerprint": "fp"}}
        with patch.dict(os.environ, {"RETRAIN_AUTO_ENABLED": "true"}), \
             patch.object(worker, "run_worker", return_value=completed), \
             patch.object(worker, "evaluate_job_candidate", return_value=evaluation), \
             patch.object(worker, "promote_job_candidate") as promote:
            result = worker.dispatch_scheduled_job(db_path=self.db, batch_root=self.root / "batches",
                min_records=2, min_per_class=1, interval_hours=10000, cooldown_days=0,
                provenance_manifest=self.root / "src.json", evaluation_tsv=self.root / "eval.tsv",
                evaluation_manifest=self.root / "manifest.json", policy_path=self.root / "policy.json",
                promotion_provenance=self.root / "promotion.json")
        self.assertEqual(result["decision"], "REJECT")
        promote.assert_not_called()
        with lifecycle.connect(self.db) as conn:
            self.assertEqual(conn.execute("SELECT status FROM retraining_jobs WHERE job_id=?", (result["job_id"],)).fetchone()[0], "rejected")

    def test_policy_pass_waits_for_explicit_approval_then_promotes(self):
        evaluation = {"evaluation_decision": "PASS", "evaluation_reasons": [],
            "evaluation_report_path": str(self.root / "report.json"), "evaluation_report": {"decision": "PASS"},
            "policy_path": str(self.root / "policy.json"), "registry_root": str(self.root / "registry")}
        completed = {"decision": "COMPLETED_CANDIDATE", "candidate_dir": str(self.root / "candidate"),
            "candidate_bundle": {"model_version": "candidate-v1"}, "batch": {"batch_id": "batch-a", "fingerprint": "fp"}}
        inputs = dict(db_path=self.db, batch_root=self.root / "batches", min_records=2, min_per_class=1,
            interval_hours=10000, cooldown_days=0, provenance_manifest=self.root / "src.json",
            evaluation_tsv=self.root / "eval.tsv", evaluation_manifest=self.root / "manifest.json",
            policy_path=self.root / "policy.json", promotion_provenance=self.root / "promotion.json")
        inputs["evaluation_tsv"] = self.root / "eval.tsv"
        with patch.dict(os.environ, {"RETRAIN_AUTO_ENABLED": "true"}), \
             patch.object(worker, "run_worker", return_value=completed), \
             patch.object(worker, "evaluate_job_candidate", return_value=evaluation), \
             patch.object(worker, "promote_job_candidate", return_value={"decision": "PROMOTED", "restart_required": True, "hot_reload": False}) as promote:
            result = worker.dispatch_scheduled_job(**inputs)
            self.assertEqual(result["decision"], "AWAITING_APPROVAL")
            promote.assert_not_called()
            with lifecycle.connect(self.db) as conn:
                self.assertEqual(conn.execute("SELECT status FROM retraining_jobs WHERE job_id=?", (result["job_id"],)).fetchone()[0], "awaiting_approval")
            approved = worker.approve_job(result["job_id"], db_path=self.db, confirm=True)
        self.assertEqual(approved["decision"], "PROMOTED")
        self.assertTrue(approved["restart_required"])
        with lifecycle.connect(self.db) as conn:
            self.assertEqual(conn.execute("SELECT status FROM retraining_jobs WHERE job_id=?", (result["job_id"],)).fetchone()[0], "promoted")

    def test_concurrent_approval_has_one_promoter(self):
        job = lifecycle.scheduled_review(self.db, min_records=2, min_per_class=1,
            interval_hours=10000, cooldown_days=0)
        lifecycle.update_job(job["job_id"], "awaiting_approval", details={
            "candidate_dir": str(self.root / "candidate"),
            "evaluation_report_path": str(self.root / "report.json"),
            "policy_path": str(self.root / "policy.json"),
            "promotion_provenance": str(self.root / "provenance.json"),
        }, db_path=self.db)
        entered, release = threading.Event(), threading.Event()
        promoted = []

        def fake_promote(*args, **kwargs):
            entered.set()
            self.assertTrue(release.wait(5))
            return {"decision": "PROMOTED"}

        with patch.object(worker, "promote_job_candidate", side_effect=fake_promote) as promote:
            thread = threading.Thread(target=lambda: promoted.append(
                worker.approve_job(job["job_id"], db_path=self.db, confirm=True)))
            thread.start()
            self.assertTrue(entered.wait(5))
            concurrent = worker.approve_job(job["job_id"], db_path=self.db, confirm=True)
            release.set()
            thread.join(5)
            self.assertFalse(thread.is_alive())
        self.assertEqual(concurrent["decision"], "BLOCKED")
        self.assertEqual(len(promoted), 1)
        promote.assert_called_once()
        with lifecycle.connect(self.db) as conn:
            state = conn.execute("SELECT status FROM retraining_jobs WHERE job_id=?", (job["job_id"],)).fetchone()[0]
        self.assertEqual(state, "promoted")

    def test_interrupted_promotion_is_requeued_only_when_registry_still_incumbent(self):
        registry = self.root / "registry"
        registry.mkdir()
        (registry / "active.json").write_text(json.dumps({"model_version": "incumbent-v1"}), encoding="utf-8")
        (registry / "registry.json").write_text(json.dumps({"versions": {
            "incumbent-v1": {"status": "production"}}}), encoding="utf-8")
        job = lifecycle.scheduled_review(self.db, min_records=2, min_per_class=1,
            interval_hours=10000, cooldown_days=0)
        lifecycle.update_job(job["job_id"], "promoting", details={
            "candidate_version": "candidate-v1", "registry_root": str(registry),
            "evaluation_report": {"incumbent_id": "incumbent-v1"},
        }, db_path=self.db)

        result = worker.recover_interrupted_approval(job["job_id"], db_path=self.db)

        self.assertEqual(result["decision"], "RECOVERED_AWAITING_APPROVAL")
        with lifecycle.connect(self.db) as conn:
            state = conn.execute("SELECT status FROM retraining_jobs WHERE job_id=?", (job["job_id"],)).fetchone()[0]
        self.assertEqual(state, "awaiting_approval")
        self.assertEqual(json.loads((registry / "active.json").read_text(encoding="utf-8"))["model_version"], "incumbent-v1")


if __name__ == "__main__":
    unittest.main()
