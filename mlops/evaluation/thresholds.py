"""Threshold trade-off calculations; this module never chooses a policy."""
from __future__ import annotations

import numpy as np


def threshold_tradeoff(labels, phishing_scores, thresholds):
    """Compute phishing-positive metrics for synthetic or trusted validation data.

    Class index 0 is phishing throughout the project. A score equal to the
    threshold is classified as phishing, matching argmax's tie behavior.
    """
    y = np.asarray(labels, dtype=np.int8)
    scores = np.asarray(phishing_scores, dtype=float)
    if y.ndim != 1 or scores.shape != y.shape or not len(y):
        raise ValueError("labels and phishing_scores must be aligned non-empty vectors")
    if not np.isin(y, [0, 1]).all() or not np.isfinite(scores).all() or ((scores < 0) | (scores > 1)).any():
        raise ValueError("labels must be 0/1 and scores finite probabilities")
    rows = []
    def expected_calibration_error():
        target = (y == 0).astype(float)
        total = 0.0
        edges = np.linspace(0, 1, 11)
        for index, (lo, hi) in enumerate(zip(edges[:-1], edges[1:])):
            mask = (scores >= lo) & ((scores <= hi) if index == 9 else (scores < hi))
            if mask.any():
                total += float(mask.sum() / len(scores)) * abs(float(scores[mask].mean() - target[mask].mean()))
        return total

    for threshold in thresholds:
        if not np.isfinite(threshold) or not 0 <= threshold <= 1:
            raise ValueError("thresholds must be finite values in [0, 1]")
        pred_phishing = scores >= threshold
        tp = int(((y == 0) & pred_phishing).sum())
        fn = int(((y == 0) & ~pred_phishing).sum())
        fp = int(((y == 1) & pred_phishing).sum())
        tn = int(((y == 1) & ~pred_phishing).sum())
        p_support, l_support = int((y == 0).sum()), int((y == 1).sum())
        p_precision = tp / (tp + fp) if tp + fp else 0.0
        p_recall = tp / p_support if p_support else None
        l_precision = tn / (tn + fn) if tn + fn else 0.0
        l_recall = tn / l_support if l_support else None
        rows.append({"threshold": float(threshold),
            "confusion_matrix_labels_phishing_legitimate": [[tp, fn], [fp, tn]],
            "phishing_recall": p_recall, "phishing_fnr": fn / p_support if p_support else None,
            "legitimate_specificity": l_recall,
            "legitimate_fpr": fp / l_support if l_support else None,
            "phishing_precision": p_precision, "legitimate_precision": l_precision,
            "phishing_support": p_support, "legitimate_support": l_support,
            "brier_score_phishing_probability": float(np.mean((scores - (y == 0)) ** 2)),
            "expected_calibration_error_10_bins": expected_calibration_error()})
    return rows


def guarded_threshold_tradeoff(*, labels, phishing_scores, thresholds, validation_manifest):
    """Require independently established validation lineage before real analysis."""
    manifest = validation_manifest or {}
    required = (manifest.get("trusted_ground_truth") is True
        and manifest.get("independent") is True
        and manifest.get("frozen_at_utc")
        and manifest.get("source_reference")
        and manifest.get("label_provenance")
        and manifest.get("incumbent_training_history_verified") is True
        and manifest.get("training_dataset_fingerprint")
        and manifest.get("training_url_overlap_count") == 0
        and manifest.get("training_domain_overlap_count") == 0
        and manifest.get("overlap_audit_sha256"))
    if not required:
        return {"decision": "BLOCKED", "blocking_reasons": ["trusted_independent_validation_lineage_unavailable"],
                "rows": [], "threshold_recommendation": None}
    return {"decision": "ANALYSIS_ONLY", "blocking_reasons": [],
            "rows": threshold_tradeoff(labels, phishing_scores, thresholds),
            "threshold_recommendation": None}
