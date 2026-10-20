"""Build an isolated OPTIMUS lifecycle demo and stop at real provenance gates.

All generated data lives below %TEMP%/optimus-retraining-demo-sandbox. Synthetic
labels are for software-path demonstration only and are not evidence about URL
classification quality. Candidate training is attempted only as a worker dry run.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import shutil
import sqlite3
import sys
import uuid
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import urlsplit

ROOT = Path(__file__).resolve().parents[1]
TEMP_ROOT = Path(__import__("tempfile").gettempdir()).resolve()
SANDBOX_ROOT = TEMP_ROOT / "optimus-retraining-demo-sandbox"
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))
ROOT_MARKER = ".optimus-retraining-demo-root"
RUN_MARKER = ".generated-by-optimus-retraining-demo"
RUN_MANIFEST = "generated-files.json"
REVIEWER = "OPTIMUS SYNTHETIC DEMO (not a human reviewer)"
RECORDS = 1000
MIN_PER_CLASS = 100


def _sha256_bytes(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


def _sha256_file(path: Path) -> str | None:
    if not path.is_file():
        return None
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def _protected_paths(project_root: Path) -> list[Path]:
    return [
        TEMP_ROOT / "optimus-demo-final" / "predictions.sqlite",
        project_root / "test_results" / "cnn_bilstm" / "best_model.h5",
        project_root / "ml" / "artifacts" / "tokenizer.json",
        project_root / "artifacts" / "model_registry" / "active.json",
        project_root / "artifacts" / "model_registry" / "registry.json",
    ]


def _assert_root_is_safe(root: Path, temp_root: Path, project_root: Path) -> Path:
    root = Path(root)
    if root.is_symlink():
        raise RuntimeError("sandbox root must not be a symbolic link")
    resolved = root.resolve()
    temp_root = temp_root.resolve()
    if resolved.name != "optimus-retraining-demo-sandbox" or resolved.parent != temp_root:
        raise RuntimeError("sandbox must be the dedicated optimus-retraining-demo-sandbox directly under the temp directory")
    for protected in _protected_paths(project_root):
        protected = protected.resolve()
        if resolved == protected or resolved in protected.parents or protected in resolved.parents:
            raise RuntimeError(f"sandbox path overlaps a protected live path: {protected}")
    return resolved


def _remove_owned_runs(root: Path) -> None:
    runs = root / "runs"
    if not runs.exists():
        return
    if runs.is_symlink() or root not in runs.resolve().parents:
        raise RuntimeError("sandbox runs directory escapes the sandbox")
    for run_dir in runs.iterdir():
        if not run_dir.is_dir() or run_dir.is_symlink():
            continue
        marker = run_dir / RUN_MARKER
        manifest_path = run_dir / RUN_MANIFEST
        if not marker.is_file() or marker.read_text(encoding="utf-8") != "OPTIMUS generated sandbox output v1\n":
            continue
        if not manifest_path.is_file():
            # An interrupted run with only our marker is safely removable;
            # preserve any other content for inspection.
            if list(run_dir.iterdir()) == [marker]:
                marker.unlink()
                run_dir.rmdir()
            continue
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        entries = manifest.get("files")
        if not isinstance(entries, list) or any(not isinstance(name, str) for name in entries):
            raise RuntimeError(f"invalid generated-file manifest; refusing reset: {manifest_path}")
        for name in entries:
            relative = Path(name)
            target = (run_dir / relative).resolve()
            if relative.is_absolute() or ".." in relative.parts or run_dir.resolve() not in target.parents:
                raise RuntimeError(f"generated-file manifest escapes its run directory: {name}")
            if target.is_symlink():
                raise RuntimeError(f"refusing to remove a generated path that became a symlink: {target}")
            if target.is_file():
                # The existing batch exporter deliberately chmods registered
                # artifacts read-only. Restore write permission only for a
                # manifest-listed file inside this owned sandbox run.
                target.chmod(0o666)
                target.unlink()
            elif target.is_dir():
                shutil.rmtree(target)
        manifest_path.unlink()
        marker.unlink()
        # Remove only now-empty directories; preserve any unrelated file.
        for directory in sorted((p for p in run_dir.rglob("*") if p.is_dir()), key=lambda p: len(p.parts), reverse=True):
            try:
                directory.rmdir()
            except OSError:
                pass
        try:
            run_dir.rmdir()
        except OSError:
            pass


def _prepare_root(root: Path, temp_root: Path, project_root: Path, reset: bool) -> Path:
    root = _assert_root_is_safe(root, temp_root, project_root)
    marker = root / ROOT_MARKER
    if root.exists():
        if not marker.is_file() or marker.read_text(encoding="utf-8") != "OPTIMUS dedicated sandbox v1\n":
            if any(root.iterdir()):
                raise RuntimeError(f"existing unowned path; refusing to use or clear it: {root}")
            if not reset:
                raise RuntimeError(f"existing empty sandbox needs --reset to establish ownership: {root}")
            marker.write_text("OPTIMUS dedicated sandbox v1\n", encoding="utf-8")
    else:
        root.mkdir(parents=True, exist_ok=False)
        marker.write_text("OPTIMUS dedicated sandbox v1\n", encoding="utf-8")
    if reset:
        _remove_owned_runs(root)
    return root


def _create_run_dir(root: Path) -> Path:
    runs = root / "runs"
    runs.mkdir(exist_ok=True)
    run_dir = runs / (datetime.now(timezone.utc).strftime("run-%Y%m%dT%H%M%S-") + uuid.uuid4().hex[:8])
    run_dir.mkdir(exist_ok=False)
    (run_dir / RUN_MARKER).write_text("OPTIMUS generated sandbox output v1\n", encoding="utf-8")
    return run_dir


def _write_manifest(run_dir: Path) -> None:
    owned = []
    for path in run_dir.rglob("*"):
        if path.is_file() and path.name not in {RUN_MARKER, RUN_MANIFEST}:
            owned.append(path.relative_to(run_dir).as_posix())
    (run_dir / RUN_MANIFEST).write_text(json.dumps({"version": 1, "files": sorted(owned)}, indent=2), encoding="utf-8")


def _seed_database(db_path: Path, record_count: int) -> dict:
    from mlops.data.lifecycle import initialize_schema, record_feedback, review_label

    if record_count < 2 or record_count % 2:
        raise ValueError("synthetic record count must be a positive even number")
    db_path.parent.mkdir(parents=True, exist_ok=True)
    with sqlite3.connect(db_path) as conn:
        conn.execute("""CREATE TABLE predictions (
            id INTEGER PRIMARY KEY AUTOINCREMENT, timestamp TEXT NOT NULL,
            url TEXT NOT NULL, prediction TEXT NOT NULL, confidence REAL NOT NULL,
            model_version TEXT NOT NULL, feedback TEXT, feedback_timestamp TEXT)""")
        rows = []
        for i in range(record_count):
            label = "phishing" if i % 2 == 0 else "legitimate"
            host_label = "phish" if label == "phishing" else "legit"
            url = f"https://{host_label}-{i:05d}.synthetic.invalid/sample/{i:05d}"
            predicted = "legitimate" if label == "phishing" else "phishing"
            rows.append((f"2026-01-01T00:{(i // 60) % 60:02d}:{i % 60:02d}+00:00",
                         url, predicted, 50.0, "synthetic-demo-incumbent", None, None))
        conn.executemany("INSERT INTO predictions(timestamp,url,prediction,confidence,model_version,feedback,feedback_timestamp) VALUES(?,?,?,?,?,?,?)", rows)
    initialize_schema(db_path)
    # This digest identifies the deterministic synthetic generator output. It is
    # not represented as independent or real-world evidence.
    fixture_digest = _sha256_bytes("\n".join(row[1] for row in rows).encode("utf-8"))
    provenance = f"synthetic-fixture-generator:v1;records_sha256={fixture_digest}"
    for prediction_id, row in enumerate(rows, 1):
        label = "phishing" if (prediction_id - 1) % 2 == 0 else "legitimate"
        if not record_feedback(prediction_id, label, db_path):
            raise RuntimeError(f"failed to seed synthetic feedback row {prediction_id}")
        review_label(prediction_id, "verify", REVIEWER, provenance, label,
                     "Synthetic label follows the fixture generator rule; not evidence about real URLs.", db_path)
    return {"record_count": record_count, "per_class": {"phishing": record_count // 2, "legitimate": record_count // 2},
            "fixture_sha256": fixture_digest, "reviewer": REVIEWER, "provenance": provenance}


def _make_synthetic_evaluation_fixture(run_dir: Path, training_urls: list[str]) -> dict:
    eval_dir = run_dir / "evaluation-fixture"
    eval_dir.mkdir()
    lines = []
    evaluation_urls = []
    for i in range(40):
        label = "phishing" if i % 2 == 0 else "legitimate"
        url = f"https://eval-{label}-{i:04d}.synthetic.invalid/case/{i:04d}"
        lines.append(f"{label}\t{url}\n")
        evaluation_urls.append(url)
    tsv = eval_dir / "evaluation.tsv"
    data = "".join(lines).encode("utf-8")
    tsv.write_bytes(data)
    # Match the existing evaluator's fingerprint contract (integer labels in
    # class order, followed by the URL), while retaining label text in the TSV.
    evaluation_fingerprint = _sha256_bytes("".join(
        f"{0 if i % 2 == 0 else 1}\t{url}\n" for i, url in enumerate(evaluation_urls)
    ).encode("utf-8"))
    training_hosts = {(urlsplit(url).hostname or "").lower() for url in training_urls}
    evaluation_hosts = {(urlsplit(url).hostname or "").lower() for url in evaluation_urls}
    manifest = {
        "scope": "synthetic-sandbox-demo-only", "synthetic_fixture": True,
        "source_reference": "generated by scripts/demo_retraining_sandbox.py; reserved .invalid domains",
        "dataset_fingerprint": evaluation_fingerprint,
        "record_count": len(evaluation_urls),
        "training_fixture_exact_url_overlap_count": len(set(training_urls) & set(evaluation_urls)),
        "training_fixture_exact_host_overlap_count": len(training_hosts & evaluation_hosts),
        "trusted_ground_truth": False,
        "independent": False,
        "domain_overlap_reviewed": False,
        "training_url_overlap_count": None,
        "training_domain_overlap_count": None,
        "training_dataset_fingerprint": None,
        "training_validation_fingerprint": None,
        "incumbent_training_history_verified": False,
        "incumbent_training_history_reference": None,
        "label_provenance": "synthetic generator rule only; not real-world ground truth",
        "evaluation_decision": "not_evaluated",
    }
    (eval_dir / "evaluation-manifest.json").write_text(json.dumps(manifest, indent=2), encoding="utf-8")
    policy = {
        "scope": "synthetic-sandbox-demo-only", "synthetic_fixture": True,
        "enabled": False, "version": "unapproved-demo-placeholder",
        "thresholds": {},
        "reason": "No approved evaluation thresholds were supplied; do not use for candidate acceptance or promotion.",
    }
    (eval_dir / "policy.json").write_text(json.dumps(policy, indent=2), encoding="utf-8")
    return {"records": len(evaluation_urls), "url_overlap_with_synthetic_feedback": manifest["training_fixture_exact_url_overlap_count"],
            "host_overlap_with_synthetic_feedback": manifest["training_fixture_exact_host_overlap_count"],
            "manifest": "evaluation-fixture/evaluation-manifest.json",
            "policy": "evaluation-fixture/policy.json", "evaluation": "not_run"}


def _safe_dvc_blocker(_project_root: Path) -> dict:
    # The normal diagnostic can create a cache in the project working tree.
    # The sandbox therefore fails closed without running DVC outside %TEMP%.
    return {"available": False, "ok": False,
            "error": "not_run_by_sandbox_to_keep_all_generated_diagnostic_state_inside_temp"}


def build_demo(run_dir: Path, project_root: Path = ROOT, record_count: int = RECORDS,
               worker_preflight: bool = True) -> dict:
    from mlops.data import lifecycle
    from mlops.retraining.worker import run_worker

    protected_before = {str(path): _sha256_file(path) for path in _protected_paths(project_root)}
    db_path = run_dir / "feedback.sqlite"
    batch_root = run_dir / "registered-batches"
    seeded = _seed_database(db_path, record_count)
    with lifecycle.connect(db_path) as conn:
        training_urls = [row[0] for row in conn.execute("SELECT url FROM predictions ORDER BY id")]
    evaluation = _make_synthetic_evaluation_fixture(run_dir, training_urls)

    saved_auto = os.environ.get("RETRAIN_AUTO_ENABLED")
    os.environ["RETRAIN_AUTO_ENABLED"] = "true"
    try:
        scheduled = lifecycle.scheduled_review(db_path, min_records=record_count,
            min_per_class=min(MIN_PER_CLASS, record_count // 2), interval_hours=876000,
            cooldown_days=0)
    finally:
        if saved_auto is None:
            os.environ.pop("RETRAIN_AUTO_ENABLED", None)
        else:
            os.environ["RETRAIN_AUTO_ENABLED"] = saved_auto
    if not scheduled.get("job_id"):
        raise RuntimeError(f"synthetic fixture did not create an eligible sandbox job: {scheduled}")
    batch = lifecycle.export_batch(db_path, batch_root)
    if not batch.get("created"):
        raise RuntimeError(f"synthetic verified feedback did not export to a new batch: {batch}")

    worker_result = None
    training_status = "not_checked"
    if worker_preflight:
        worker_result = run_worker(scheduled["job_id"], execute=False, db_path=db_path,
            batch_root=batch_root, dataset=project_root / "dataset" / "big_dataset",
            validation_report=project_root / "mlops" / "dvc" / "validation_report.json",
            provenance_manifest=None, candidate_root=run_dir / "candidate-artifacts",
            dvc_check=_safe_dvc_blocker)
        training_status = "blocked_before_training" if worker_result["decision"] == "BLOCKED" else "dry_run_only"
        lifecycle.update_job(scheduled["job_id"], "blocked" if worker_result["decision"] == "BLOCKED" else "eligible",
            details={"sandbox_demo": True, "synthetic_only": True,
                     "worker_dry_run_decision": worker_result["decision"],
                     "worker_blockers": worker_result.get("blockers", [])},
            error="; ".join(worker_result.get("blockers", [])) if worker_result["decision"] == "BLOCKED" else None,
            db_path=db_path)

    with lifecycle.connect(db_path) as conn:
        counts = {
            "predictions": conn.execute("SELECT COUNT(*) FROM predictions").fetchone()[0],
            "feedback": conn.execute("SELECT COUNT(*) FROM predictions WHERE feedback IS NOT NULL").fetchone()[0],
            "pending_verification": conn.execute("SELECT COUNT(*) FROM predictions WHERE verification_status='pending_verification'").fetchone()[0],
            "verified": conn.execute("SELECT COUNT(*) FROM predictions WHERE verification_status='verified'").fetchone()[0],
            "training_eligible": conn.execute("SELECT COUNT(*) FROM predictions WHERE training_eligible=1").fetchone()[0],
            "batches": conn.execute("SELECT COUNT(*) FROM training_batches").fetchone()[0],
            "jobs": [dict(row) for row in conn.execute("SELECT status,COUNT(*) AS count FROM retraining_jobs GROUP BY status")],
        }
    protected_after = {str(path): _sha256_file(path) for path in _protected_paths(project_root)}
    summary = {
        "scope": "synthetic sandbox; not evidence of model quality",
        "sandbox": str(run_dir), "database": str(db_path), "counts": counts,
        "synthetic_review": seeded,
        "batch": {"created": True, "batch_id": batch["batch_id"], "record_count": batch["record_count"],
                  "label_distribution": batch["label_distribution"], "sha256": batch["sha256"],
                  "directory": batch["directory"]},
        "candidate_training": training_status,
        "worker_blockers": worker_result.get("blockers", []) if worker_result else [],
        "evaluation_fixture": evaluation,
        "evaluation_metrics": None,
        "approval": "not_applicable_no_evaluated_candidate",
        "promotion": "not_attempted",
        "protected_live_artifacts_unchanged": protected_before == protected_after,
        "protected_live_artifacts_sha256_before": protected_before,
        "protected_live_artifacts_sha256_after": protected_after,
        "notes": [
            "Synthetic review entries exercise the lifecycle API; they are not human verification or real URL evidence.",
            "The worker only ran execute=False. No candidate training, evaluation, approval, or promotion occurred.",
            "The evaluation fixture is explicitly untrusted and its policy has no thresholds; the evaluator was not run.",
            "The real workspace DVC diagnostic was not run because it can create state outside this sandbox.",
        ],
    }
    summary_path = run_dir / "status.json"
    summary_path.write_text(json.dumps(summary, indent=2, default=str), encoding="utf-8")
    _write_manifest(run_dir)
    return summary


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--reset", action="store_true",
                        help="remove only files listed by prior sandbox-generated run manifests")
    args = parser.parse_args(argv)
    try:
        root = _prepare_root(SANDBOX_ROOT, TEMP_ROOT, ROOT, args.reset)
        run_dir = _create_run_dir(root)
        summary = build_demo(run_dir)
    except Exception as exc:
        print(json.dumps({"demo": "failed_safely", "error": f"{type(exc).__name__}: {exc}"}, indent=2), file=sys.stderr)
        return 2
    print(json.dumps({
        "sandbox": summary["sandbox"],
        "database": summary["database"],
        "counts": summary["counts"],
        "batch": summary["batch"],
        "candidate_training": summary["candidate_training"],
        "worker_blockers": summary["worker_blockers"],
        "evaluation_fixture": summary["evaluation_fixture"],
        "evaluation_metrics": None,
        "approval": summary["approval"],
        "promotion": summary["promotion"],
        "protected_live_artifacts_unchanged": summary["protected_live_artifacts_unchanged"],
        "note": "Synthetic lifecycle demonstration only; no candidate model was trained or promoted.",
    }, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
