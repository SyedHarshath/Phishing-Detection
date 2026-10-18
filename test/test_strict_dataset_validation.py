import tempfile
import unittest
from pathlib import Path
import hashlib

from mlops.dvc.validate_splits import validate_dataset


class StrictDatasetValidationTests(unittest.TestCase):
    def _dataset(self, rows):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        root = Path(temporary.name)
        for split in ("train", "val", "test"):
            (root / f"{split}.txt").write_bytes(rows.get(split, b"legitimate\thttps://ok.example/\n"))
        return root

    def test_clean_splits_pass_and_empty_trailing_line_is_reported(self):
        root = self._dataset({"train": b"phishing\thttps://bad.example/\n", "val": b"legitimate\thttps://ok.example/\n\n", "test": b"phishing\thttps://test.example/\n"})
        result = validate_dataset(root)
        self.assertTrue(result["passed"])
        self.assertEqual(result["splits"]["val"]["empty_lines"], 1)

    def test_malformed_row_fails_closed(self):
        root = self._dataset({"train": b"unknown\thttps://bad.example/\n"})
        result = validate_dataset(root)
        self.assertFalse(result["passed"])
        self.assertEqual(result["splits"]["train"]["malformed"], 1)

    def test_nul_input_is_rejected(self):
        root = self._dataset({"train": b"legitimate\x00\thttps://ok.example/\n"})
        self.assertFalse(validate_dataset(root)["passed"])

    def test_duplicate_and_cross_split_url_fail(self):
        url = b"legitimate\thttps://same.example/path\n"
        root = self._dataset({"train": url, "val": url})
        result = validate_dataset(root)
        self.assertFalse(result["passed"])
        self.assertEqual(result["cross_split"]["exact_url_overlap"], 1)

    def test_canonical_url_overlap_and_conflicting_label_fail(self):
        root = self._dataset({
            "train": b"phishing\thttp://EXAMPLE.com:80/a#first\n",
            "val": b"legitimate\thttp://example.com/a#second\n",
        })
        result = validate_dataset(root)
        self.assertFalse(result["passed"])
        self.assertEqual(result["cross_split"]["canonical_url_overlap"], 1)
        self.assertEqual(result["cross_split"]["canonical_label_conflicts"], 1)
        self.assertEqual(result["registrable_domain_overlap"]["status"], "unavailable")

    def test_pinned_psl_reports_each_split_pair_and_input_fingerprint(self):
        root = self._dataset({"train": b"phishing\thttps://a.shop.co.uk/\n",
                              "val": b"legitimate\thttps://b.shop.co.uk/\n",
                              "test": b"legitimate\thttps://ok.example/\n"})
        psl = root / "psl.dat"; psl.write_text("com\nco.uk\n", encoding="utf-8")
        digest = hashlib.sha256(psl.read_bytes()).hexdigest()
        result = validate_dataset(root, psl_path=psl, psl_sha256=digest, psl_version="fixture-1")
        self.assertEqual(result["status"], "completed")
        self.assertTrue(result["input_fingerprint"])
        self.assertEqual(result["public_suffix_list"]["sha256"], digest)
        self.assertEqual(result["cross_split"]["registrable_domain_overlap_by_split"]["train|val"], 1)
        with self.assertRaisesRegex(ValueError, "pinned value"):
            validate_dataset(root, psl_path=psl, psl_sha256="0" * 64, psl_version="fixture-1")

    def test_invalid_url_fails(self):
        root = self._dataset({"train": b"phishing\tnot-a-url\n"})
        result = validate_dataset(root)
        self.assertFalse(result["passed"])
        self.assertEqual(result["splits"]["train"]["invalid_urls"], 1)


if __name__ == "__main__":
    unittest.main()
