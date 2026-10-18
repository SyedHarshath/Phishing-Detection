# Retraining guide

## Current safe workflow

1. Keep user reports separate from verified labels. The current `/feedback` endpoint records an unverified claim only; it cannot establish ground truth.
2. Resolve dataset provenance and DVC cache access before treating a data revision as reproducible. Do not run `dvc checkout`, `dvc pull`, or `dvc repro` until the impact on local data/cache has been reviewed.
3. Run the read-only strict quality gate: `python -m mlops.dvc.validate_splits`. It writes `mlops/dvc/validation_report.json`, fingerprints all splits, reports empty lines, malformed rows, duplicates, invalid URLs, exact URL overlap, and shared-domain records. Any malformed row, duplicate, or exact overlap fails the gate. Shared-domain overlap is reported for review because shared domains can be expected and require a deliberate split policy.
4. Smoke candidate training is separate and capped: `python -m src.train_smoke --help`. It writes below `artifacts/candidate_cnn_bilstm`; it is not a production candidate and cannot be promoted. It can consume real split samples, so run only after the data gate is resolved.

## Phase 6 source recovery and import

Do not fetch data directly into `dataset/big_dataset`. Ask the upstream custodian for the released archive, immutable version, historical collection metadata, label methodology and trusted file hashes. Stage only operator-supplied files with `python -m mlops.dvc.staging_import import SOURCE --manifest provenance.json`. Review the stage inventory, originals, normalized files, exclusion records and source attestation; then separately approve with `python -m mlops.dvc.staging_import approve STAGE --operator NAME --approval-reference TICKET`. Add `--reviewed-exclusions` only after reviewing every exclusion. Approval writes a registration inside the staging directory; it does not update the active DVC pointer or training dataset.

`registration.json` can be supplied to the worker as `--provenance-manifest` only when running against that exact staged normalized directory. The worker still requires clean DVC status and a strict report for the exact bytes. The present workspace pointer mismatch and missing `dvc.lock` keep execution blocked. See [dataset acquisition](DATASET_ACQUISITION.md), [the provenance decision](DATA_PROVENANCE_DECISION.md), and [overlap analysis](OVERLAP_ANALYSIS.md).

## Verified review and batch lifecycle

The FastAPI `/feedback` API records a user claim as `pending_verification`; it never makes the claim training-eligible. A local analyst can review evidence and explicitly verify, reject, or dispute a claim. The verified label and reviewer/source provenance are retained in an audit table. This is a controlled local CLI, not an authenticated remote administration endpoint.

```powershell
python -m mlops.data.lifecycle --db mlops/data/predictions.db verify 123 --label phishing --reviewer analyst --provenance "independent analyst review" --reason "source evidence checked"
python -m mlops.data.lifecycle --db mlops/data/predictions.db reject 124 --reviewer analyst --provenance "analyst review" --reason "claim unsupported"
python -m mlops.data.lifecycle --db mlops/data/predictions.db dispute 125 --reviewer analyst --provenance "conflicting trusted sources"
python -m mlops.data.lifecycle --db mlops/data/predictions.db export-batch --output mlops/data/approved_batches
```

Schema migration is additive. Existing rows and IDs remain; historical feedback is set to pending and copied into feedback and label audit histories. Repeated identical feedback is idempotent. A conflicting report against an approved label disputes that label and removes training eligibility while preserving the reviewed label and audit trail. Approved batches contain TSV plus a JSON manifest with source IDs, class distribution, provenance counts, schema/preprocessing version, and SHA-256. An exported prediction ID cannot enter a later batch. Invalid URLs, duplicate URLs, missing provenance, and invalid labels stop export. Store batch directories as sensitive data because they contain raw URLs.

Before the first CLI use on an older database, make a backup and apply the additive migration once (FastAPI startup also applies it):

```powershell
Copy-Item mlops/data/predictions.db mlops/data/predictions.before-label-migration.db
python -m mlops.data.lifecycle init
```

## Eligibility controller (review only)

Initial configurable policy defaults are 1,000 new verified records, 100 per class, 30 days between reviews, and a 7-day cooldown. These are operational starting values, not validated statistical guarantees. A time review still requires new verified data and minimum counts. Jobs are persisted and a SQLite uniqueness constraint prevents multiple eligible/running jobs. A review only records a manual-worker-required job; it never starts training.

```powershell
python -m mlops.data.lifecycle eligibility --min-records 1000 --min-per-class 100 --review-days 30 --cooldown-days 7
python -m mlops.data.lifecycle scheduled-review --min-records 1000 --min-per-class 100 --review-days 30 --cooldown-days 7
python -m mlops.data.lifecycle status
```

Preview without writing with `python -m mlops.data.lifecycle preview-batch`. The future worker interface is `claim_job(job_id)`, `export_batch(...)`, then `finish_job(job_id, success, snapshot_id=...)`. Export associates the immutable batch ID with the active job. A failed job can be requeued with `python -m mlops.data.lifecycle retry-job JOB_ID` and reuses its recorded batch reference; no training worker exists in this milestone. Eligibility and preview commands are read-only after schema initialization. The final evaluation/test data must remain separate and must never be exported through this lifecycle.
