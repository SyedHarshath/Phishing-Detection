"""Offline builder for a frozen, temporally prospective evaluation snapshot.

This module never visits URLs. Source trust is an operator-supplied review
allowlist, and historical independence remains explicitly separate.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import re
import shutil
import sqlite3
import tempfile
import uuid
from datetime import datetime, timezone
from pathlib import Path

from mlops.dvc.validate_splits import LABELS, _canonical_url, _domain, audit_external_overlap

SHA256_RE = re.compile(r"^[0-9a-f]{64}$")


def _timestamp(value, field):
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"missing_timestamp:{field}")
    try: parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError as exc: raise ValueError(f"invalid_timestamp:{field}") from exc
    if parsed.tzinfo is None: raise ValueError(f"timezone_required:{field}")
    return parsed.astimezone(timezone.utc)


def _sha(path):
    digest = hashlib.sha256()
    with Path(path).open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""): digest.update(chunk)
    return digest.hexdigest()


def build_prospective_evaluation(*, records_jsonl: Path, protocol_path: Path,
        output_root: Path, training_sources: dict[str, Path],
        psl_path: Path, psl_sha256: str, psl_version: str) -> dict:
    records_jsonl, protocol_path = Path(records_jsonl), Path(protocol_path)
    protocol = json.loads(protocol_path.read_text(encoding="utf-8"))
    required = ("incumbent_version", "incumbent_bundle_sha256", "frozen_at_utc",
                "freeze_verified_by", "trusted_sources")
    if any(not protocol.get(key) for key in required):
        raise ValueError("freeze protocol missing incumbent identity, timestamp, reviewer or trusted_sources")
    if not SHA256_RE.fullmatch(protocol["incumbent_bundle_sha256"]):
        raise ValueError("incumbent_bundle_sha256 must be a SHA-256 hex digest")
    freeze_at = _timestamp(protocol["frozen_at_utc"], "frozen_at_utc")
    if freeze_at > datetime.now(timezone.utc):
        raise ValueError("incumbent freeze timestamp is in the future")
    trusted_sources = protocol["trusted_sources"]
    if not isinstance(trusted_sources, dict) or not trusted_sources:
        raise ValueError("trusted_sources must contain operator-reviewed source entries")
    for source_id, review in trusted_sources.items():
        if (not isinstance(review, dict) or review.get("approved") is not True
                or not review.get("source_reference") or not review.get("reviewed_by")
                or not review.get("label_quality_evidence")
                or not isinstance(review.get("approved_label_methods"), list)
                or not review["approved_label_methods"]):
            raise ValueError(f"trusted source review incomplete: {source_id}")

    output_root = Path(output_root)
    output_root.mkdir(parents=True, exist_ok=True)
    stage = output_root / f"prospective-{datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%SZ')}-{uuid.uuid4().hex[:8]}"
    stage.mkdir()
    partial = stage / "evaluation.tsv.partial"
    preserved_records = stage / "source_records.jsonl"
    rejected_counts, label_counts = {}, {"phishing": 0, "legitimate": 0}
    records_seen = accepted = 0
    exclusion_path = stage / "exclusions.jsonl"
    exclusion_hash = hashlib.sha256()
    partial_report_path = stage / "readiness.partial.json"
    snapshot_at = datetime.now(timezone.utc)
    run_metadata = {"stage_id": stage.name, "records_jsonl_path": str(preserved_records),
        "protocol_path": str(protocol_path), "protocol_sha256": _sha(protocol_path),
        "incumbent_version": protocol["incumbent_version"],
        "incumbent_bundle_sha256": protocol["incumbent_bundle_sha256"],
        "freeze_timestamp_utc": protocol["frozen_at_utc"]}
    partial_report_path.write_text(json.dumps({**run_metadata, "status": "running", "phase": "record_validation"}, indent=2), encoding="utf-8")
    try:
        shutil.copyfile(records_jsonl, preserved_records)
        with tempfile.TemporaryDirectory(prefix="optimus-evaluation-") as tmp:
            conn = sqlite3.connect(str(Path(tmp) / "eval.sqlite"))
            conn.execute("CREATE TABLE urls (url TEXT PRIMARY KEY, canonical TEXT UNIQUE, label TEXT NOT NULL)")
            with records_jsonl.open("r", encoding="utf-8") as src, partial.open("w", encoding="utf-8", newline="\n") as out, exclusion_path.open("wb") as exclusions:
                for line_no, raw in enumerate(src, 1):
                    if not raw.strip(): continue
                    records_seen += 1
                    try: record = json.loads(raw)
                    except json.JSONDecodeError:
                        reason = "invalid_json"
                        entry = json.dumps({"line": line_no, "reason": reason}, separators=(",", ":")).encode() + b"\n"
                        exclusions.write(entry); exclusion_hash.update(entry); rejected_counts[reason] = rejected_counts.get(reason, 0) + 1
                        continue
                    try:
                        if not isinstance(record, dict): raise ValueError("record_not_object")
                        url = str(record.get("url", "")).strip()
                        label = record.get("label")
                        if label not in LABELS: raise ValueError("invalid_label")
                        if any(ord(char) < 32 or ord(char) == 127 for char in url) or _domain(url) is None:
                            raise ValueError("invalid_http_url_or_control_character")
                        source_id = record.get("source_id")
                        source = trusted_sources.get(source_id) if isinstance(source_id, str) else None
                        if not isinstance(source, dict) or source.get("approved") is not True:
                            raise ValueError("label_source_not_operator_approved")
                        if record.get("label_method") not in source.get("approved_label_methods", []):
                            raise ValueError("unapproved_label_method")
                        for field in ("source_record_id", "label_provenance_reference", "verified_by"):
                            if not isinstance(record.get(field), str) or not record[field].strip():
                                raise ValueError(f"missing_{field}")
                        observed = _timestamp(record.get("first_observed_at_utc"), "first_observed_at_utc")
                        verified = _timestamp(record.get("label_verified_at_utc"), "label_verified_at_utc")
                        if observed <= freeze_at: raise ValueError("record_not_observed_after_model_freeze")
                        if verified < observed or verified > datetime.now(timezone.utc): raise ValueError("invalid_label_verification_time")
                        if record.get("verified_label") is not True: raise ValueError("label_not_verified")
                        if record.get("excluded_from_training") is not True: raise ValueError("training_exclusion_not_attested")
                        if record.get("excluded_from_selection") is not True: raise ValueError("selection_exclusion_not_attested")
                        canonical = _canonical_url(url)
                        try:
                            conn.execute("INSERT INTO urls VALUES (?,?,?)", (url, canonical, label))
                        except sqlite3.IntegrityError:
                            prior = conn.execute("SELECT label FROM urls WHERE url=? OR canonical=?", (url, canonical)).fetchone()
                            raise ValueError("duplicate_or_conflicting_evaluation_url" if prior and prior[0] != label else "duplicate_evaluation_url")
                        out.write(f"{label}\t{url}\n")
                        accepted += 1; label_counts[label] += 1
                    except (ValueError, TypeError) as exc:
                        reason = str(exc)
                        entry = json.dumps({"line": line_no, "reason": reason}, separators=(",", ":")).encode() + b"\n"
                        exclusions.write(entry); exclusion_hash.update(entry); rejected_counts[reason] = rejected_counts.get(reason, 0) + 1
                    if records_seen % 100_000 == 0:
                        print(f"[prospective] processed {records_seen:,} records", flush=True)
            conn.close()
        snapshot_at = datetime.now(timezone.utc)
        records_sha256 = _sha(records_jsonl)
        if not training_sources:
            rejected_counts["known_training_sources_not_supplied"] = 1
        if rejected_counts or not accepted:
            partial.unlink(missing_ok=True)
            result = {**run_metadata, "records_jsonl_sha256": records_sha256,
                "status": "blocked", "decision": "BLOCKED", "created_at_utc": snapshot_at.isoformat(),
                "records_seen": records_seen, "accepted_records": accepted, "label_counts": label_counts,
                "blocking_reasons": rejected_counts or {"no_eligible_records": 1},
                "exclusions_sha256": exclusion_hash.hexdigest(), "evaluation_file_created": False,
                "incumbent": {"version": protocol["incumbent_version"],
                    "bundle_sha256": protocol["incumbent_bundle_sha256"], "frozen_at_utc": protocol["frozen_at_utc"]}}
            (stage / "readiness.json").write_text(json.dumps(result, indent=2), encoding="utf-8")
            partial_report_path.unlink(missing_ok=True)
            return result
        final_path = stage / "evaluation.tsv"
        partial.replace(final_path)
        progress_state = {**run_metadata, "status": "running", "phase": "overlap_audit",
                          "records_jsonl_sha256": records_sha256}
        def report_progress(source_name, processed):
            progress_state["progress"] = {"source": source_name, "processed_records": processed,
                "updated_at_utc": datetime.now(timezone.utc).isoformat()}
            partial_report_path.write_text(json.dumps(progress_state, indent=2), encoding="utf-8")
            print(f"[overlap] {source_name}: processed {processed:,} records", flush=True)
        try:
            overlap = audit_external_overlap(training_sources=training_sources, evaluation_path=final_path,
                psl_path=psl_path, psl_sha256=psl_sha256, psl_version=psl_version,
                progress=report_progress)
        except Exception as exc:
            result = {**run_metadata, "records_jsonl_sha256": records_sha256,
                "status": "incomplete", "decision": "BLOCKED",
                "blocking_reasons": [f"overlap_audit_incomplete:{type(exc).__name__}:{exc}"],
                "evaluation_file_created": False, "progress": progress_state.get("progress")}
            final_path.unlink(missing_ok=True)
            (stage / "readiness.json").write_text(json.dumps(result, indent=2), encoding="utf-8")
            return result
        independent_nonoverlap = all(not any(v for v in source.values()) for source in overlap["overlap_by_training_source"].values())
        readiness = "READY_FOR_FROZEN_INCUMBENT_EVALUATION" if independent_nonoverlap else "BLOCKED"
        if not independent_nonoverlap: final_path.unlink(missing_ok=True)
        result = {**run_metadata, "schema_version": 1, "status": "completed", "decision": readiness,
            "created_at_utc": snapshot_at.isoformat(), "records_seen": records_seen,
            "records_jsonl_sha256": records_sha256,
            "preserved_source_records_path": str(preserved_records),
            "protocol_sha256": _sha(protocol_path),
            "accepted_records": accepted, "label_counts": label_counts,
            "evaluation_snapshot_sha256": _sha(final_path) if final_path.exists() else None,
            "evaluation_snapshot_path": str(final_path) if final_path.exists() else None,
            "evaluation_file_created": final_path.exists(), "exclusions_sha256": exclusion_hash.hexdigest(),
            "incumbent": {"version": protocol["incumbent_version"],
                "bundle_sha256": protocol["incumbent_bundle_sha256"], "frozen_at_utc": protocol["frozen_at_utc"]},
            "trusted_source_reviews": trusted_sources, "overlap_audit": overlap,
            "known_training_nonoverlap": independent_nonoverlap,
            "blocking_reasons": [] if independent_nonoverlap else ["overlap_with_known_training_source"],
            "historical_independence": "unproven" if protocol.get("incumbent_training_history_verified") is not True else "training_history_must_be_cross_checked",
            "promotion_eligible": False,
            "exclusions": {"records_rejected": sum(rejected_counts.values()), "reason_counts": rejected_counts,
                "exclusions_sha256": exclusion_hash.hexdigest()},
            "policy": {"threshold_tuning_allowed": False, "training_or_selection_use_allowed": False},
            "limitations": ["Source allowlist and record attestations require operator verification.",
                "No URL was fetched. Zero overlap against supplied known sources does not prove no unknown historical overlap."]}
        (stage / "readiness.json").write_text(json.dumps(result, indent=2), encoding="utf-8")
        partial_report_path.unlink(missing_ok=True)
        return result
    except Exception:
        partial.unlink(missing_ok=True)
        raise


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("records", type=Path); parser.add_argument("protocol", type=Path)
    parser.add_argument("--training-source", action="append", default=[], metavar="NAME=PATH")
    parser.add_argument("--psl-file", type=Path, required=True)
    parser.add_argument("--psl-sha256", required=True); parser.add_argument("--psl-version", required=True)
    parser.add_argument("--output-root", type=Path, default=Path("mlops/evaluation/prospective"))
    args = parser.parse_args()
    sources = {}
    for item in args.training_source:
        name, sep, path = item.partition("=")
        if not sep or not name or name in sources: parser.error("each --training-source must be a unique NAME=PATH")
        sources[name] = Path(path)
    result = build_prospective_evaluation(records_jsonl=args.records, protocol_path=args.protocol,
        output_root=args.output_root, training_sources=sources,
        psl_path=args.psl_file, psl_sha256=args.psl_sha256, psl_version=args.psl_version)
    print(json.dumps(result, indent=2))
    return 0 if result.get("decision") != "BLOCKED" else 2


if __name__ == "__main__": raise SystemExit(main())
