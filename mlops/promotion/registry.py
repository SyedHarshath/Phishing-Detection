"""Windows-safe local model registry with atomic active-manifest switching.

Promotion writes immutable version directories and atomically replaces one
small active manifest. Serving processes read it at startup; they do not hot
reload. Real promotion stays disabled unless explicit config and CLI consent
are both supplied.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import math
import os
import errno
import re
import shutil
import tempfile
import uuid
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path

from mlops.retraining.worker import _sha
from mlops.dvc.diagnostics import diagnose

ROOT = Path(__file__).resolve().parents[2]
DEFAULT_REGISTRY = ROOT / "artifacts" / "model_registry"
VERSION_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,127}$")


def _now():
    return datetime.now(timezone.utc).isoformat()


def _json(path: Path):
    return json.loads(path.read_text(encoding="utf-8"))


def _atomic_json(path: Path, value: dict):
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp_name = tempfile.mkstemp(prefix=f".{path.name}-", suffix=".tmp", dir=path.parent)
    try:
        with os.fdopen(fd, "w", encoding="utf-8", newline="\n") as stream:
            json.dump(value, stream, indent=2)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(tmp_name, path)
    finally:
        if os.path.exists(tmp_name):
            os.unlink(tmp_name)


@contextmanager
def _exclusive_lock(root: Path):
    root.mkdir(parents=True, exist_ok=True)
    lock = root / ".promotion.lock"
    handle = lock.open("a+b")
    try:
        handle.seek(0, os.SEEK_END)
        if handle.tell() == 0:
            handle.write(b"0"); handle.flush()
        handle.seek(0)
        if os.name == "nt":
            import msvcrt
            try:
                msvcrt.locking(handle.fileno(), msvcrt.LK_NBLCK, 1)
            except OSError as exc:
                raise RuntimeError("another registry operation is in progress") from exc
        else:
            import fcntl
            try:
                fcntl.flock(handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
            except OSError as exc:
                if exc.errno in (errno.EACCES, errno.EAGAIN):
                    raise RuntimeError("another registry operation is in progress") from exc
                raise
    except Exception:
        handle.close()
        raise
    try:
        yield
    finally:
        handle.seek(0)
        if os.name == "nt":
            import msvcrt
            msvcrt.locking(handle.fileno(), msvcrt.LK_UNLCK, 1)
        else:
            import fcntl
            fcntl.flock(handle.fileno(), fcntl.LOCK_UN)
        handle.close()


def verify_bundle(bundle_dir: str | Path, *, require_real: bool = True, loader=None) -> dict:
    directory = Path(bundle_dir)
    if not directory.is_dir():
        raise FileNotFoundError(f"bundle directory missing: {directory}")
    manifest_path = directory / "bundle.json"
    manifest = _json(manifest_path)
    if manifest.get("bundle_schema_version") != 1:
        raise ValueError("unsupported or missing bundle schema version")
    if require_real and (manifest.get("test_only") is not False or manifest.get("bundle_type") != "real_candidate"):
        raise ValueError("test-only or non-candidate bundle cannot be promoted")
    if require_real:
        metadata = manifest.get("metadata") or {}
        registration_sha = metadata.get("source_registration_sha256")
        if (metadata.get("source_provenance_verified") is not True
                or metadata.get("authoritative_registration") is not True
                or metadata.get("candidate_origin") != "approved_dataset_import"
                or not isinstance(registration_sha, str) or len(registration_sha) != 64
                or any(char not in "0123456789abcdef" for char in registration_sha.lower())):
            raise ValueError("candidate lacks an approved dataset source registration")
    if manifest.get("class_order") != ["phishing", "legitimate"]:
        raise ValueError("bundle class order is incompatible")
    if manifest.get("label_mapping") != {"phishing": 0, "legitimate": 1}:
        raise ValueError("bundle label mapping is incompatible")
    if manifest.get("sequence_length") != 200 or manifest.get("preprocessing_version") != "url-character-lowercase-pad200-v1":
        raise ValueError("bundle preprocessing contract is incompatible")
    if not manifest.get("model_version") or not manifest.get("candidate_id"):
        raise ValueError("bundle identifiers are missing")
    for field in ("model_version", "candidate_id"):
        if not VERSION_RE.fullmatch(str(manifest[field])):
            raise ValueError(f"invalid bundle {field}")
    files = manifest.get("files") or {}
    model_path, tokenizer_path = directory / "best_model.h5", directory / "tokenizer.json"
    for name, path in (("best_model.h5", model_path), ("tokenizer.json", tokenizer_path)):
        if path.resolve().parent != directory.resolve() or not path.is_file() or files.get(name) != _sha(path):
            raise ValueError(f"missing or corrupt artifact: {name}")
    if model_path.suffix.lower() != ".h5" or tokenizer_path.suffix.lower() != ".json":
        raise ValueError("unsupported model or tokenizer format")
    predictor = (loader or _load_predictor)(model_path, tokenizer_path)
    if getattr(predictor, "categories", None) != ["phishing", "legitimate"]:
        raise ValueError("loaded model output order is incompatible")
    if getattr(predictor, "sequence_length", None) != 200:
        raise ValueError("loaded model sequence length is incompatible")
    tokenizer = getattr(predictor, "tokenizer", None)
    if tokenizer is not None and (getattr(tokenizer, "char_level", None) is not True
                                  or getattr(tokenizer, "lower", None) is not True
                                  or getattr(tokenizer, "oov_token", None) != "-n-"):
        raise ValueError("tokenizer configuration is incompatible")
    compatibility = manifest.get("compatibility") or {}
    if compatibility.get("model_format") != "keras-h5" or compatibility.get("tokenizer_format") != "keras-json":
        raise ValueError("bundle format compatibility metadata is missing or unsupported")
    model = getattr(predictor, "model", None)
    if model is not None:
        input_shape = getattr(model, "input_shape", None)
        output_shape = getattr(model, "output_shape", None)
        if input_shape and input_shape[-1] != 200:
            raise ValueError("model input shape does not match the 200-character contract")
        if output_shape and output_shape[-1] != 2:
            raise ValueError("model output shape does not match the two-class contract")
        word_index = getattr(tokenizer, "word_index", {}) if tokenizer is not None else {}
        embedding_layers = [layer for layer in getattr(model, "layers", [])
                            if layer.__class__.__name__ == "Embedding"]
        if embedding_layers and word_index and max(word_index.values()) >= embedding_layers[0].input_dim:
            raise ValueError("tokenizer vocabulary exceeds the model embedding table")
    # Isolated, deterministic-shaped inference smoke test; no network or services.
    if hasattr(predictor, "predict"):
        result = predictor.predict("https://optimus-promotion-check.invalid/path")
        if (not isinstance(result, dict) or result.get("prediction") not in {"phishing", "legitimate"}
                or not isinstance(result.get("confidence"), (int, float))
                or not math.isfinite(result["confidence"]) or not 0 <= result["confidence"] <= 100):
            raise ValueError("candidate failed isolated inference smoke test")
    return {"manifest": manifest, "bundle_sha256": _sha(manifest_path),
            "model_sha256": _sha(model_path), "tokenizer_sha256": _sha(tokenizer_path),
            "directory": str(directory)}


def _load_predictor(model_path: Path, tokenizer_path: Path):
    from ml.prediction import PhishingPredictor
    return PhishingPredictor(model_path, tokenizer_path)


def _registry(root: Path) -> dict:
    path = root / "registry.json"
    if not path.is_file():
        raise FileNotFoundError(f"registry not initialized: {path}")
    return _json(path)


def _active(root: Path) -> dict:
    path = root / "active.json"
    if not path.is_file():
        raise FileNotFoundError(f"active model manifest not initialized: {path}")
    active = _json(path)
    bundle_dir = (root / active["bundle_path"]).resolve()
    if root.resolve() not in bundle_dir.parents:
        raise ValueError("active bundle path escapes registry root")
    if not bundle_dir.is_dir():
        raise FileNotFoundError("active bundle directory missing")
    return {**active, "bundle_dir": bundle_dir}


def resolve_active_paths(manifest_path: str | Path) -> tuple[Path, Path, str]:
    """Resolve active artifact pair safely for startup-time inference loading."""
    path = Path(manifest_path).resolve()
    active = _json(path)
    root = path.parent.resolve()
    bundle = (root / active["bundle_path"]).resolve()
    if root not in bundle.parents:
        raise ValueError("active bundle path escapes registry root")
    return bundle / "best_model.h5", bundle / "tokenizer.json", str(active["model_version"])


def initialize_registry(root: str | Path, incumbent_bundle: str | Path, *, loader=None) -> dict:
    """Initialize an isolated registry from a verified bundle; never overwrites."""
    root = Path(root)
    root.mkdir(parents=True, exist_ok=True)
    checked = verify_bundle(incumbent_bundle, require_real=False, loader=loader)
    version = checked["manifest"]["model_version"]
    with _exclusive_lock(root):
        if (root / "registry.json").exists() or (root / "active.json").exists():
            raise FileExistsError("registry already initialized")
        target = root / "versions" / version
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copytree(incumbent_bundle, target)
        registry = {"schema_version": 1, "versions": {version: {
            "status": "production", "bundle_path": str(Path("versions") / version),
            "bundle_sha256": checked["bundle_sha256"], "model_sha256": checked["model_sha256"],
            "tokenizer_sha256": checked["tokenizer_sha256"], "registered_at": _now()}},
            "history": []}
        active = {"schema_version": 1, "model_version": version,
                  "bundle_path": str(Path("versions") / version), "previous_version": None,
                  "updated_at": _now()}
        _atomic_json(root / "registry.json", registry)
        _atomic_json(root / "active.json", active)
        return {"initialized": True, "active": active}


def bootstrap_legacy_incumbent(root: str | Path = DEFAULT_REGISTRY, *,
                               model_path: str | Path = ROOT / "test_results" / "cnn_bilstm" / "best_model.h5",
                               tokenizer_path: str | Path = ROOT / "ml" / "artifacts" / "tokenizer.json",
                               loader=None) -> dict:
    """Copy the current matched production pair into a new registry; no live writes."""
    root = Path(root)
    temp_bundle = root / ".bootstrap-legacy-incumbent"
    if temp_bundle.exists():
        raise FileExistsError(f"bootstrap staging path exists; inspect before removing: {temp_bundle}")
    temp_bundle.mkdir(parents=True)
    model_path, tokenizer_path = Path(model_path), Path(tokenizer_path)
    try:
        shutil.copy2(model_path, temp_bundle / "best_model.h5")
        shutil.copy2(tokenizer_path, temp_bundle / "tokenizer.json")
        manifest = {"bundle_schema_version": 1, "candidate_id": "legacy-incumbent",
            "model_version": "legacy-incumbent", "bundle_type": "legacy_incumbent",
            "role": "production-incumbent", "test_only": False,
            "class_order": ["phishing", "legitimate"],
            "label_mapping": {"phishing": 0, "legitimate": 1},
            "sequence_length": 200,
            "preprocessing_version": "url-character-lowercase-pad200-v1",
            "architecture": "existing production model; original training metadata unavailable",
            "training_dataset_fingerprint": None, "training_run_id": None,
            "created_at_utc": _now(),
            "compatibility": {"tokenizer_format": "keras-json", "model_format": "keras-h5"},
            "files": {"best_model.h5": _sha(temp_bundle / "best_model.h5"),
                      "tokenizer.json": _sha(temp_bundle / "tokenizer.json")}}
        _atomic_json(temp_bundle / "bundle.json", manifest)
        return initialize_registry(root, temp_bundle, loader=loader)
    finally:
        if temp_bundle.exists():
            shutil.rmtree(temp_bundle)


def inspect_registry(root: str | Path = DEFAULT_REGISTRY) -> dict:
    root = Path(root)
    return {"registry": _registry(root), "active": _active(root)}


def recover_registry(root: str | Path = DEFAULT_REGISTRY, *, loader=None) -> dict:
    """Reconcile registry metadata after a process stops around the atomic switch."""
    root = Path(root)
    with _exclusive_lock(root):
        if not (root / "active.json").is_file():
            registry = _registry(root)
            current = [name for name, record in registry.get("versions", {}).items()
                       if record.get("status") == "production"]
            if len(current) != 1:
                return {"decision": "BLOCKED", "reason": "cannot_recover_unique_production_version"}
            version = current[0]
            item = registry["versions"][version]
            bundle_dir = (root / item["bundle_path"]).resolve()
            if root.resolve() not in bundle_dir.parents:
                return {"decision": "BLOCKED", "reason": "registry_path_escapes_root"}
            checked = verify_bundle(bundle_dir, require_real=False, loader=loader)
            if checked["bundle_sha256"] != item.get("bundle_sha256"):
                return {"decision": "BLOCKED", "reason": "registry_bundle_checksum_mismatch"}
            active = {"schema_version": 1, "model_version": version,
                      "bundle_path": item["bundle_path"], "previous_version": None,
                      "updated_at": _now()}
            _atomic_json(root / "active.json", active)
            return {"decision": "RECOVERED", "active_version": version, "history_reconciled": False}
        active = _active(root)
        checked = verify_bundle(active["bundle_dir"], require_real=False, loader=loader)
        registry = _registry(root)
        version = active["model_version"]
        if version not in registry["versions"]:
            registry["versions"][version] = {"status": "production",
                "bundle_path": active["bundle_path"], "bundle_sha256": checked["bundle_sha256"],
                "model_sha256": checked["model_sha256"], "tokenizer_sha256": checked["tokenizer_sha256"],
                "registered_at": active.get("updated_at", _now())}
        for key, item in registry["versions"].items():
            item["status"] = "production" if key == version else (
                "previous_production" if key == active.get("previous_version") else "available")
        operation = active.get("last_operation")
        if operation and not any(event.get("operation_id") == operation.get("operation_id")
                                 for event in registry.get("history", [])):
            registry.setdefault("history", []).append({**operation, "recovered": True})
        _atomic_json(root / "registry.json", registry)
        return {"decision": "RECOVERED", "active_version": version,
                "history_reconciled": bool(operation), "bundle_sha256": checked["bundle_sha256"]}


def _policy_hash(policy: dict) -> str:
    return hashlib.sha256(json.dumps(policy, sort_keys=True, separators=(",", ":")).encode()).hexdigest()


def _policy_ready(policy: dict) -> bool:
    thresholds = policy.get("thresholds") or {}
    required = {"max_phishing_fnr", "max_legitimate_fpr", "min_phishing_recall",
                "min_legitimate_recall", "min_phishing_precision", "min_legitimate_precision",
                "max_phishing_fnr_regression", "max_legitimate_fpr_regression", "max_latency_ratio"}
    return policy.get("enabled") is True and bool(policy.get("version")) and required.issubset(thresholds) and all(
        isinstance(thresholds[key], (int, float)) and math.isfinite(thresholds[key]) for key in required)


def promote(candidate_dir: str | Path, evaluation_report: str | Path,
            policy_path: str | Path, provenance_report: str | Path,
            *, root: str | Path = DEFAULT_REGISTRY, enabled: bool = False,
            confirm: bool = False, dvc_status: dict | None = None, loader=None) -> dict:
    root, candidate_dir = Path(root), Path(candidate_dir)
    if not enabled:
        return {"decision": "BLOCKED", "reason": "promotion_disabled_by_configuration"}
    if not confirm:
        return {"decision": "BLOCKED", "reason": "operator_confirmation_required"}
    try:
        checked = verify_bundle(candidate_dir, require_real=True, loader=loader)
        report, policy, provenance = _json(Path(evaluation_report)), _json(Path(policy_path)), _json(Path(provenance_report))
    except Exception as exc:
        return {"decision": "BLOCKED", "reason": f"preflight_failed:{type(exc).__name__}:{exc}"}
    if not _policy_ready(policy):
        return {"decision": "BLOCKED", "reason": "current_policy_missing_thresholds_or_disabled"}
    if report.get("decision") != "PASS" or report.get("policy_version") != policy.get("version"):
        return {"decision": "BLOCKED", "reason": "evaluation_report_not_pass_or_policy_version_mismatch"}
    if report.get("policy_sha256") != _policy_hash(policy):
        return {"decision": "BLOCKED", "reason": "evaluation_report_policy_fingerprint_mismatch"}
    if report.get("candidate_id") != checked["manifest"].get("candidate_id"):
        return {"decision": "BLOCKED", "reason": "evaluation_report_candidate_mismatch"}
    if report.get("candidate_bundle_sha256") != checked["bundle_sha256"]:
        return {"decision": "BLOCKED", "reason": "evaluation_report_bundle_mismatch"}
    if not all(report.get("evaluation_independence", {}).get(k) is True
               for k in ("trusted_ground_truth", "independent", "domain_overlap_reviewed",
                         "incumbent_training_history_verified")):
        return {"decision": "BLOCKED", "reason": "evaluation_independence_not_established"}
    if (not report.get("dataset_fingerprint")
            or report.get("dataset_fingerprint") == provenance.get("training_validation_fingerprint")
            or report.get("training_dataset_fingerprint") != checked["manifest"].get("training_dataset_fingerprint")
            or report.get("training_validation_fingerprint") != provenance.get("training_validation_fingerprint")):
        return {"decision": "BLOCKED", "reason": "evaluation_data_is_missing_or_not_independent_of_validation"}
    metrics = report.get("candidate_metrics")
    required_metrics = ["accuracy", "balanced_accuracy", "phishing_false_negative_rate",
                        "legitimate_false_positive_rate", "mean_max_confidence",
                        "latency_seconds", "latency_seconds_per_example", "model_size_bytes"]
    if not isinstance(metrics, dict) or any(not isinstance(metrics.get(key), (int, float))
            or not math.isfinite(metrics[key]) for key in required_metrics):
        return {"decision": "BLOCKED", "reason": "required_evaluation_metrics_missing_or_non_finite"}
    per_class = metrics.get("per_class") or {}
    if any(not isinstance(per_class.get(cls), dict) or any(
            not isinstance(per_class[cls].get(key), (int, float)) or not math.isfinite(per_class[cls][key])
            for key in ("precision", "recall", "f1", "support"))
            for cls in ("phishing", "legitimate")):
        return {"decision": "BLOCKED", "reason": "per_class_metrics_missing_or_non_finite"}
    if not metrics.get("confusion_matrix_labels_phishing_legitimate") or not report.get("evaluation_manifest_sha256"):
        return {"decision": "BLOCKED", "reason": "confusion_matrix_or_evaluation_manifest_fingerprint_missing"}
    if (not checked["manifest"].get("training_dataset_fingerprint")
            or provenance.get("passed") is not True
            or provenance.get("dataset_fingerprint") != checked["manifest"].get("training_dataset_fingerprint")):
        return {"decision": "BLOCKED", "reason": "training_data_provenance_unverified"}
    candidate_metadata = checked["manifest"].get("metadata") or {}
    if (provenance.get("source_provenance_verified") is not True
            or provenance.get("authoritative_registration") is not True
            or not provenance.get("source_registration_sha256")
            or candidate_metadata.get("source_provenance_verified") is not True
            or candidate_metadata.get("authoritative_registration") is not True
            or candidate_metadata.get("candidate_origin") != "approved_dataset_import"
            or candidate_metadata.get("source_registration_sha256") != provenance.get("source_registration_sha256")):
        return {"decision": "BLOCKED", "reason": "approved_dataset_source_registration_missing_or_mismatched"}
    if not provenance.get("validation_passed") or provenance.get("dvc_clean") is not True:
        return {"decision": "BLOCKED", "reason": "baseline_validation_or_dvc_provenance_failed"}
    dvc_status = dvc_status or diagnose(ROOT)
    if not dvc_status or dvc_status.get("ok") is not True or dvc_status.get("stdout", "").strip() or dvc_status.get("stderr", "").strip():
        return {"decision": "BLOCKED", "reason": "current_read_only_dvc_status_not_clean"}

    with _exclusive_lock(root):
        active = _active(root)
        incumbent_info = verify_bundle(active["bundle_dir"], require_real=False, loader=loader)
        if report.get("incumbent_id") != active["model_version"]:
            return {"decision": "BLOCKED", "reason": "evaluation_report_incumbent_mismatch"}
        if report.get("incumbent_bundle_sha256") != incumbent_info["bundle_sha256"]:
            return {"decision": "BLOCKED", "reason": "evaluation_report_incumbent_bundle_mismatch"}
        version = checked["manifest"]["model_version"]
        relative = Path("versions") / version
        target = root / relative
        if target.exists():
            # A complete directory can remain if the prior process stopped just
            # before the atomic active-manifest switch. Reuse only exact bytes.
            try:
                copied = verify_bundle(target, require_real=True, loader=loader)
            except Exception as exc:
                return {"decision": "BLOCKED", "reason": f"existing_version_invalid:{exc}"}
            if copied["bundle_sha256"] != checked["bundle_sha256"]:
                return {"decision": "BLOCKED", "reason": "model_version_already_registered_with_different_bytes"}
            if version in _registry(root)["versions"]:
                return {"decision": "BLOCKED", "reason": "model_version_already_registered"}
        else:
            target.parent.mkdir(parents=True, exist_ok=True)
            staging = target.parent / f".{version}-{uuid.uuid4().hex}.staging"
            try:
                shutil.copytree(candidate_dir, staging)
                # Recheck copied bytes before making the immutable version visible.
                copied = verify_bundle(staging, require_real=True, loader=loader)
                if copied["bundle_sha256"] != checked["bundle_sha256"]:
                    raise ValueError("staged bundle differs from verified candidate")
                os.replace(staging, target)
            finally:
                if staging.exists():
                    shutil.rmtree(staging, ignore_errors=True)
        report_sha = _sha(Path(evaluation_report))
        history_event = {"action": "promote", "from_version": active["model_version"],
                         "to_version": version, "at": _now(), "reason": "evaluation PASS",
                         "evaluation_report": str(Path(evaluation_report).resolve()),
                         "evaluation_report_sha256": report_sha, "policy_version": policy["version"],
                         "bundle_sha256": checked["bundle_sha256"]}
        next_active = {"schema_version": 1, "model_version": version,
                       "bundle_path": str(relative), "previous_version": active["model_version"],
                       "updated_at": history_event["at"],
                       "last_operation": {**history_event, "operation_id": str(uuid.uuid4())}}
        history_event = next_active["last_operation"]
        _atomic_json(root / "active.json", next_active)  # single-file atomic switch
        registry = _registry(root)
        registry["versions"][version] = {"status": "production", "bundle_path": str(relative),
            "bundle_sha256": checked["bundle_sha256"], "model_sha256": checked["model_sha256"],
            "tokenizer_sha256": checked["tokenizer_sha256"], "registered_at": history_event["at"],
            "evaluation_report_sha256": report_sha}
        registry["versions"][active["model_version"]]["status"] = "previous_production"
        registry["history"].append(history_event)
        _atomic_json(root / "registry.json", registry)
        return {"decision": "PROMOTED", "active": next_active, "history": history_event,
                "restart_required": True, "hot_reload": False}


def rollback(*, root: str | Path = DEFAULT_REGISTRY, reason: str,
             confirm: bool = False, loader=None) -> dict:
    root = Path(root)
    if not confirm:
        return {"decision": "BLOCKED", "reason": "operator_confirmation_required"}
    if not reason.strip():
        return {"decision": "BLOCKED", "reason": "rollback_reason_required"}
    with _exclusive_lock(root):
        active = _active(root)
        target_version = active.get("previous_version")
        if not target_version:
            return {"decision": "BLOCKED", "reason": "no_previous_known_good_version"}
        registry = _registry(root)
        target_info = registry["versions"].get(target_version)
        if not target_info:
            return {"decision": "BLOCKED", "reason": "previous_version_missing_from_registry"}
        target_dir = (root / target_info["bundle_path"]).resolve()
        if root.resolve() not in target_dir.parents:
            return {"decision": "BLOCKED", "reason": "rollback_registry_path_escapes_root"}
        try:
            checked = verify_bundle(target_dir, require_real=False, loader=loader)
        except Exception as exc:
            return {"decision": "BLOCKED", "reason": f"rollback_target_invalid:{type(exc).__name__}:{exc}"}
        if checked["bundle_sha256"] != target_info["bundle_sha256"]:
            return {"decision": "BLOCKED", "reason": "rollback_target_registry_checksum_mismatch"}
        source = active["model_version"]
        timestamp = _now()
        event = {"action": "rollback", "from_version": source, "to_version": target_version,
                 "at": timestamp, "reason": reason.strip(), "bundle_sha256": checked["bundle_sha256"]}
        new_active = {"schema_version": 1, "model_version": target_version,
                      "bundle_path": target_info["bundle_path"], "previous_version": source,
                      "updated_at": timestamp,
                      "last_operation": {**event, "operation_id": str(uuid.uuid4())}}
        event = new_active["last_operation"]
        _atomic_json(root / "active.json", new_active)
        registry["versions"][source]["status"] = "rollback_source"
        registry["versions"][target_version]["status"] = "production"
        registry["history"].append(event)
        _atomic_json(root / "registry.json", registry)
        resolved = _active(root)
        return {"decision": "ROLLED_BACK", "active_version": resolved["model_version"],
                "history": event, "restart_required": True, "hot_reload": False}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest="command", required=True)
    inspect = sub.add_parser("inspect"); inspect.add_argument("--registry", type=Path, default=DEFAULT_REGISTRY)
    bootstrap = sub.add_parser("bootstrap-legacy")
    bootstrap.add_argument("--registry", type=Path, default=DEFAULT_REGISTRY)
    bootstrap.add_argument("--model", type=Path, default=ROOT / "test_results" / "cnn_bilstm" / "best_model.h5")
    bootstrap.add_argument("--tokenizer", type=Path, default=ROOT / "ml" / "artifacts" / "tokenizer.json")
    promote_cmd = sub.add_parser("promote")
    promote_cmd.add_argument("candidate", type=Path); promote_cmd.add_argument("evaluation_report", type=Path)
    promote_cmd.add_argument("policy", type=Path); promote_cmd.add_argument("provenance_report", type=Path)
    promote_cmd.add_argument("--registry", type=Path, default=DEFAULT_REGISTRY)
    promote_cmd.add_argument("--enable-promotion", action="store_true")
    promote_cmd.add_argument("--confirm", action="store_true")
    rollback_cmd = sub.add_parser("rollback"); rollback_cmd.add_argument("--registry", type=Path, default=DEFAULT_REGISTRY)
    rollback_cmd.add_argument("--reason", required=True); rollback_cmd.add_argument("--confirm", action="store_true")
    recovery_cmd = sub.add_parser("recover"); recovery_cmd.add_argument("--registry", type=Path, default=DEFAULT_REGISTRY)
    args = parser.parse_args()
    if args.command == "inspect":
        result = inspect_registry(args.registry)
    elif args.command == "recover":
        result = recover_registry(args.registry)
    elif args.command == "bootstrap-legacy":
        result = bootstrap_legacy_incumbent(args.registry, model_path=args.model, tokenizer_path=args.tokenizer)
    elif args.command == "promote":
        result = promote(args.candidate, args.evaluation_report, args.policy, args.provenance_report,
                         root=args.registry,
                         enabled=(args.enable_promotion and os.getenv("PHISHGUARD_PROMOTION_ENABLED", "false").lower() == "true"),
                         confirm=args.confirm)
    else:
        result = rollback(root=args.registry, reason=args.reason, confirm=args.confirm)
    print(json.dumps(result, indent=2, default=str))
    return 0 if result.get("decision") in {"PROMOTED", "ROLLED_BACK", "RECOVERED"} or "registry" in result else 2


if __name__ == "__main__":
    raise SystemExit(main())
