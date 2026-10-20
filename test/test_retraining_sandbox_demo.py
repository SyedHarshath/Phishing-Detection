import json
import sqlite3
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from mlops.data import lifecycle
from scripts.demo_retraining_sandbox import (
    ROOT_MARKER, RUN_MANIFEST, RUN_MARKER, _assert_root_is_safe,
    _prepare_root, _remove_owned_runs, build_demo,
)
from mlops.evaluation.compare import dataset_fingerprint


class RetrainingSandboxDemoTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.parent = Path(self.temp.name)
        self.root = self.parent / "optimus-retraining-demo-sandbox"
        self.project = self.parent / "project"
        self.project.mkdir()

    def test_refuses_unowned_or_protected_paths(self):
        outside = self.parent / "not-the-sandbox"
        with self.assertRaisesRegex(RuntimeError, "dedicated"):
            _assert_root_is_safe(outside, self.parent, self.project)
        unowned = self.parent / "optimus-retraining-demo-sandbox"
        unowned.mkdir()
        (unowned / "keep.txt").write_text("unrelated", encoding="utf-8")
        with self.assertRaisesRegex(RuntimeError, "unowned"):
            _prepare_root(unowned, self.parent, self.project, reset=True)
        self.assertEqual((unowned / "keep.txt").read_text(encoding="utf-8"), "unrelated")

    def test_reset_removes_only_manifested_outputs(self):
        self.root.mkdir()
        (self.root / ROOT_MARKER).write_text("OPTIMUS dedicated sandbox v1\n", encoding="utf-8")
        run = self.root / "runs" / "run-fixture"
        run.mkdir(parents=True)
        (run / RUN_MARKER).write_text("OPTIMUS generated sandbox output v1\n", encoding="utf-8")
        (run / "owned.json").write_text("{}", encoding="utf-8")
        (run / "unrelated.txt").write_text("preserve", encoding="utf-8")
        (run / RUN_MANIFEST).write_text(json.dumps({"version": 1, "files": ["owned.json"]}), encoding="utf-8")
        _remove_owned_runs(self.root)
        self.assertFalse((run / "owned.json").exists())
        self.assertTrue((run / "unrelated.txt").is_file())

    def test_repeatable_synthetic_lifecycle_uses_only_temporary_paths(self):
        _prepare_root(self.root, self.parent, self.project, reset=True)
        first_dir = self.root / "runs" / "run-first"
        first_dir.mkdir(parents=True)
        (first_dir / RUN_MARKER).write_text("OPTIMUS generated sandbox output v1\n", encoding="utf-8")
        first = build_demo(first_dir, project_root=self.project, record_count=8, worker_preflight=False)
        self.assertEqual(first["counts"]["feedback"], 8)
        self.assertEqual(first["counts"]["training_eligible"], 8)
        self.assertEqual(first["batch"]["record_count"], 8)
        self.assertEqual(first["evaluation_fixture"]["url_overlap_with_synthetic_feedback"], 0)
        self.assertEqual(first["evaluation_fixture"]["host_overlap_with_synthetic_feedback"], 0)
        manifest = json.loads((first_dir / first["evaluation_fixture"]["manifest"]).read_text(encoding="utf-8"))
        evaluation_tsv = first_dir / "evaluation-fixture" / "evaluation.tsv"
        records = [(0 if label == "phishing" else 1, url)
                   for label, url in (line.rstrip("\n").split("\t", 1)
                                      for line in evaluation_tsv.read_text(encoding="utf-8").splitlines())]
        self.assertEqual(manifest["dataset_fingerprint"], dataset_fingerprint(
            [label for label, _ in records], [url for _, url in records]))
        self.assertFalse(manifest["trusted_ground_truth"])
        self.assertFalse(manifest["incumbent_training_history_verified"])
        self.assertTrue(Path(first["database"]).is_relative_to(self.root))
        first_batch_hash = first["batch"]["sha256"]

        second_dir = self.root / "runs" / "run-second"
        second_dir.mkdir()
        (second_dir / RUN_MARKER).write_text("OPTIMUS generated sandbox output v1\n", encoding="utf-8")
        second = build_demo(second_dir, project_root=self.project, record_count=8, worker_preflight=False)
        self.assertEqual(second["batch"]["sha256"], first_batch_hash)
        self.assertEqual(second["counts"]["training_eligible"], 8)

    def test_worker_candidate_output_can_be_confined_to_sandbox(self):
        from mlops.retraining import worker

        db = self.parent / "worker.sqlite"
        with sqlite3.connect(db) as conn:
            conn.execute("CREATE TABLE predictions(id INTEGER PRIMARY KEY, timestamp TEXT NOT NULL, url TEXT NOT NULL, prediction TEXT NOT NULL, confidence REAL NOT NULL, model_version TEXT NOT NULL, feedback TEXT, feedback_timestamp TEXT)")
        lifecycle.initialize_schema(db)
        with lifecycle.connect(db) as conn:
            conn.execute("INSERT INTO retraining_jobs(job_id,status,reason,created_at,updated_at,batch_id,error) VALUES('sandbox-job','eligible','test','now','now','sandbox-batch',NULL)")

        dataset = self.parent / "dataset"
        dataset.mkdir()
        (dataset / "train.txt").write_text(
            "phishing\thttps://base-phish.synthetic.invalid/1\n"
            "legitimate\thttps://base-legit.synthetic.invalid/1\n", encoding="utf-8")
        (dataset / "val.txt").write_text(
            "phishing\thttps://val-phish.synthetic.invalid/1\n"
            "legitimate\thttps://val-legit.synthetic.invalid/1\n", encoding="utf-8")
        batch = {"batch_id": "sandbox-batch", "fingerprint": "fixture-fingerprint",
                 "records": [("phishing", "https://batch-phish.synthetic.invalid/1"),
                             ("legitimate", "https://batch-legit.synthetic.invalid/1")]}
        baseline = {"split_sha256": {"train": "train-fingerprint", "val": "val-fingerprint"},
                    "source_provenance_verified": True, "authoritative_registration": True,
                    "source_registration_sha256": "a" * 64}
        candidate_root = self.root / "candidate-artifacts"

        def mocked_trainer(train_split, val_split, output_dir, *, metadata):
            output_dir.mkdir(parents=True, exist_ok=False)
            return {"candidate_id": output_dir.name,
                    "training": {"train_count": len(train_split.urls),
                                 "validation_count": len(val_split.urls)}}

        with patch.object(worker, "verify_batch", return_value=(batch, [])), \
             patch.object(worker, "verify_baseline", return_value=(baseline, [])), \
             patch.object(worker, "log_mlflow_best_effort", return_value={"status": "unavailable", "reason": "isolated test"}):
            result = worker.run_worker("sandbox-job", execute=True, db_path=db,
                batch_root=self.parent / "batches", dataset=dataset,
                validation_report=self.parent / "validation.json",
                provenance_manifest=self.parent / "provenance.json",
                candidate_root=candidate_root,
                dvc_check=lambda _: {"ok": True, "stdout": "", "stderr": ""},
                trainer=mocked_trainer)
        self.assertEqual(result["decision"], "COMPLETED_CANDIDATE")
        candidate_dir = Path(result["candidate_dir"]).resolve()
        self.assertIn(candidate_root.resolve(), candidate_dir.parents)
        with lifecycle.connect(db) as conn:
            status = conn.execute("SELECT status FROM retraining_jobs WHERE job_id='sandbox-job'").fetchone()[0]
        self.assertEqual(status, "training_completed")


if __name__ == "__main__":
    unittest.main()
