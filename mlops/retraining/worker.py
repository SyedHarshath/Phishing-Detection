"""Explicit, isolated worker for persisted OPTIMUS retraining jobs.

Without ``--execute`` this is strictly read-only. Execution claims the job only
after all local artifacts and provenance checks pass. DVC is queried read-only.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import importlib.util
import os
import sqlite3
import sys
import tempfile
import uuid
from collections import Counter
from pathlib import Path

from mlops.data import lifecycle
from mlops.dvc.diagnostics import diagnose
from mlops.dvc.validate_splits import _domain

ROOT = Path(__file__).resolve().parents[2]


def log_mlflow_best_effort(candidate_dir: Path, bundle: dict, job_id: str) -> dict:
    """Use only an explicitly configured tracking URI; never start a service."""
    uri = os.getenv("MLFLOW_TRACKING_URI")
    if not uri or importlib.util.find_spec("mlflow") is None:
        return {"status": "unavailable", "reason": "tracking_uri_or_mlflow_package_missing"}
    try:
        import mlflow
        from mlflow.tracking import MlflowClient
        mlflow.set_tracking_uri(uri)
        mlflow.set_experiment("OPTIMUS")
        with mlflow.start_run(run_name=f"retraining-{job_id}") as run:
            mlflow.set_tags({"project": "OPTIMUS", "stage": "candidate-training",
                             "model_role": "candidate-not-production", "job_id": job_id})
            mlflow.log_params({"candidate_id": bundle["candidate_id"],
                               "preprocessing_version": bundle["preprocessing_version"],
                               "training_count": bundle["training"]["train_count"],
                               "validation_count": bundle["training"]["validation_count"]})
            mlflow.log_artifacts(str(candidate_dir), artifact_path="candidate")
            client = MlflowClient(tracking_uri=uri)
            with tempfile.TemporaryDirectory(prefix="optimus-mlflow-verify-") as temp:
                retrieved = Path(client.download_artifacts(run.info.run_id, "candidate/bundle.json", temp))
                verified = json.loads(retrieved.read_text(encoding="utf-8"))
                if verified.get("candidate_id") != bundle.get("candidate_id"):
                    raise RuntimeError("retrieved MLflow bundle does not match candidate")
            return {"status": "artifacts_verified", "run_id": run.info.run_id, "tracking_uri": uri}
    except Exception as exc:
        return {"status": "failed", "reason": f"{type(exc).__name__}: {exc}", "tracking_uri": uri}


def _sha(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as stream:
        for part in iter(lambda: stream.read(1024 * 1024), b""):
            h.update(part)
    return h.hexdigest()


def _read_only_job(job_id: str, db_path: Path):
    lifecycle.require_schema(db_path)
    conn = sqlite3.connect(f"file:{db_path.resolve().as_posix()}?mode=ro", uri=True)
    conn.row_factory = sqlite3.Row
    try:
        row = conn.execute("SELECT * FROM retraining_jobs WHERE job_id=?", (job_id,)).fetchone()
        return dict(row) if row else None
    finally:
        conn.close()


def verify_batch(batch_id: str, db_path: Path, batch_root: Path) -> tuple[dict | None, list[str]]:
    problems = []
    conn = sqlite3.connect(f"file:{db_path.resolve().as_posix()}?mode=ro", uri=True)
    conn.row_factory = sqlite3.Row
    try:
        row = conn.execute("SELECT * FROM training_batches WHERE batch_id=?", (batch_id,)).fetchone()
        if row is None:
            return None, ["approved_batch_not_found"]
        source_rows = conn.execute("""SELECT p.id,p.url,p.verified_label,p.verification_status,
            p.training_eligible,p.label_provenance FROM training_batch_records b
            JOIN predictions p ON p.id=b.prediction_id WHERE b.batch_id=? ORDER BY p.id""", (batch_id,)).fetchall()
        ids = [r["id"] for r in source_rows]
    finally:
        conn.close()
    directory = batch_root / batch_id
    manifest_path, data_path = directory / "manifest.json", directory / "train.tsv"
    try:
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        data = data_path.read_bytes()
    except (OSError, json.JSONDecodeError):
        return None, ["batch_manifest_or_data_missing_or_invalid"]
    digest = hashlib.sha256(data).hexdigest()
    records, counts, urls = [], Counter(), set()
    for line_no, raw in enumerate(data.decode("utf-8").splitlines(), 1):
        label, sep, url = raw.partition("\t")
        if not sep or label not in lifecycle.LABELS or not url.strip() or _domain(url.strip()) is None:
            problems.append(f"invalid_batch_record:{line_no}"); continue
        if url in urls:
            problems.append("duplicate_batch_url"); continue
        urls.add(url); counts[label] += 1; records.append((label, url))
    if digest != row["fingerprint"] or digest != manifest.get("sha256"):
        problems.append("batch_checksum_mismatch")
    if manifest.get("batch_id") != batch_id or manifest.get("verification_status") != "verified":
        problems.append("batch_manifest_identity_or_verification_mismatch")
    if len(records) != row["record_count"] or len(records) != manifest.get("record_count"):
        problems.append("batch_record_count_mismatch")
    if sorted(ids) != sorted(manifest.get("source_prediction_ids", [])):
        problems.append("batch_source_ids_mismatch")
    expected_records = [(r["verified_label"], r["url"]) for r in source_rows]
    if expected_records != records:
        problems.append("batch_source_content_mismatch")
    if any(r["verification_status"] != "verified" or not r["training_eligible"]
           or not r["label_provenance"] for r in source_rows):
        problems.append("batch_source_no_longer_verified")
    if dict(counts) != manifest.get("label_distribution"):
        problems.append("batch_label_distribution_mismatch")
    return {"batch_id": batch_id, "manifest": manifest, "records": records,
            "fingerprint": digest, "directory": str(directory)}, problems


def verify_baseline(dataset: Path, report_path: Path, dvc_result: dict,
                    provenance_path: Path | None = None) -> tuple[dict | None, list[str]]:
    problems = []
    try:
        report = json.loads(report_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        report = None
        problems.append("strict_baseline_validation_report_missing_or_invalid")
    if report is not None and report.get("passed") is not True:
        problems.append("strict_baseline_validation_not_passed")
    if not dvc_result.get("ok"):
        problems.append("read_only_dvc_status_failed")
    elif dvc_result.get("stdout", "").strip() or dvc_result.get("stderr", "").strip():
        problems.append("dvc_reports_workspace_changes")
    hashes = {}
    for split in ("train", "val"):
        path = dataset / f"{split}.txt"
        expected = (report or {}).get("splits", {}).get(split, {}).get("sha256")
        if not path.is_file() or not expected or _sha(path) != expected:
            problems.append(f"baseline_{split}_fingerprint_mismatch")
        else:
            hashes[split] = expected
    all_split_hashes = {}
    for split in ("train", "val", "test"):
        path = dataset / f"{split}.txt"
        if path.is_file():
            all_split_hashes[split] = _sha(path)
    provenance = None
    if provenance_path is None or not provenance_path.is_file():
        problems.append("dataset_source_provenance_manifest_missing")
    else:
        try:
            provenance = json.loads(provenance_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            provenance = None
        if not (isinstance(provenance, dict)
                and provenance.get("approved") is True
                and provenance.get("authoritative_for_training") is True
                and provenance.get("verified") is True
                and provenance.get("source_reference")
                and provenance.get("verification_evidence")
                and provenance.get("verified_by")
                and provenance.get("split_sha256") == all_split_hashes):
            problems.append("dataset_source_provenance_unverified_or_fingerprint_mismatch")
    pointer = ROOT / "dataset" / "big_dataset.dvc"
    lock = ROOT / "dvc.lock"
    if not pointer.is_file() or not lock.is_file():
        problems.append("dvc_pointer_or_lock_missing")
    source_provenance_verified = not any(p.startswith("dataset_source_provenance") for p in problems)
    source_digest = _sha(provenance_path) if provenance_path and provenance_path.is_file() and source_provenance_verified else None
    return ({"validation_report": str(report_path), "split_sha256": hashes,
             "source_provenance_verified": source_provenance_verified,
             "authoritative_registration": source_provenance_verified,
             "source_registration_sha256": source_digest,
             "dataset": str(dataset)}, problems)


def run_worker(job_id: str, *, execute: bool = False, db_path: Path = lifecycle.DEFAULT_DB,
               batch_root: Path = lifecycle.DEFAULT_BATCH_DIR,
               dataset: Path = ROOT / "dataset" / "big_dataset",
               validation_report: Path = ROOT / "mlops" / "dvc" / "validation_report.json",
               provenance_manifest: Path | None = None,
               dvc_check=diagnose, trainer=None) -> dict:
    job = _read_only_job(job_id, db_path)
    blockers = []
    if not job:
        blockers.append("job_not_found")
    elif job["status"] != "eligible":
        blockers.append(f"job_not_claimable:{job['status']}")
    elif not job.get("batch_id"):
        blockers.append("job_has_no_approved_batch")
    batch = None
    baseline = None
    if job and job.get("batch_id"):
        batch, batch_errors = verify_batch(job["batch_id"], db_path, batch_root)
        blockers.extend(batch_errors)
    dvc = dvc_check(ROOT)
    baseline, baseline_errors = verify_baseline(dataset, validation_report, dvc, provenance_manifest)
    blockers.extend(baseline_errors)
    result = {"job_id": job_id, "dry_run": not execute, "job_status": job.get("status") if job else None,
              "batch": ({k: v for k, v in batch.items() if k != "records"} if batch else None),
              "baseline": baseline, "dvc": dvc, "decision": "BLOCKED" if blockers else "READY",
              "blockers": blockers, "training_started": False, "promotion_enabled": False}
    if not execute or blockers:
        return result
    if not lifecycle.claim_job(job_id, db_path):
        return {**result, "decision": "BLOCKED", "blockers": ["job_claim_lost_to_concurrent_worker"]}
    attempt_id = str(uuid.uuid4())
    out_dir = ROOT / "artifacts" / "candidates" / job_id / attempt_id
    try:
        # Load only baseline train/val and verified batch records; test.txt is never read.
        from src.data_loader import DatasetSplit, load_split
        base_train = load_split("train", dataset_dir=dataset)
        val_split = load_split("val", dataset_dir=dataset)
        labels = {"phishing": 0, "legitimate": 1}
        batch_split = DatasetSplit(urls=[u for _, u in batch["records"]],
                                   labels=[labels[l] for l, _ in batch["records"]],
                                   split="approved_batch", parsed_records=len(batch["records"]),
                                   malformed_records=0,
                                   label_counts={"phishing": sum(l == "phishing" for l, _ in batch["records"]),
                                                 "legitimate": sum(l == "legitimate" for l, _ in batch["records"])},
                                   sample_size=len(batch["records"]))
        train_split = DatasetSplit(urls=base_train.urls + batch_split.urls,
                                   labels=base_train.labels + batch_split.labels,
                                   split="train_plus_verified_batch",
                                   parsed_records=base_train.parsed_records + batch_split.parsed_records,
                                   malformed_records=base_train.malformed_records,
                                   label_counts={"phishing": base_train.label_counts.get("phishing", 0) + batch_split.label_counts["phishing"],
                                                 "legitimate": base_train.label_counts.get("legitimate", 0) + batch_split.label_counts["legitimate"]},
                                   sample_size=len(base_train.urls) + len(batch_split.urls))
        # Prevent exact duplicate examples and validation-domain leakage.
        if set(base_train.urls).intersection(batch_split.urls):
            raise ValueError("approved batch overlaps baseline training URLs")
        val_domains = {_domain(url) for url in val_split.urls}
        batch_domains = {_domain(url) for url in batch_split.urls}
        if val_domains.intersection(batch_domains):
            raise ValueError("approved batch shares domains with baseline validation data")
        train = trainer
        if train is None:
            from src.train_smoke import train_candidate_from_splits
            train = train_candidate_from_splits
        snapshot_hash = hashlib.sha256((baseline["split_sha256"]["train"] + batch["fingerprint"]
                                       + str(len(base_train.urls)) + str(len(batch_split.urls))).encode("ascii")).hexdigest()
        bundle = train(train_split, val_split, out_dir, metadata={"job_id": job_id,
                        "batch_id": batch["batch_id"], "batch_fingerprint": batch["fingerprint"],
                        "baseline_fingerprints": baseline["split_sha256"],
                        "source_provenance_verified": baseline["source_provenance_verified"],
                        "authoritative_registration": baseline["authoritative_registration"],
                        "source_registration_sha256": baseline["source_registration_sha256"],
                        "training_dataset_fingerprint": snapshot_hash, "test_only": False})
        tracking = log_mlflow_best_effort(out_dir, bundle, job_id)
        lifecycle.finish_job(job_id, True, db_path, snapshot_id=batch["fingerprint"])
        return {**result, "decision": "COMPLETED_CANDIDATE", "job_status": "completed",
                "training_started": True, "candidate_bundle": bundle, "candidate_dir": str(out_dir),
                "mlflow_tracking": tracking}
    except Exception as exc:
        lifecycle.finish_job(job_id, False, db_path, error=f"{type(exc).__name__}: {exc}")
        return {**result, "decision": "FAILED", "job_status": "failed",
                "training_started": True, "error": f"{type(exc).__name__}: {exc}"}


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("job_id")
    parser.add_argument("--execute", action="store_true", help="claim job and train an isolated candidate")
    parser.add_argument("--db", type=Path, default=lifecycle.DEFAULT_DB)
    parser.add_argument("--batch-root", type=Path, default=lifecycle.DEFAULT_BATCH_DIR)
    parser.add_argument("--dataset", type=Path, default=ROOT / "dataset" / "big_dataset")
    parser.add_argument("--validation-report", type=Path, default=ROOT / "mlops" / "dvc" / "validation_report.json")
    parser.add_argument("--provenance-manifest", type=Path,
                        help="independently reviewed manifest binding the source reference to train/val/test SHA-256 values")
    args = parser.parse_args()
    report = run_worker(args.job_id, execute=args.execute, db_path=args.db,
                        batch_root=args.batch_root, dataset=args.dataset,
                        validation_report=args.validation_report,
                        provenance_manifest=args.provenance_manifest)
    print(json.dumps(report, indent=2, default=str))
    return 0 if report["decision"] in {"READY", "COMPLETED_CANDIDATE"} else 2


if __name__ == "__main__":
    raise SystemExit(main())
