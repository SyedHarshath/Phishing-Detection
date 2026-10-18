# Evaluation readiness decision (2026-10-18)

## Status: BLOCKED

The current `val.txt` and `test.txt` are readable, but neither can be claimed as an independent evaluation set. Available MLflow records and run artifacts do not bind the incumbent to a dataset fingerprint or document checkpoint-selection history. Therefore it is unknown whether either split informed incumbent training, tokenization, model selection, or previous reporting. File names and zero overlap counts cannot resolve that question.

The upstream project README describes split roles and sources, but does not establish the incumbent’s actual data usage or a content-addressed frozen snapshot. The local note’s Google Drive access-request link is not an available, verified evaluation source. No concrete independently labeled set, separately curated source, or verified post-deployment labels were established in this audit. Do not relabel or repartition existing rows and call the result independent.

## Existing pipeline and gates

`mlops/evaluation/compare.py` requires trusted ground truth, a frozen fingerprint and timestamp, label provenance, explicit URL/domain-overlap audit fields, training and training-validation fingerprints, a versioned enabled policy with all required thresholds, and now explicit evidence that the incumbent training history was verified. Missing evidence returns `BLOCKED`. Promotion also checks these attestations, artifact and report checksums, candidate/incumbent versions, policy hash, provenance report and read-only DVC status. Retraining execution requires a separate source provenance manifest bound to current split SHA-256s. Promotion remains disabled by default and missing thresholds remain blocking.

These are fail-closed software gates around supplied evidence; they cannot make an unsupported attestation true. A domain-overlap count must be based on pinned public-suffix data for registrable domains. This environment has no such dataset/dependency, so current inventory can report hostnames only and cannot support a registrable-domain independence claim.

## Threshold trade-off mechanism

`mlops/evaluation/thresholds.py` implements threshold tables for phishing score (class 0), including confusion matrix, phishing recall/FNR, legitimate specificity/FPR, per-class precision/recall, support, phishing-probability Brier score and 10-bin expected calibration error. The guarded entry point returns `BLOCKED` unless validation provenance, frozen source, verified incumbent training history and overlap evidence are supplied. It never selects a threshold or recommends policy limits. Synthetic fixtures test the calculations; no production threshold table was generated because there is no trusted validation set. Bundle evaluation measures inference latency separately; latency does not vary with the decision threshold.

No acceptable FPR/FNR limits are invented. `configs/evaluation_policy.example.json` remains an unconfigured example. No threshold tuning, real-data model inference comparison, final holdout selection, training, or promotion was performed.

## What would unblock evaluation

1. A frozen, independently labeled evaluation source with immutable source/version, collection period, sampling design, label provenance and per-file hashes.
2. Trusted incumbent training and checkpoint-selection records with exact dataset fingerprints, enabling evidence that the evaluation data was not used for training, tokenization or model selection.
3. Disk-backed exact/canonical URL overlap results and registrable-domain overlap results computed with a pinned PSL snapshot and dependency; retain the audit output and bind it to the fingerprints.
4. Signed or access-controlled evidence for candidate/incumbent bundle checksums, evaluation-manifest digest, code revision and policy version.
5. Operator review to define quality limits from validation-only trade-offs, and a separately reserved final holdout for one-time evaluation.

Phase 6 added an offline prospective record builder and a PSL-pinned disk-backed cross-dataset overlap audit. No suitable record set or PSL snapshot was present, so these mechanisms have only synthetic test coverage. See [prospective evaluation protocol](PROSPECTIVE_EVALUATION.md) and [overlap analysis](OVERLAP_ANALYSIS.md). A prospective set can satisfy the frozen temporal protocol while the incumbent's historical independence remains unproven; promotion stays blocked.

## Inventory scope

Fast inventories of workspace and DVC-cache files completed. They stream hashes and parse/label counts and are immutable report files. The expensive disk-backed full duplicate and cross-split scan did **not** complete and was not run; the earlier interrupted scan produced no usable result. Do not interpret the fast inventory as proof that there is no duplicate or leakage.

See [data provenance decision](DATA_PROVENANCE_DECISION.md) and [MLOps audit](MLOPS_AUDIT.md).
