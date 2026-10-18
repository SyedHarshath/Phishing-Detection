import hashlib
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from mlops.dvc.inventory import create_inventory


def _md5(payload):
    return hashlib.md5(payload).hexdigest()


class DatasetInventoryTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.dataset = self.root / "dataset" / "big_dataset"
        self.dataset.mkdir(parents=True)
        (self.dataset / "train.txt").write_text("phishing\thttps://a.example/x\nlegitimate\thttps://b.example/\n", encoding="utf-8")
        (self.dataset / "val.txt").write_text("phishing\thttps://c.example/\n", encoding="utf-8")
        (self.dataset / "test.txt").write_text("legitimate\thttps://d.example/\n", encoding="utf-8")
        self.output = self.root / "reports"

    def _pointer(self):
        cache = self.root / ".dvc" / "cache" / "files" / "md5"
        entries = []
        for name in ("train.txt", "val.txt", "test.txt"):
            content = (self.dataset / name).read_bytes()
            digest = _md5(content)
            blob = cache / digest[:2] / digest[2:]
            blob.parent.mkdir(parents=True, exist_ok=True)
            blob.write_bytes(content)
            entries.append({"md5": digest, "relpath": name})
        directory_blob = json.dumps(entries, separators=(",", ":")).encode()
        dir_md5 = _md5(directory_blob) + ".dir"
        object_path = cache / dir_md5[:2] / dir_md5[2:]
        object_path.parent.mkdir(parents=True, exist_ok=True)
        object_path.write_bytes(directory_blob)
        pointer = self.dataset.parent / "big_dataset.dvc"
        pointer.write_text(json.dumps({"outs": [{"md5": dir_md5, "size": sum((self.dataset / n).stat().st_size for n in ("train.txt", "val.txt", "test.txt")), "nfiles": 3, "path": "dataset"}]}))
        return pointer, self.root / ".dvc" / "cache"

    def test_fast_inventory_is_stable_and_complete(self):
        first = create_inventory(dataset=self.dataset, output_dir=self.output, progress_interval=1000)
        second = create_inventory(dataset=self.dataset, output_dir=self.output, progress_interval=1000)
        self.assertEqual(first["status"], "completed")
        self.assertEqual(first["inventory_fingerprint"], second["inventory_fingerprint"])
        report = json.loads(Path(first["json_report"]).read_text())
        train = next(item for item in report["files"] if item["path"] == "train.txt")
        self.assertEqual(train["label_counts"]["phishing"], 1)
        self.assertEqual(report["registrable_domain_overlap"]["status"], "unavailable")
        self.assertTrue(Path(first["markdown_report"]).is_file())
        self.assertFalse(Path(first["json_report"]).with_suffix(".partial.json").exists())

    def test_local_file_changes_and_dvc_cache_snapshot_are_distinguished(self):
        pointer, cache_root = self._pointer()
        local = create_inventory(dataset=self.dataset, output_dir=self.output,
            pointer=pointer, progress_interval=1000)
        self.assertEqual(local["report"]["provenance_status"], "pointer_workspace_match")
        # Alter a workspace file after pointer creation, retaining the pointer/cache.
        (self.dataset / "val.txt").write_text("phishing\thttps://changed.invalid/\n", encoding="utf-8")
        (self.dataset / "extra.txt").write_text("local note\n", encoding="utf-8")
        local_changed = create_inventory(dataset=self.dataset, output_dir=self.output,
            pointer=pointer, progress_interval=1000)
        self.assertEqual(local_changed["report"]["provenance_status"], "pointer_mismatch")
        self.assertIn("val.txt", local_changed["report"]["pointer_comparison"]["files_differing_from_pointer_md5"])
        self.assertIn("extra.txt", local_changed["report"]["pointer_comparison"]["untracked_local_files"])
        cached = create_inventory(dataset=self.dataset, output_dir=self.output,
            pointer=pointer, cache_root=cache_root, from_dvc_cache=True, progress_interval=1000)
        self.assertEqual(cached["report"]["provenance_status"], "pointer_cache_match")
        self.assertEqual(cached["report"]["pointer_comparison"]["files_differing_from_pointer_md5"], [])

    def test_full_scan_reports_duplicates_and_conflicting_split_labels(self):
        (self.dataset / "val.txt").write_text("phishing\thttps://a.example/x\n", encoding="utf-8")
        result = create_inventory(dataset=self.dataset, output_dir=self.output,
                                  mode="full", progress_interval=1000)
        full = result["report"]["full_validation"]
        self.assertTrue(full["completed"])
        self.assertFalse(full["passed"])
        self.assertGreater(full["cross_split"].get("exact_url_overlap", 0), 0)

    def test_fast_mode_bounds_pathological_line_memory_and_reports_nuls(self):
        (self.dataset / "train.txt").write_bytes(b"\x00" * (2 * 1024 * 1024) + b"\n")
        result = create_inventory(dataset=self.dataset, output_dir=self.output)
        train = next(item for item in result["report"]["files"] if item["path"] == "train.txt")
        self.assertEqual(train["line_count"], 1)
        self.assertEqual(train["overlong_records"], 1)
        self.assertEqual(train["nul_bytes"], 2 * 1024 * 1024)
        self.assertEqual(train["malformed_records"], 1)

    def test_failed_scan_leaves_incomplete_marker_without_completed_report(self):
        with patch("mlops.dvc.inventory._inventory_one", side_effect=OSError("synthetic disk failure")):
            with self.assertRaisesRegex(OSError, "synthetic disk failure"):
                create_inventory(dataset=self.dataset, output_dir=self.output)
        partials = list(self.output.glob("*.partial.json"))
        self.assertEqual(len(partials), 1)
        self.assertEqual(json.loads(partials[0].read_text())["status"], "incomplete")
        completed_reports = [path for path in self.output.glob("inventory-*.json") if ".partial." not in path.name]
        self.assertEqual(completed_reports, [])

    def test_interrupted_full_overlap_scan_is_not_marked_complete(self):
        with patch("mlops.dvc.inventory.validate_dataset", side_effect=OSError("synthetic overlap disk failure")):
            with self.assertRaisesRegex(OSError, "overlap disk failure"):
                create_inventory(dataset=self.dataset, output_dir=self.output, mode="full")
        partials = list(self.output.glob("*.partial.json"))
        self.assertEqual(len(partials), 1)
        self.assertEqual(json.loads(partials[0].read_text())["status"], "incomplete")
        completed = [p for p in self.output.glob("inventory-*.json") if ".partial." not in p.name]
        self.assertEqual(completed, [])


if __name__ == "__main__":
    unittest.main()
