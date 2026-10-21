# Retraining lifecycle operations

The existing `scheduled-review` command exports verified feedback into an immutable batch, persists a job, and invokes the isolated candidate worker. Evaluation requires operator-supplied evidence; missing inputs block the job. Promotion is possible only through the existing registry, with its policy enabled and explicit confirmation. Serving reads `active.json` at process start; after a confirmed promotion, stop and restart the API, then verify `/model-info` before considering the new version served.

This is a scheduled command, not a background daemon. Configure an external scheduler to invoke it at the desired review interval; each invocation checks both the new verified-record threshold and elapsed interval, and still requires minimum records in both classes. The interval only requests a review and does not bypass minimum data or provenance checks. Configuration uses environment variables:

| Variable | Default | Purpose |
|---|---:|---|
| `RETRAIN_AUTO_ENABLED` | `false` | Dispatch is disabled unless the operator explicitly sets this to `true` for an approved isolated workflow. |
| `PHISHGUARD_MIN_NEW_VERIFIED_RECORDS` | `1000` | New verified records required for the count trigger. |
| `PHISHGUARD_MIN_RECORDS_PER_CLASS` | `100` | Minimum verified records for each class, required for either trigger. |
| `RETRAIN_INTERVAL_HOURS` | `PHISHGUARD_REVIEW_INTERVAL_DAYS × 24` (`30` days) | Interval review trigger. |
| `PHISHGUARD_RETRAINING_COOLDOWN_DAYS` | `7` | Minimum time after a successful job before another is eligible. |

Values can also be supplied as the command's `--min-records`, `--min-per-class`, `--review-days`, and `--cooldown-days` options. The lifecycle database follows the API database selection: `DATABASE_URL` when configured, otherwise the local SQLite path. A job records its trigger reason, timestamps, batch ID, and stage details. Statuses include `eligible`, `running`, `training_completed`, `evaluating`, `awaiting_approval`, `promoting`, `promoted`, `rejected`, `blocked`, and `failed`. Only verified, provenance-bearing labels enter exported batches; user feedback itself remains an unverified claim. PhishTank or Common Crawl membership is not by itself proof that an individual URL label is valid.

Example (the paths must refer to independently reviewed, real evidence):

```powershell
python -m mlops.data.lifecycle scheduled-review `
  --provenance-manifest <training-source-provenance.json> `
  --evaluation-tsv <independent-evaluation.tsv> `
  --evaluation-manifest <evaluation-manifest.json> `
  --policy <approved-enabled-policy.json> `
  --promotion-provenance <candidate-provenance-report.json>
```

An evaluation PASS stores the candidate and report with job status `awaiting_approval`. Approve explicitly with `python -m mlops.data.lifecycle --db <database> approve-job <job-id> --confirm`. The process must also have `PHISHGUARD_PROMOTION_ENABLED=true`; registry policy and provenance checks still apply. A promotion result reports `restart_required: true` and `hot_reload: false`.

Inspect jobs with `python -m mlops.data.lifecycle --db <database> status`. Requeue failed jobs with `retry-job <job-id>`. If a training process was interrupted, first confirm that worker is no longer running, then use `recover-job <job-id>`; this reuses the job's existing batch. Do not recover a live job. Approval is claimed atomically; concurrent approval requests cannot invoke promotion twice. If a process is interrupted while status is `promoting`, confirm that it has stopped, then use `recover-promotion <job-id>`. This reads both registry manifests and either marks the job promoted, returns it to `awaiting_approval` when the incumbent is still active, or leaves it locked for manual reconciliation if registry state is mixed or unreadable. It does not write to the registry. Dispatch is disabled by default; set `RETRAIN_AUTO_ENABLED=true` only for an explicitly approved isolated workflow after provenance and evaluation prerequisites are ready.

No policy thresholds, independent evaluation records, or provenance are supplied by this project. The example policy is disabled, so real evaluation and promotion correctly remain blocked until authorized owners provide those inputs. Unit tests use synthetic fixtures and mocks; they are not evidence of model quality.
