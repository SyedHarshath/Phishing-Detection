# PhishGuard — Phase 6: DVC

> **Phase 7 safety notice:** the checked-in dataset pointer and local workspace do
> not match, and the pointer-matching cached training file is unusable. Do not
> run `dvc init`, `dvc add`, `dvc checkout`, `dvc pull`, `dvc repro`, or `dvc push`
> against the current files. The general setup examples below are not safe to
> execute for this checkout until an authorized source version is recovered and
> reviewed. See [the provenance decision](../../docs/DATA_PROVENANCE_DECISION.md)
> and [operational readiness runbook](../../docs/PHASE7_OPERATIONAL_READINESS.md).

This phase adds **Data Version Control (DVC)** around the existing PhishGuard dataset.

## What this phase does

- Tracks the large URL dataset with a small `.dvc` pointer instead of committing the dataset to Git.
- Keeps dataset versions tied to Git commits.
- Adds a lightweight dataset validation report.
- Leaves MLflow responsible for experiment tracking/model metadata.
- Keeps the existing SQLite monitoring database and MLflow runs outside DVC.

### Git vs DVC

```text
Git
 ├── source code
 ├── configs / params
 ├── dvc.yaml
 └── dataset/big_dataset.dvc   <-- small pointer

DVC remote/local cache
 └── actual dataset files       <-- large data
```

## 1. Install DVC

From the project root:

```powershell
pip install -r requirements_dvc.txt
```

Verify:

```powershell
dvc --version
```

## 2. Initialize DVC

Only run this once:

```powershell
dvc init
```

This creates the `.dvc/` metadata directory.

## 3. Track the existing dataset

Your project already contains:

```text
dataset/big_dataset/
```

Run:

```powershell
dvc add dataset/big_dataset
```

DVC will create:

```text
dataset/big_dataset.dvc
```

and add the real dataset to the DVC cache.

**Do not manually create or edit the `.dvc` file.** Let DVC generate it.

## 4. Commit the DVC pointer with Git

```powershell
git add .dvc .dvcignore dataset/big_dataset.dvc
git commit -m "Add DVC dataset versioning"
```

The important idea is that Git records the pointer/hash while DVC stores the actual large files.

## 5. Validate the dataset

From the project root:

```powershell
python -m mlops.dvc.validate_dataset
```

This produces:

```text
mlops/dvc/dataset_report.json
```

The report is intended as a sanity check before training.

## 6. Reproduce a dataset version

After switching to another Git commit:

```powershell
dvc checkout
```

or:

```powershell
dvc pull
```

if a DVC remote has been configured.

## 7. Optional: configure a DVC remote

For a real team/CI setup, configure remote storage such as an S3-compatible bucket, Google Cloud Storage, Azure Blob, or another supported backend.

Example pattern:

```powershell
dvc remote add -d storage <REMOTE>
dvc push
```

Do not put cloud credentials into Git.

## Important design decision

Do **not** put `mlruns/`, `predictions.db`, or temporary monitoring output under DVC.

Use:

- **Git** → code/configuration
- **DVC** → datasets and reproducible data artifacts
- **MLflow** → experiments, metrics, model metadata/artifacts
- **SQLite** → application prediction/feedback logs

## Next step

After DVC is initialized, the next MLOps improvement should be a reproducible training pipeline:

```text
DVC dataset
    ↓
data validation
    ↓
training
    ↓
evaluation
    ↓
model artifact
    ↓
MLflow run
    ↓
deployment
```

The existing `src/dl.py` is Kaggle-oriented, so it should be refactored into a local/config-driven training entry point before making it a DVC pipeline stage. This avoids pretending the current training script is reproducible when it still contains environment-specific paths.

## Safe dataset inspection and smoke-test candidate

The reusable binary-safe inspector is:

```powershell
python -m mlops.dvc.inspect_dataset
```

It writes `mlops/dvc/dataset_schema_report.json` without modifying the source
splits. The files were scanned on 2026-10-18 and all three are readable UTF-8
`label<TAB>url` text. The inspector reported 3,641,984 / 1,045,772 / 515,080
parsed records and 2 / 3 / 0 malformed rows in train/validation/test. This
conflicts with an earlier report that training was zero-filled; no automatic
repair was attempted. DVC 3.67.1 status succeeds with a process-only workspace
site-cache override and reports the local directory differs from
`dataset/big_dataset.dvc`; pointer/cache provenance remains unresolved. See
`docs/MLOPS_AUDIT.md`. Do not restore from DVC until the expected files and
provenance are confirmed.

The bounded loader in `src/data_loader.py` streams the selected split and
returns a seeded, approximately class-balanced sample. The smoke trainer fits
the character tokenizer only on its training sample and saves candidate files
only under `artifacts/candidate_cnn_bilstm/`. Its default total sample is
5,000 records and it is capped at three epochs. It never writes to the
production model or tokenizer paths.

After restoring and verifying the training split, the separated DVC stages
are:

```text
validate -> smoke_train_candidate -> evaluate_smoke_candidate
```

The training stage is a small candidate smoke-test, not full-scale production
training. There is intentionally no full-data retraining or model promotion
stage in `dvc.yaml`. The train split described by the current DVC cache causes
the smoke stage to stop with an explicit parser error and a failure report rather than
silently substituting validation or test data.

Phase 5 read-only cache inspection refined that statement: the DVC-pointer-matching cache train blob contains only NUL bytes and is unparseable; the current workspace train split parses 3,641,984 records with two malformed rows. All workspace files differ from the pointer entries. The mismatch is unexplained, and neither side is declared authoritative. Do not restore or re-add data to clear the status.

See [provenance decision](../../docs/DATA_PROVENANCE_DECISION.md) and [evaluation readiness](../../docs/EVALUATION_READINESS.md).

Inventory commands (reports are written separately; source splits are read-only):

```powershell
python -m mlops.dvc.inventory --mode fast --pointer dataset/big_dataset.dvc
python -m mlops.dvc.inventory --mode full --pointer dataset/big_dataset.dvc
python -m mlops.dvc.inventory --mode fast --pointer dataset/big_dataset.dvc --from-dvc-cache
```

Full mode uses a temporary disk-backed SQLite index and is intentionally excluded from unit tests. No pinned public-suffix data is installed, so registrable-domain overlap remains unavailable; hostname overlap is not equivalent.

Use `--psl-file`, `--psl-sha256`, and `--psl-version` with the full inventory/validator command to enable registrable-domain overlap. The report records all three; the checksum must match the supplied list. See [overlap analysis](../../docs/OVERLAP_ANALYSIS.md).

Never import data into the active dataset path. The operator-supplied staging workflow and separate registration approval are documented in [dataset acquisition](../../docs/DATASET_ACQUISITION.md). Staging does not itself resolve DVC drift or enable retraining.
