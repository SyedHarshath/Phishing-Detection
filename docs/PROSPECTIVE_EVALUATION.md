# Prospective evaluation collection protocol

No prospective record set was available during Phase 6, so no labels or model metrics were created. This protocol keeps a prospective evaluation separate from training and preserves the incumbent as a frozen baseline.

## Freeze the incumbent first

The current files were copied byte-for-byte and checksummed at [legacy freeze manifest](../mlops/promotion/legacy-freeze-20261018T160840Z/freeze_manifest.json). The snapshot includes the model, tokenizer, preprocessing and prediction code. It records the historical training and checkpoint-selection provenance as **unknown**. It does not reconstruct those facts or make later evaluation eligible for promotion.

## Allowed record sources

Each source must be reviewed and allowlisted in the evaluation protocol. A record must have a source record ID, URL, label, first-observed timestamp, label-verification timestamp, method, provenance reference and reviewer. Its source must support the particular label; a URL appearing in a Common Crawl capture is not by itself a verified benign label. PhishTank’s official online-valid feed provides community-verified phishing reports and verification timestamps, but the current hourly feed is not a historical snapshot and does not supply the benign class. User-submitted feedback is not ground truth unless it has independently passed the project’s verified-label process.

For every record, `first_observed_at_utc` must be strictly later than the incumbent freeze; `label_verified_at_utc` must have a timezone, be at or after first observation, and not be in the future. The record must explicitly state `verified_label: true`, `excluded_from_training: true`, and `excluded_from_selection: true`. Missing timestamps, unapproved methods, unverified labels, duplicates and conflicts are recorded as exclusions and block snapshot creation.

## Offline builder

The builder does not visit URLs. It copies the source JSONL, writes a separate frozen TSV only when record and overlap gates pass, and produces `readiness.json` with the incumbent identity, source reviews, exclusions hash, dataset checksum, PSL version/checksum, and per-known-training-source exact/canonical/hostname/registrable-domain overlap.

```powershell
python -m mlops.evaluation.prospective records.jsonl protocol.json `
  --training-source incumbent-train=dataset/big_dataset/train.txt `
  --training-source incumbent-validation=dataset/big_dataset/val.txt `
  --training-source incumbent-test=dataset/big_dataset/test.txt `
  --psl-file C:\provenance\public_suffix_list.dat `
  --psl-sha256 <reviewed-file-sha256> --psl-version <upstream-release-or-commit> `
  --output-root mlops/evaluation/prospective
```

The `protocol.json` must bind `incumbent_version`, `incumbent_bundle_sha256`, `frozen_at_utc`, `freeze_verified_by`, and an operator-reviewed `trusted_sources` map. Each approved source entry must include `source_reference`, `reviewed_by`, `label_quality_evidence`, and `approved_label_methods`. Input record schema is demonstrated in `test/test_prospective_evaluation.py`.

`READY_FOR_FROZEN_INCUMBENT_EVALUATION` means only that the timestamp, source-attestation, exclusion and overlap protocol passed against all explicitly supplied known datasets. `historical_independence` remains `unproven` while the incumbent’s training history is unknown. The builder always sets `promotion_eligible: false`; the evaluation set must not be used for training, checkpoint selection, threshold tuning, or iterative selection. Candidate promotion remains blocked by the existing incumbent-history gate. Do not use this final evaluation snapshot to select an operating threshold; that requires a separate trustworthy validation dataset.
