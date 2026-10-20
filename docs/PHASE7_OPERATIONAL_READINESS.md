# OPTIMUS operational readiness

**Status: limited local operation; not cleared for public deployment, real-data retraining, or model promotion.** This runbook describes the checked-in interfaces as of 2026-10-18. The upstream data source and local DVC pointer still disagree, incumbent training and checkpoint-selection history are unknown, and no trusted independent evaluation set, pinned PSL, or operational thresholds are available. Synthetic tests demonstrate software behavior only; they do not establish phishing-detection quality.

## Current system and protection boundaries

- The Chrome extension calls the loopback FastAPI service. `backend/app/main.py` uses the shared `PhishingPredictor` in `ml/prediction.py`; the model and tokenizer are loaded lazily and held in memory until process restart.
- The API stores full submitted URLs, including path and query, in its configured SQLite database. `/predictions` is disabled unless `PHISHGUARD_PREDICTIONS_TOKEN` is configured and then requires `X-PhishGuard-Admin-Token`; URL values are omitted unless an authenticated caller explicitly sets `include_urls=true`. This is a shared-secret gate, not user-level authorization. For remote access, require HTTPS at a trusted TLS terminator and restrict network access there. Compose loopback binding is defense in depth, not the endpoint's security boundary. Restrict database filesystem access as well.
- Feedback is an unverified claim. Explicit reviewer action is required before it is eligible for a fingerprinted batch. Batch export does not start training and does not satisfy dataset provenance or DVC gates.
- The model registry and atomic active-manifest switch exist. Registry and manifest updates are separate files; `recover` reconciles an interrupted update. A switched manifest does not hot-reload a running API or Streamlit process.
- The local `.h5` model, working dataset, SQLite prediction database, MLflow database/artifact store, and virtual environment are ignored by Git. The dataset directory is not reproducible from a clean clone, and the model is not carried by the checked-in DVC pointer as currently usable. Back them up independently; neither ignored local state nor a tracked hash report is a recovery copy.
- The source dataset is disputed: workspace files differ from the DVC pointer, and the pointer-matching cached training split is unusable. Do not run DVC restore, pull, add, repro, or push commands to resolve it. The local MLflow helper may start a server when called; monitoring/MLflow commands are excluded from the safe smoke sequence.
- The prediction API returns stable generic model/inference errors and distinguishes model loading failures from inference failures. History access uses the configured shared token, and URLs are omitted by default; there is no retention job or user-level authorization. Restrict access to the database and any exported batches.

## Prerequisites and safe verification

Use a clean Python 3.10 environment matching the Docker base, with dependencies installed from the project’s approved lock/package process. No formatter, linter, type checker, or pinned Python dependency lockfile is configured. Do not install or upgrade dependencies as part of routine verification without change review. Run commands from the repository root in PowerShell.

Safe verification sequence (tests use temporary files/databases; one existing registry integration test loads the current model read-only and exercises a reserved `.invalid` URL):

```powershell
python -m unittest discover -s test -v
python -m compileall -q backend ml mlops src test
python -m pip check
git diff --check
```

The full test suite includes a small synthetic candidate fit; it is not production training. `compileall` creates only ignored `__pycache__` files. `pip check` is read-only. Do not run these tests if the registry test’s protected model/tokenizer files are known to be open or being replaced by another process.

Safety-gate regression tests can be run separately:

```powershell
python -m unittest discover -s test -p 'test_operational_safety.py' -v
python -m unittest discover -s test -p 'test_label_lifecycle.py' -v
python -m unittest discover -s test -p 'test_model_promotion_registry.py' -v
python -m unittest discover -s test -p 'test_candidate_evaluation.py' -v
```

The last registry command loads the local model in a temporary registry and uses a temporary API database; it does not run a server or change the active model. Import validation without starting a service:

```powershell
python -c "import backend.app.main, ml.prediction, mlops.data.lifecycle, mlops.evaluation.compare, mlops.promotion.registry"
```

No full production overlap scan, DVC data operation, model training on local splits, MLflow server, Docker build, or deployment command belongs in this sequence.

## Repository/change and artifact inventory

The audit began on clean branch `main` at commit `f863014` (`First Stage - Operational Phase`); no working-tree change predated this Phase 7 work. The checkout has 145 tracked files. No `.env` file or credential-named tracked file was found; `.env.example` contains configuration examples only. No secret values were printed or copied into reports.

Tracked project inputs include source/config/docs/tests, `dataset/big_dataset.dvc`, `.dvc` metadata, the two identical legacy tokenizer pickle paths, and inventory/phase reports. Tracked inventory JSON/Markdown files are generated audit evidence, not authoritative dataset snapshots. There are no tracked `.h5` model weights, prediction/MLflow SQLite databases, or dataset split files. Locally ignored state includes `dataset/big_dataset/`, `test_results/*` weights, `mlops/data/predictions.db`, `mlflow.db`, `mlruns/`, `mlartifacts/`, `artifacts/`, and `mlflow_venv/`. A clean checkout therefore cannot serve predictions or reproduce the local experiments until separately authorized, hash-verified artifacts and runtime data are supplied.

The model and inference files match the Phase 6 freeze; the local application DB and dataset files match the recorded Phase 5 evidence. Phase 7 test execution did not alter these hashes:

| Local file | SHA-256 after verification | Prior evidence |
|---|---|---|
| `test_results/cnn_bilstm/best_model.h5` | `f12c216a8e840cbf099288e7b2350acfd956d71dd2f0478da79ad97402558365` | Freeze manifest |
| `ml/artifacts/tokenizer.json` | `c2f3fa9724eda654478e1bc65ddae804f78f9bc85ce23f6711d315a1b91c4a1c` | Freeze manifest |
| `mlops/data/predictions.db` | `0e4ce3db4cdb0a3c43555caed74f207f9e64379df0f69ac84b45b77dd0ad756d` | Phase 5 audit |
| `mlflow.db` | `4b05d519c3c42a9b9c523125d97553cdd6bbb7982a12ff4f8a0e6df7b313a265` | Phase 6 verification |
| workspace train split | `2de358802c0a4102b047845aa2d61184f3c9a5ac25cdf134bb64e126bb410165` | Phase 5 inventory |
| workspace validation split | `793d466ea76a5e0344a3fb9937d8249296f9a02e7cc3d0b299666021686f0644` | Phase 5 inventory |
| workspace test split | `813b7da0209c0c2dc6f3082a62cb77db2699d1922959f5b707ae31f496c16f33` | Phase 5 inventory |

These local hashes establish current byte identity against prior audit evidence. They are not signed upstream provenance, an independent backup, or evidence that the dataset or incumbent training history is trustworthy. No deletion or reset was performed.

## Local API and synthetic smoke procedure

The following is an operator procedure, not executed as part of this audit. It runs the real incumbent inference artifacts but writes only synthetic events to a separate temporary SQLite database. Do not remove the database until its contents are no longer needed.

In PowerShell window 1:

```powershell
$env:PHISHGUARD_DATABASE_PATH = Join-Path $env:TEMP 'optimus-smoke\predictions.sqlite'
$env:PHISHGUARD_PREDICTIONS_TOKEN = '<temporary high-entropy smoke secret>'
python -m uvicorn backend.app.main:app --host 127.0.0.1 --port 8000
```

Stop the process with `Ctrl+C`. Startup creates/migrates the database at the selected path. Environment variables are process-local. In PowerShell window 2, set the same database path and smoke token in this process before invoking the CLI:

```powershell
$env:PHISHGUARD_DATABASE_PATH = Join-Path $env:TEMP 'optimus-smoke\predictions.sqlite'
$env:PHISHGUARD_PREDICTIONS_TOKEN = '<same high-entropy smoke secret set in window 1>'
$health = Invoke-RestMethod http://127.0.0.1:8000/health
$health | Select-Object status, model_loaded, model_error
Invoke-RestMethod http://127.0.0.1:8000/model-info
$prediction = Invoke-RestMethod -Method Post -Uri http://127.0.0.1:8000/predict `
  -ContentType 'application/json' `
  -Body (@{ url = 'https://optimus-smoke.invalid/check' } | ConvertTo-Json)
$prediction
$feedback = Invoke-RestMethod -Method Post -Uri http://127.0.0.1:8000/feedback `
  -ContentType 'application/json' `
  -Body (@{ prediction_id = $prediction.prediction_id; actual_label = 'phishing' } | ConvertTo-Json)
$feedback
python -m mlops.data.lifecycle --db $env:PHISHGUARD_DATABASE_PATH preview-batch
```

Expected: health is `healthy`, model info and prediction identify a model version, prediction returns one of `phishing` or `legitimate` with a finite confidence, feedback is accepted, and `preview-batch` reports zero eligible records. This is an inference/persistence compatibility check only. It is not a quality result, and the `.invalid` sample is not ground truth. If the model is unavailable, health reports `degraded` and the Compose health check fails. Stop window 1 before changing the active manifest.

The Streamlit entry point is `src/app.py`, but Streamlit is not declared in `requirements.txt`; its startup command (`python -m streamlit run src/app.py`) is therefore **untested and conditional on an already approved environment containing Streamlit**. Stop it with `Ctrl+C`. The Docker image and Compose procedure have not been built or started; building installs packages and requires local model artifacts, so review that operation separately before use.

Chrome/Edge: load `extension/` as an unpacked extension after the API is running locally. Use its popup against a non-sensitive test URL. The extension currently has no request timeout; stop its scan when the API is unavailable. Do not send private URLs during smoke checks.

## Health, model identity, and safe restart checklist

Before an approved local model switch:

1. Confirm the candidate and evaluation/provenance reports are complete and independently reviewed; current real-data gates are known to block.
2. Back up the entire registry (including `active.json`, `registry.json`, version bundles and history) and the database as described below. Record backup hashes in the change record.
3. Verify the candidate bundle and hashes in a staging location. Never copy files over the active model or tokenizer individually.
4. Stop FastAPI and Streamlit. Perform only the separately authorized promotion/rollback action.
5. Inspect the registry and active manifest. Start each serving process again; loaded models are process-local and do not hot-reload.
6. Verify `/health` (`healthy` and `model_loaded: true`), `/model-info` (expected version), and one reserved `.invalid` smoke prediction against an isolated database.
7. If any identity, load, or smoke check fails, stop both processes, inspect/reconcile registry state, perform a confirmed rollback to the recorded prior version, restart both processes, and repeat health/identity checks. Keep both versions and evidence.

`/health` distinguishes API reachability from model availability: HTTP success with `degraded` means the API process is reachable but its model is unavailable. `400` from `/predict` means input validation; `503` means model unavailable; `500` means inference failed. `404` from feedback means no matching prediction. Feedback acceptance is not label verification. Evaluation `BLOCKED` is a missing/failed gate decision, while `FAILED` denotes an evaluation run failure; promotion returns `BLOCKED` with a reason and only `PROMOTED` after all gates pass. Never infer quality from these operational statuses.

## Backup and recovery

Back up as a consistent, timestamped set before any model or data operation. Store backups outside the working tree on access-controlled storage. Never overwrite the only available copy.

**Model and registry:** preserve the model and tokenizer as one matched bundle with `bundle.json`; preserve the whole registry directory, including both manifests, all immutable version directories, and history. Hash every file. Copy a candidate backup into a fresh staging directory, then run `verify_bundle` against the copied bundle and compare the manifest and artifact hashes before considering any switch. A single active manifest file replacement is atomic, but active and registry JSON updates are not one transaction. After interruption, inspect the backups and active bundle before using the explicit `python -m mlops.promotion.registry recover` command; it mutates registry metadata. Do not run recovery against a live or unbacked registry.

**SQLite feedback database:** use SQLite’s online backup API while the application may be active; do not copy only the main `.db` file while writes may be in progress. Example (replace the destination with a new protected timestamped path):

```powershell
python -c "import sqlite3; s=sqlite3.connect('file:mlops/data/predictions.db?mode=ro', uri=True); d=sqlite3.connect(r'C:\secure-backups\predictions-YYYYMMDD-HHMM.sqlite'); s.backup(d); d.close(); s.close()"
python -c "import sqlite3; c=sqlite3.connect('file:C:/secure-backups/predictions-YYYYMMDD-HHMM.sqlite?mode=ro', uri=True); print(c.execute('PRAGMA quick_check').fetchone()[0]); c.close()"
Get-FileHash -Algorithm SHA256 'C:\secure-backups\predictions-YYYYMMDD-HHMM.sqlite'
```

Expected SQLite integrity result is `ok`. For restoration, restore to a new staged database path, run `PRAGMA quick_check`, verify the expected schema and compare the backup hash. Stop the API before changing `PHISHGUARD_DATABASE_PATH` to the staged copy and restart it; retain the original database. Do not run a schema migration against the only copy.

**MLflow:** preserve `mlflow.db` together with the referenced local artifact store (`mlartifacts/`) and any relevant run artifacts/configuration. Use a consistent SQLite backup and retain paths referenced by run metadata. The configured helper uses a local tracking service and can start it; do not invoke it for a read-only audit.

**Dataset and DVC:** preserve the workspace dataset snapshot, `.dvc` pointer/config, inventory reports, and any relevant cache snapshot as separately named copies with SHA-256 manifests. Verify each copy against its recorded hash before use. Current workspace and pointer differ; cache training bytes are NUL-filled. Do not restore, overwrite, `dvc add`, or declare either side authoritative as a recovery action.

## Evidence blockers and required owners

| Gate | Evidence still missing | Provider and validation procedure |
|---|---|---|
| Authoritative training source | Immutable authorized dataset version, acquisition/sampling/label documentation, signed or trusted per-file hashes | Dataset custodian provides archive and attestation; stage via `mlops.dvc.staging_import`, review original files, exclusion report, checksums, and strict validation, then use separate operator approval. |
| Workspace/DVC reconciliation | Evidence explaining the current workspace hashes, NUL cache train file, extra file, and expected tracked file set | Repository/data maintainer identifies the intended version; compare its manifest against workspace and pointer/cache inventories. No checkout/pull/add before this review. |
| Incumbent history | Training and checkpoint-selection record bound to source/split fingerprints, code revision, tokenizer, model hash and MLflow run | Model owner or experiment custodian supplies signed/access-controlled run evidence; reconcile artifacts and hashes with frozen bundle and MLflow metadata. Unknown remains unknown otherwise. |
| Overlap independence | Complete exact/canonical/hostname/registrable-domain scans plus pinned PSL bytes, release ID, and checksum | Data/evaluation reviewer supplies approved PSL snapshot and resources; run the disk-backed scan against every known training/selection source and retain its input fingerprint/report. Requires explicit resource approval due dataset size. |
| Independent evaluation | Prospective or separately curated examples, immutable version, verified labels, source methods, observation/verification timestamps and exclusions | Evaluation owner supplies reviewed records/protocol; run `mlops.evaluation.prospective` and then the independent candidate evaluator. Temporal separation alone does not resolve unknown historical incumbent use. |
| Operational thresholds/promotion | Risk-approved validation trade-offs and limits for both classes, latency, and regression; complete candidate provenance | Product/risk owner approves limits from trusted validation evidence; one-time final holdout stays separate. Policy remains disabled until signed review and all existing gates pass. |

Exact source-import, prospective-evaluation, and overlap command formats are in [dataset acquisition](DATASET_ACQUISITION.md), [prospective evaluation](PROSPECTIVE_EVALUATION.md), and [overlap analysis](OVERLAP_ANALYSIS.md). These commands require operator-supplied evidence; none is available in this checkout.

## Troubleshooting and safe-operation decision

- **API unreachable:** check whether the local process is running and port 8000 is available. Do not open the port to a network to work around a local configuration problem.
- **API reachable, model degraded:** inspect local artifact availability and active manifest/bundle hashes offline. Public health responses intentionally omit local paths. Do not copy a tokenizer or model from another version into the active bundle.
- **Prediction 400/503/500:** classify respectively as invalid input, unavailable model, or inference failure. Keep full URLs and exception traces out of tickets/logs; reproduce with a reserved `.invalid` URL and temporary database.
- **Feedback accepted but not eligible:** expected until an authorized reviewer verifies the label with provenance. Review `preview-batch`; never edit eligibility fields directly.
- **Registry appears inconsistent:** stop serving processes, preserve the entire directory, compare active and registry manifests plus bundle hashes, then follow the interrupted-operation recovery procedure. Do not delete temporary or orphan version directories before review.
- **DVC validation/status differs:** stop. Preserve reports and hashes; the mismatch is an open evidence issue, not permission to repair or restore.

Before an operation, verify all answers are yes: Is the input source authorized and fingerprinted? Is every write directed to a new staging path? Are current artifacts/database backed up and hash-recorded? Is the operation required and bounded? Can failure leave the incumbent and original data untouched? Does an operator review the result before activation? If any answer is no, do not run the operation.
