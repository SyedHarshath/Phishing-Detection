import tempfile
import unittest
from pathlib import Path

from mlops.dvc.inspect_dataset import inspect_split
from src.data_loader import DatasetFormatError, load_split


class DatasetLoaderTests(unittest.TestCase):
    def test_parses_labels_and_skips_malformed_records(self):
        with tempfile.TemporaryDirectory() as temporary_directory:
            root = Path(temporary_directory)
            (root / "train.txt").write_bytes(
                b"phishing\thttps://bad.example\r\n"
                b"legitimate\thttps://good.example\r\n"
                b"unknown\thttps://invalid.example\r\n"
                b"phishing\t\r\n"
            )

            result = load_split("train", sample_size=2, seed=7, dataset_dir=root)

        self.assertEqual(result.parsed_records, 2)
        self.assertEqual(result.malformed_records, 2)
        self.assertEqual(result.label_counts, {"phishing": 1, "legitimate": 1})
        self.assertEqual(set(result.labels), {0, 1})
        self.assertEqual(set(result.urls), {
            "https://bad.example",
            "https://good.example",
        })

    def test_rejects_zero_filled_binary_split(self):
        with tempfile.TemporaryDirectory() as temporary_directory:
            root = Path(temporary_directory)
            (root / "train.txt").write_bytes(bytes(256))

            with self.assertRaisesRegex(DatasetFormatError, "binary"):
                load_split("train", dataset_dir=root)

    def test_invalid_split_name_is_rejected(self):
        with self.assertRaisesRegex(ValueError, "Unknown split"):
            load_split("training")

    def test_inspector_reports_text_schema_and_malformed_records(self):
        with tempfile.TemporaryDirectory() as temporary_directory:
            path = Path(temporary_directory) / "val.txt"
            path.write_bytes(
                b"phishing\thttps://bad.example\r\n"
                b"legitimate\thttps://good.example\r\n"
                b"malformed row\r\n"
            )

            report = inspect_split(path)

        self.assertEqual(report["encoding"], "UTF-8")
        self.assertEqual(report["schema"]["field_order"], ["label", "url"])
        self.assertFalse(report["schema"]["header_present"])
        self.assertEqual(report["parsed_record_count"], 2)
        self.assertEqual(report["malformed_record_count"], 1)
        self.assertEqual(report["label_counts"], {"phishing": 1, "legitimate": 1})

    def test_inspector_identifies_zero_filled_binary(self):
        with tempfile.TemporaryDirectory() as temporary_directory:
            path = Path(temporary_directory) / "train.txt"
            path.write_bytes(bytes(512))

            report = inspect_split(path)

        self.assertEqual(report["detected_type"], "zero-filled binary")
        self.assertEqual(report["parsing_status"], "not_parseable_as_text")
        self.assertEqual(report["parsed_record_count"], 0)
        self.assertIsNone(report["malformed_record_count"])


if __name__ == "__main__":
    unittest.main()
