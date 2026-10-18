# Model promotion and rollback

## Current safety state

Promotion is an explicit local CLI operation. Automatic promotion remains off (`PHISHGUARD_AUTOMATIC_PROMOTION=false`), and deliberate promotion also requires `PHISHGUARD_PROMOTION_ENABLED=true`, an enabled evaluation policy with finite thresholds, and `--enable-promotion --confirm`. The checked-in example policy has null limits and is disabled. The known dataset/DVC hash mismatch, absent `dvc.lock`, and unresolved independent evaluation set keep real candidates blocked.

The registry and promotion mechanism are implemented, but real-data eligibility and a real promotion have not been verified. Do not treat the current project as ready for production model rollout.

## Bundle contract

Each bundle directory contains `best_model.h5`, `tokenizer.json`, and `bundle.json`. The manifest includes schema and model version, candidate ID and type, test-only flag, artifact SHA-256 hashes, class order (`phishing`, `legitimate`), numeric label mapping, preprocessing version, 200-character sequence length, training dataset fingerprint, run ID when available, creation timestamp, and format compatibility metadata. Model and tokenizer are always copied and verified together. Smoke/test-only bundles are rejected by promotion.

`verify_bundle` checks manifest fields, artifact checksums and formats, tokenizer/model compatibility, model loading, and one isolated `.invalid` URL prediction. Loading is a compatibility check only; promotion additionally requires provenance and evaluation gates.

## Registry and serving

`artifacts/model_registry/versions/<model-version>/` stores immutable bundle copies. `registry.json` tracks versions and operation history. `active.json` is the single active pointer, atomically replaced on the same filesystem. A Windows OS file lock prevents concurrent registry operations. `recover` reconciles registry history if a process stops after the active pointer changes but before the history file is updated.

FastAPI's `PredictionService` creates one `PhishingPredictor` and keeps it in memory. `PhishingPredictor` resolves `PHISHGUARD_ACTIVE_MODEL_MANIFEST` at initialization, or defaults to `artifacts/model_registry/active.json` when present; without a manifest it keeps the existing production paths. The Streamlit app now uses the same `PhishingPredictor`, but its `st.cache_resource` also keeps the loaded pair for the process lifetime. **Restart both FastAPI and Streamlit after promotion or rollback.** There is no live hot reload. The registry command does not start, stop, or restart services.

## Operations

From the repository root in PowerShell:

```powershell
# Copy the current production model/tokenizer into a new registry bundle.
# This reads the current pair and writes only under artifacts/model_registry.
python -m mlops.promotion.registry bootstrap-legacy

# Inspect registry and active version.
python -m mlops.promotion.registry inspect

# Check candidate checksums and compatibility before evaluation/promotion.
python -c "from mlops.promotion.registry import verify_bundle; print(verify_bundle(r'artifacts/candidates/<candidate-id>'))"

# Compare on a frozen, independently labeled evaluation TSV.
python -m mlops.evaluation.compare `
  artifacts/candidates/<candidate-id> `
  artifacts/model_registry/versions/legacy-incumbent `
  data/independent-evaluation.tsv `
  data/independent-evaluation.manifest.json `
  configs/evaluation_policy.json `
  --output artifacts/candidates/<candidate-id>/evaluation_report.json

# Promotion requires an approved policy and provenance report plus an evaluation report.
# Configure PHISHGUARD_PROMOTION_ENABLED=true only after review, then explicitly confirm:
python -m mlops.promotion.registry promote `
  artifacts/candidates/<candidate-id> `
  artifacts/candidates/<candidate-id>/evaluation_report.json `
  configs/evaluation_policy.json `
  artifacts/candidates/<candidate-id>/provenance_report.json `
  --enable-promotion --confirm

# Roll back to the recorded previous known-good version.
python -m mlops.promotion.registry rollback --reason "post-restart health check failed" --confirm

# Reconcile metadata after an interrupted operation. This verifies the active bundle.
python -m mlops.promotion.registry recover
```

Promotion checks the exact candidate and incumbent bundle hashes in the evaluation report, evaluation decision `PASS`, current policy version and fingerprint, finite required metrics, independent trusted evaluation markers, training provenance and validation, and a current clean read-only DVC status. It blocks on missing/stale/mismatched reports, missing thresholds, untrusted evaluation provenance, or unresolved DVC status. It does not create an evaluation dataset or establish ground truth.

The evaluation manifest must bind `dataset_fingerprint` to the integer-label TSV format used by the evaluator and include `trusted_ground_truth: true`, `independent: true`, `domain_overlap_reviewed: true`, label provenance, a frozen timestamp and source reference, zero exact-URL/domain overlaps against training data, an overlap audit SHA-256, and matching training and training-validation fingerprints. Those assertions must be backed by the named review artifacts; do not use the test split used by a trainer or validator as the candidate selection dataset.

The provenance report must identify the exact `training_dataset_fingerprint` in the candidate bundle, state `passed: true`, `validation_passed: true`, and `dvc_clean: true`, and include `training_validation_fingerprint`. It must also bind `source_provenance_verified`, `authoritative_registration`, and `source_registration_sha256` to the candidate bundle metadata produced from the staged importer's reviewed registration. Promotion rejects missing or mismatched source registration and evaluation data whose fingerprint matches that training validation fingerprint. These fields must come from reviewed evidence; do not hand-edit them to bypass checks.

After a successful promotion or rollback, restart both serving processes and verify `/health`, `/model-info`, and a known synthetic URL prediction. A failed check should be followed by the rollback command, service restart, and the same checks. The CLI confirms that the active manifest references a loadable bundle; it cannot verify a running service because it does not manage service processes.

## Failure handling and audit

The version directory is staged and verified before it becomes visible. The active manifest replacement is atomic; interruption leaves either the old or new manifest intact. If the process stops after the new pointer is written, run `recover` to reconcile version status and history. Rollback independently verifies the target bundle and registered bundle checksum before switching. History records source and target version, time, reason, evaluation report checksum, policy version, and bundle checksum where applicable. Candidate bundles and the previous production pair are retained.

MLflow is optional. The retraining worker uses only an explicitly configured tracking URI and reports unavailable/failed tracking distinctly. Registry operations rely on local bundle/report integrity and do not infer artifact retrieval from a local copy.

## Remaining prerequisites

- Resolve and record why local dataset hash `aea1efa4e032d893296ea75d6ce31e1a.dir` differs from tracked DVC hash `6e5cc66e19ab49115a2d14ac50f053f0.dir`; restore no data and update no pointer blindly.
- Produce a clean, reproducible DVC pipeline state and `dvc.lock`.
- Establish a frozen, independently labeled evaluation dataset with reviewed URL/domain overlap and source provenance. Validation data used for checkpoint selection cannot serve as that final comparison set.
- Agree and validate false-positive, false-negative, per-class precision/recall, latency, and regression thresholds for the application. Do not copy arbitrary example limits into production.
- Verify an end-to-end real candidate report and provenance report before allowing promotion.

Until these prerequisites pass, keep `PHISHGUARD_PROMOTION_ENABLED=false` and leave the incumbent active.

## Synthetic end-to-end demonstration

The registry test creates fake model files and a stub loader entirely under a temporary directory. It explicitly enables promotion for that isolated fixture, then verifies rollback and recovery:

```powershell
python -m unittest discover -s test -p 'test_model_promotion_registry.py'
```

This exercises control flow, not TensorFlow model quality, trusted real evaluation data, DVC provenance, or a running API deployment.
