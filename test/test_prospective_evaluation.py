import hashlib
import json
import tempfile
import unittest
from pathlib import Path

from mlops.dvc.validate_splits import PublicSuffixList, audit_external_overlap
from mlops.evaluation.prospective import build_prospective_evaluation


class ProspectiveEvaluationTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(); self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.psl = self.root / "public_suffix_list.dat"
        self.psl.write_text("com\nco.uk\n*.ck\n!www.ck\n", encoding="utf-8")
        self.psl_sha = hashlib.sha256(self.psl.read_bytes()).hexdigest()
        self.train = self.root / "train.txt"
        self.train.write_text("phishing\thttps://train.example.com/a\nlegitimate\thttps://ok.co.uk/\n", encoding="utf-8")
        self.protocol = self.root / "protocol.json"
        self.protocol.write_text(json.dumps({"incumbent_version": "legacy-frozen",
            "incumbent_bundle_sha256": "a" * 64, "frozen_at_utc": "2026-01-01T00:00:00Z",
            "freeze_verified_by": "reviewer", "trusted_sources": {"feed": {
                "approved": True, "source_reference": "feed-ref", "reviewed_by": "reviewer",
                "label_quality_evidence": "verification-doc", "approved_label_methods": ["source-verified"]}}}), encoding="utf-8")

    def _record(self, url="https://independent.test/a", **extra):
        return {"url": url, "label": "phishing", "source_id": "feed", "source_record_id": "item-1",
            "first_observed_at_utc": "2026-02-01T00:00:00Z", "label_verified_at_utc": "2026-02-02T00:00:00Z",
            "label_method": "source-verified", "label_provenance_reference": "verified-record-1",
            "verified_by": "reviewer", "verified_label": True,
            "excluded_from_training": True, "excluded_from_selection": True, **extra}

    def _write_records(self, records):
        p = self.root / "records.jsonl"
        p.write_text("".join(json.dumps(item) + "\n" for item in records), encoding="utf-8")
        return p

    def test_freezes_temporal_records_and_distinguishes_unknown_history(self):
        result = build_prospective_evaluation(records_jsonl=self._write_records([self._record()]),
            protocol_path=self.protocol, output_root=self.root / "out",
            training_sources={"known-train": self.train}, psl_path=self.psl,
            psl_sha256=self.psl_sha, psl_version="fixture-psl-v1")
        self.assertEqual(result["decision"], "READY_FOR_FROZEN_INCUMBENT_EVALUATION")
        self.assertTrue(result["known_training_nonoverlap"])
        self.assertEqual(result["historical_independence"], "unproven")
        self.assertFalse(result["promotion_eligible"])

    def test_missing_temporal_or_verification_evidence_blocks_snapshot(self):
        record = self._record(); record["first_observed_at_utc"] = None
        result = build_prospective_evaluation(records_jsonl=self._write_records([record]),
            protocol_path=self.protocol, output_root=self.root / "out",
            training_sources={"known-train": self.train}, psl_path=self.psl,
            psl_sha256=self.psl_sha, psl_version="fixture-psl-v1")
        self.assertEqual(result["decision"], "BLOCKED")
        self.assertFalse(result["evaluation_file_created"])
        self.assertIn("missing_timestamp:first_observed_at_utc", result["blocking_reasons"])

    def test_overlap_audit_reports_exact_and_registrable_domains(self):
        evaluation = self.root / "evaluation.tsv"
        evaluation.write_text("legitimate\thttps://train.example.com/path\n", encoding="utf-8")
        overlap = audit_external_overlap(training_sources={"known-train": self.train}, evaluation_path=evaluation,
            psl_path=self.psl, psl_sha256=self.psl_sha, psl_version="fixture-psl-v1")
        values = overlap["overlap_by_training_source"]["known-train"]
        self.assertEqual(values["hostname_overlap"], 1)
        self.assertEqual(values["registrable_domain_overlap"], 1)
        self.assertEqual(values["exact_url_overlap"], 0)

    def test_malformed_training_source_keeps_overlap_audit_incomplete(self):
        malformed = self.root / "malformed.txt"
        malformed.write_text("unstructured line\n", encoding="utf-8")
        result = build_prospective_evaluation(records_jsonl=self._write_records([self._record()]),
            protocol_path=self.protocol, output_root=self.root / "out",
            training_sources={"malformed": malformed}, psl_path=self.psl,
            psl_sha256=self.psl_sha, psl_version="fixture-psl-v1")
        self.assertEqual(result["decision"], "BLOCKED")
        self.assertEqual(result["status"], "incomplete")
        self.assertFalse(result["evaluation_file_created"])

    def test_psl_wildcard_exception_and_hash_are_enforced(self):
        resolver = PublicSuffixList(self.psl, self.psl_sha, "fixture-psl-v1")
        self.assertEqual(resolver.registrable_domain("a.b.co.uk"), "b.co.uk")
        self.assertEqual(resolver.registrable_domain("a.x.ck"), "a.x.ck")
        self.assertEqual(resolver.registrable_domain("www.ck"), "www.ck")
        with self.assertRaisesRegex(ValueError, "SHA-256"):
            PublicSuffixList(self.psl, "0" * 64, "fixture-psl-v1")


if __name__ == "__main__": unittest.main()
