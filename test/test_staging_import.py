import hashlib
import json
import tempfile
import unittest
from pathlib import Path
from zipfile import ZIP_DEFLATED, ZipFile

from mlops.dvc.staging_import import approve_authoritative, import_dataset


class StagingImportTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(); self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.source = self.root / "source"; self.source.mkdir()
        self.rows = {"train.txt": b"phishing\thttps://bad.example/a\r\nlegitimate\thttps://good.example/b\r\n",
                     "val.txt": b"phishing\thttps://bad.example/c\n",
                     "test.txt": b"legitimate\thttps://good.example/d\n"}
        for name, content in self.rows.items(): (self.source / name).write_bytes(content)
        self.manifest_path = self.root / "provenance.json"
        manifest = {"source_name": "fixture", "source_reference": "fixture://reviewed",
            "version": "v1", "acquired_at_utc": "2026-01-01T00:00:00Z",
            "acquisition_method": "operator-supplied local files",
            "source_attestation_reference": "review-ticket-1",
            "record_format": "UTF-8 label<TAB>URL, one record per line",
            "labels": {"phishing": 0, "legitimate": 1},
            "files": {name: hashlib.sha256(content).hexdigest() for name, content in self.rows.items()},
            "splits": {"train": "train.txt", "val": "val.txt", "test": "test.txt"}}
        self.manifest_path.write_text(json.dumps(manifest), encoding="utf-8")
        self.stage_root = self.root / "staging"

    def test_import_is_separate_reproducible_and_requires_operator_approval(self):
        original_hashes = {name: hashlib.sha256((self.source / name).read_bytes()).hexdigest() for name in self.rows}
        first = import_dataset(self.source, self.manifest_path, staging_root=self.stage_root)
        second = import_dataset(self.source, self.manifest_path, staging_root=self.stage_root)
        self.assertEqual(first["status"], "staged")
        self.assertFalse(first["registration"]["approved"])
        self.assertEqual(first["normalized_sha256"], second["normalized_sha256"])
        self.assertFalse(first["source"]["source_origin_cryptographically_verified"])
        self.assertEqual({name: hashlib.sha256((self.source / name).read_bytes()).hexdigest() for name in self.rows}, original_hashes)
        stage = self.stage_root / first["stage_id"]
        registration = approve_authoritative(stage, operator="reviewer", approval_reference="ticket-2")
        self.assertTrue(registration["authoritative_for_training"])
        self.assertEqual(registration["split_sha256"], first["normalized_sha256"])

    def test_checksum_mismatch_leaves_incomplete_report_and_no_approval(self):
        manifest = json.loads(self.manifest_path.read_text())
        manifest["files"]["train.txt"] = "0" * 64
        self.manifest_path.write_text(json.dumps(manifest), encoding="utf-8")
        with self.assertRaisesRegex(ValueError, "checksum mismatch"):
            import_dataset(self.source, self.manifest_path, staging_root=self.stage_root)
        self.assertTrue(list(self.stage_root.glob("*/IMPORT_INCOMPLETE.json")))

    def test_untrusted_or_missing_manifest_fields_are_rejected(self):
        self.manifest_path.write_text(json.dumps({"source_name": "missing"}), encoding="utf-8")
        with self.assertRaisesRegex(ValueError, "missing fields"):
            import_dataset(self.source, self.manifest_path, staging_root=self.stage_root)

    def test_archive_import_requires_and_checks_container_checksum(self):
        archive = self.root / "data.zip"
        with ZipFile(archive, "w", compression=ZIP_DEFLATED) as zf:
            for name in self.rows: zf.write(self.source / name, arcname=name)
        manifest = json.loads(self.manifest_path.read_text(encoding="utf-8"))
        manifest["source_container_sha256"] = hashlib.sha256(archive.read_bytes()).hexdigest()
        self.manifest_path.write_text(json.dumps(manifest), encoding="utf-8")
        result = import_dataset(archive, self.manifest_path, staging_root=self.stage_root)
        self.assertEqual(result["source"]["source_container_sha256"], manifest["source_container_sha256"])


if __name__ == "__main__": unittest.main()
