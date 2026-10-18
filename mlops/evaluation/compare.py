"""Policy-driven comparison of two models on the same frozen examples.

This module accepts already-computed probabilities so its decision logic can be
tested without loading TensorFlow or touching production artifacts.
"""
from __future__ import annotations

import hashlib
import json
import math
import time
from pathlib import Path

import numpy as np
from sklearn.metrics import accuracy_score, balanced_accuracy_score, confusion_matrix, precision_recall_fscore_support

from mlops.evaluation.thresholds import guarded_threshold_tradeoff, threshold_tradeoff


def dataset_fingerprint(labels: list[int], urls: list[str]) -> str:
    payload = "".join(f"{label}\t{url}\n" for label, url in zip(labels, urls)).encode("utf-8")
    return hashlib.sha256(payload).hexdigest()


def _metrics(labels, probabilities, latency_seconds=0.0, model_size_bytes=None):
    y = np.asarray(labels, dtype=np.int64)
    p = np.asarray(probabilities, dtype=float)
    if p.shape != (len(y), 2) or not np.isfinite(p).all() or (p < 0).any():
        raise ValueError("probabilities must be a finite N x 2 matrix")
    if not np.allclose(p.sum(axis=1), 1, atol=1e-3):
        raise ValueError("each probability row must sum to one")
    if not math.isfinite(float(latency_seconds)) or latency_seconds < 0:
        raise ValueError("latency must be finite and non-negative")
    if model_size_bytes is not None and (not isinstance(model_size_bytes, (int, float))
                                         or not math.isfinite(model_size_bytes) or model_size_bytes < 0):
        raise ValueError("model size must be finite and non-negative")
    pred = p.argmax(axis=1)
    cm = confusion_matrix(y, pred, labels=[0, 1])
    precision, recall, f1, support = precision_recall_fscore_support(y, pred, labels=[0, 1], zero_division=0)
    phishing_n = int(cm[0].sum())
    legitimate_n = int(cm[1].sum())
    return {
        "confusion_matrix_labels_phishing_legitimate": cm.tolist(),
        "per_class": {name: {"precision": float(precision[i]), "recall": float(recall[i]),
                             "f1": float(f1[i]), "support": int(support[i])}
                      for i, name in enumerate(("phishing", "legitimate"))},
        "phishing_false_negative_rate": float(cm[0, 1] / phishing_n) if phishing_n else None,
        "legitimate_false_positive_rate": float(cm[1, 0] / legitimate_n) if legitimate_n else None,
        "accuracy": float(accuracy_score(y, pred)),
        "balanced_accuracy": float(balanced_accuracy_score(y, pred)),
        "mean_max_confidence": float(p.max(axis=1).mean()),
        "latency_seconds": float(latency_seconds),
        "latency_seconds_per_example": float(latency_seconds / max(1, len(y))),
        "model_size_bytes": model_size_bytes,
    }


def compare_probabilities(*, labels: list[int], urls: list[str], candidate_probabilities,
                          incumbent_probabilities, candidate_id: str, incumbent_id: str,
                          evaluation_manifest: dict | None, policy: dict | None,
                          candidate_latency_seconds: float = 0.0,
                          incumbent_latency_seconds: float = 0.0,
                          candidate_size_bytes: int | None = None,
                          incumbent_size_bytes: int | None = None,
                          candidate_bundle_sha256: str | None = None,
                          incumbent_bundle_sha256: str | None = None,
                          training_dataset_fingerprint: str | None = None) -> dict:
    """Return BLOCKED unless data provenance and every required policy bound exist."""
    blockers, failures = [], []
    if not labels or len(labels) != len(urls) or any(v not in (0, 1) for v in labels):
        blockers.append("invalid_or_empty_evaluation_examples")
    fingerprint = dataset_fingerprint(labels, urls) if len(labels) == len(urls) else None
    manifest = evaluation_manifest or {}
    if not (manifest.get("trusted_ground_truth") is True and manifest.get("independent") is True
            and manifest.get("domain_overlap_reviewed") is True
            and manifest.get("dataset_fingerprint") == fingerprint
            and manifest.get("source_reference")
            and manifest.get("frozen_at_utc")
            and manifest.get("label_provenance")
            and manifest.get("training_url_overlap_count") == 0
            and manifest.get("training_domain_overlap_count") == 0
            and manifest.get("overlap_audit_sha256")
            and manifest.get("training_dataset_fingerprint") == training_dataset_fingerprint
            and manifest.get("incumbent_training_history_verified") is True
            and manifest.get("incumbent_training_history_reference")
            and fingerprint != training_dataset_fingerprint
            and fingerprint != manifest.get("training_validation_fingerprint")):
        blockers.append("evaluation_manifest_missing_or_untrusted")
    if not isinstance(policy, dict) or policy.get("enabled") is not True or not policy.get("version"):
        blockers.append("evaluation_policy_not_enabled_or_versioned")
    thresholds = (policy or {}).get("thresholds")
    required = {"max_phishing_fnr", "max_legitimate_fpr", "min_phishing_recall",
                "min_legitimate_recall", "min_phishing_precision", "min_legitimate_precision",
                "max_phishing_fnr_regression", "max_legitimate_fpr_regression",
                "max_latency_ratio"}
    if not isinstance(thresholds, dict) or not required.issubset(thresholds):
        blockers.append("required_policy_thresholds_missing")
    elif any(not isinstance(thresholds[key], (int, float)) or not math.isfinite(thresholds[key])
             for key in required):
        blockers.append("policy_thresholds_must_be_finite_numbers")
    elif (not 0 <= thresholds["max_phishing_fnr"] <= 1
          or not 0 <= thresholds["max_legitimate_fpr"] <= 1
          or not 0 <= thresholds["min_phishing_recall"] <= 1
          or not 0 <= thresholds["min_legitimate_recall"] <= 1
          or not 0 <= thresholds["min_phishing_precision"] <= 1
          or not 0 <= thresholds["min_legitimate_precision"] <= 1
          or thresholds["max_phishing_fnr_regression"] < 0
          or thresholds["max_legitimate_fpr_regression"] < 0
          or thresholds["max_latency_ratio"] <= 0):
        blockers.append("policy_thresholds_out_of_range")
    try:
        if not labels or len(labels) != len(urls) or any(v not in (0, 1) for v in labels):
            raise ValueError("labels and URLs must be non-empty, aligned, and mapped to 0/1")
        candidate = _metrics(labels, candidate_probabilities, candidate_latency_seconds, candidate_size_bytes)
        incumbent = _metrics(labels, incumbent_probabilities, incumbent_latency_seconds, incumbent_size_bytes)
    except (ValueError, TypeError) as exc:
        blockers.append(f"invalid_model_output:{exc}")
        candidate = incumbent = None
    decision = "BLOCKED"
    if candidate is not None and not blockers:
        bounds = {
            "phishing_false_negative_rate": thresholds["max_phishing_fnr"],
            "legitimate_false_positive_rate": thresholds["max_legitimate_fpr"],
        }
        for key, maximum in bounds.items():
            if candidate[key] is None:
                blockers.append(f"required_metric_unavailable:{key}")
            elif candidate[key] > maximum:
                failures.append(f"{key}_exceeds_policy")
        for cls, key in (("phishing", "min_phishing_recall"), ("legitimate", "min_legitimate_recall")):
            if candidate["per_class"][cls]["support"] == 0:
                blockers.append(f"required_class_missing:{cls}")
            elif candidate["per_class"][cls]["recall"] < thresholds[key]:
                failures.append(f"{cls}_recall_below_policy")
        for cls, key in (("phishing", "min_phishing_precision"), ("legitimate", "min_legitimate_precision")):
            if candidate["per_class"][cls]["precision"] < thresholds[key]:
                failures.append(f"{cls}_precision_below_policy")
        for metric, policy_key in (("phishing_false_negative_rate", "max_phishing_fnr_regression"),
                                   ("legitimate_false_positive_rate", "max_legitimate_fpr_regression")):
            c_value, i_value = candidate[metric], incumbent[metric]
            if c_value is not None and i_value is not None and c_value - i_value > thresholds[policy_key]:
                failures.append(f"{metric}_regressed_vs_incumbent")
        ratio = candidate["latency_seconds_per_example"] / max(incumbent["latency_seconds_per_example"], 1e-12)
        if ratio > thresholds["max_latency_ratio"]:
            failures.append("latency_ratio_exceeds_policy")
        decision = "BLOCKED" if blockers else ("REJECT" if failures else "PASS")
    return {"candidate_id": candidate_id, "incumbent_id": incumbent_id,
            "dataset_fingerprint": fingerprint, "policy_version": (policy or {}).get("version"),
            "training_dataset_fingerprint": training_dataset_fingerprint,
            "training_validation_fingerprint": manifest.get("training_validation_fingerprint"),
            "policy_sha256": (hashlib.sha256(json.dumps(policy, sort_keys=True, separators=(",", ":")).encode()).hexdigest() if policy else None),
            "evaluation_manifest_sha256": (hashlib.sha256(json.dumps(manifest, sort_keys=True, separators=(",", ":")).encode()).hexdigest() if manifest else None),
            "evaluation_independence": {"trusted_ground_truth": manifest.get("trusted_ground_truth") is True,
                                        "independent": manifest.get("independent") is True,
                                        "domain_overlap_reviewed": manifest.get("domain_overlap_reviewed") is True,
                                        "incumbent_training_history_verified": manifest.get("incumbent_training_history_verified") is True},
            "candidate_bundle_sha256": candidate_bundle_sha256,
            "incumbent_bundle_sha256": incumbent_bundle_sha256,
            "candidate_metrics": candidate, "incumbent_metrics": incumbent,
            "latency_ratio_candidate_to_incumbent": (
                candidate["latency_seconds_per_example"] / max(incumbent["latency_seconds_per_example"], 1e-12)
                if candidate and incumbent else None),
            "decision": decision, "blocking_reasons": blockers, "rejection_reasons": failures,
            "promotion_enabled": False}


def save_report(report: dict, path: str | Path) -> None:
    target = Path(path)
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(json.dumps(report, indent=2), encoding="utf-8")


def evaluate_model_bundles(*, labels: list[int], urls: list[str], candidate_model: str | Path,
                           candidate_tokenizer: str | Path, incumbent_model: str | Path,
                           incumbent_tokenizer: str | Path, candidate_id: str,
                           incumbent_id: str, evaluation_manifest: dict | None,
                           policy: dict | None) -> dict:
    """Load and compare two project-compatible bundles on identical URLs.

    URL normalization, character tokenization, padding, and output order come
    from the project's PhishingPredictor and ``ml.preprocessing`` contract.
    """
    from ml.prediction import PhishingPredictor
    from ml.preprocessing import preprocess_url

    candidate_model, candidate_tokenizer = Path(candidate_model), Path(candidate_tokenizer)
    bundle_path = candidate_model.parent / "bundle.json"
    try:
        bundle = json.loads(bundle_path.read_text(encoding="utf-8"))
        for name, path in (("best_model.h5", candidate_model), ("tokenizer.json", candidate_tokenizer)):
            digest = hashlib.sha256(path.read_bytes()).hexdigest()
            if bundle.get("files", {}).get(name) != digest:
                raise ValueError(f"candidate artifact checksum mismatch: {name}")
        if bundle.get("role") != "candidate-not-production" or bundle.get("class_order") != ["phishing", "legitimate"]:
            raise ValueError("candidate bundle role or class order is incompatible")
    except Exception as exc:
        return {"candidate_id": candidate_id, "incumbent_id": incumbent_id,
                "dataset_fingerprint": dataset_fingerprint(labels, urls), "decision": "BLOCKED",
                "blocking_reasons": [f"candidate_bundle_invalid:{type(exc).__name__}: {exc}"],
                "rejection_reasons": [], "promotion_enabled": False}

    try:
        candidate = PhishingPredictor(candidate_model, candidate_tokenizer)
    except Exception as exc:
        return {"candidate_id": candidate_id, "incumbent_id": incumbent_id,
                "dataset_fingerprint": dataset_fingerprint(labels, urls), "decision": "BLOCKED",
                "blocking_reasons": [f"candidate_model_load_failed:{type(exc).__name__}: {exc}"],
                "rejection_reasons": [], "promotion_enabled": False}
    try:
        incumbent = PhishingPredictor(incumbent_model, incumbent_tokenizer)
    except Exception as exc:
        return {"candidate_id": candidate_id, "incumbent_id": incumbent_id,
                "dataset_fingerprint": dataset_fingerprint(labels, urls), "decision": "BLOCKED",
                "blocking_reasons": [f"incumbent_model_load_failed:{type(exc).__name__}: {exc}"],
                "rejection_reasons": [], "promotion_enabled": False}
    if candidate.categories != incumbent.categories or candidate.categories != ["phishing", "legitimate"]:
        return {"candidate_id": candidate_id, "incumbent_id": incumbent_id,
                "dataset_fingerprint": dataset_fingerprint(labels, urls), "decision": "BLOCKED",
                "blocking_reasons": ["model_output_class_order_incompatible"],
                "rejection_reasons": [], "promotion_enabled": False}

    def predict_all(predictor):
        values = np.concatenate([preprocess_url(url, predictor.tokenizer) for url in urls], axis=0)
        started = time.perf_counter()
        values_out = predictor.model.predict(values, verbose=0)
        return values_out, time.perf_counter() - started

    try:
        candidate_prob, candidate_seconds = predict_all(candidate)
        incumbent_prob, incumbent_seconds = predict_all(incumbent)
    except Exception as exc:
        return {"candidate_id": candidate_id, "incumbent_id": incumbent_id,
                "dataset_fingerprint": dataset_fingerprint(labels, urls), "decision": "BLOCKED",
                "blocking_reasons": [f"bundle_prediction_failed:{type(exc).__name__}: {exc}"],
                "rejection_reasons": [], "promotion_enabled": False}
    candidate_size = Path(candidate_model).stat().st_size + Path(candidate_tokenizer).stat().st_size
    incumbent_size = Path(incumbent_model).stat().st_size + Path(incumbent_tokenizer).stat().st_size
    incumbent_bundle_path = Path(incumbent_model).parent / "bundle.json"
    incumbent_pair = (hashlib.sha256(incumbent_bundle_path.read_bytes()).hexdigest()
        if incumbent_bundle_path.is_file() else hashlib.sha256((
            hashlib.sha256(Path(incumbent_model).read_bytes()).hexdigest()
            + hashlib.sha256(Path(incumbent_tokenizer).read_bytes()).hexdigest()).encode("ascii")).hexdigest())
    return compare_probabilities(labels=labels, urls=urls,
        candidate_probabilities=candidate_prob, incumbent_probabilities=incumbent_prob,
        candidate_id=candidate_id, incumbent_id=incumbent_id,
        evaluation_manifest=evaluation_manifest, policy=policy,
        candidate_latency_seconds=candidate_seconds, incumbent_latency_seconds=incumbent_seconds,
        candidate_size_bytes=candidate_size, incumbent_size_bytes=incumbent_size,
        candidate_bundle_sha256=hashlib.sha256(bundle_path.read_bytes()).hexdigest(),
        incumbent_bundle_sha256=incumbent_pair,
        training_dataset_fingerprint=bundle.get("training_dataset_fingerprint"))


def main() -> int:
    import argparse
    parser = argparse.ArgumentParser(description="Compare a candidate and incumbent on one frozen label<TAB>URL file")
    parser.add_argument("candidate_bundle", type=Path)
    parser.add_argument("incumbent_bundle", type=Path)
    parser.add_argument("evaluation_tsv", type=Path)
    parser.add_argument("evaluation_manifest", type=Path)
    parser.add_argument("policy", type=Path)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    labels, urls = [], []
    label_ids = {"phishing": 0, "legitimate": 1}
    for line_no, raw in enumerate(args.evaluation_tsv.read_text(encoding="utf-8").splitlines(), 1):
        label, sep, url = raw.partition("\t")
        if not sep or label not in label_ids or not url.strip() or "\t" in url:
            raise ValueError(f"malformed evaluation row at line {line_no}")
        labels.append(label_ids[label]); urls.append(url.strip())
    if not labels or len(set(urls)) != len(urls):
        raise ValueError("evaluation dataset is empty or contains duplicate URLs")
    candidate_manifest = json.loads((args.candidate_bundle / "bundle.json").read_text(encoding="utf-8"))
    incumbent_manifest = json.loads((args.incumbent_bundle / "bundle.json").read_text(encoding="utf-8"))
    report = evaluate_model_bundles(labels=labels, urls=urls,
        candidate_model=args.candidate_bundle / "best_model.h5",
        candidate_tokenizer=args.candidate_bundle / "tokenizer.json",
        incumbent_model=args.incumbent_bundle / "best_model.h5",
        incumbent_tokenizer=args.incumbent_bundle / "tokenizer.json",
        candidate_id=candidate_manifest["candidate_id"],
        incumbent_id=incumbent_manifest["model_version"],
        evaluation_manifest=json.loads(args.evaluation_manifest.read_text(encoding="utf-8")),
        policy=json.loads(args.policy.read_text(encoding="utf-8")))
    save_report(report, args.output)
    print(json.dumps(report, indent=2))
    return 0 if report["decision"] == "PASS" else 2


if __name__ == "__main__":
    raise SystemExit(main())
