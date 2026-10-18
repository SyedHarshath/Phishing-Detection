# Data provenance decision (2026-10-18)

## Decision

**Dataset provenance is unresolved. Real-data retraining is blocked.** No DVC restore, dataset download, data rewrite, training, or promotion was performed. The existing workspace and DVC cache were inspected read-only.

## Verified facts

- Git history contains only the initial repository commit (`c415837`). There is no committed history here that explains how the current files or incumbent were produced.
- `dataset/big_dataset.dvc` records directory object `6e5cc66e19ab49115a2d14ac50f053f0.dir`, 468,693,266 bytes, four files. No DVC remote is configured and `dvc.lock` is absent.
- All four pointer blobs and the directory object are present in the local DVC cache and their content matches the pointer entries. The cached `train.txt` is 326,952,092 NUL bytes and parses as zero records. This is the snapshot described by the pointer, but it is unusable as the project’s label/URL training split.
- Workspace files differ from every pointer MD5; the workspace also contains untracked `tain_note.txt`. Workspace train data parses as 3,641,984 records with two malformed lines. This identifies the mismatch but does not explain when or why it occurred.
- Fast streaming inventories (including SHA-256, DVC MD5, line/label/malformed counts) completed for both workspace and cache snapshot. Latest corrected reports: [workspace JSON](../mlops/dvc/inventories/inventory-20261018T145344Z-d628cad8.json), [workspace Markdown](../mlops/dvc/inventories/inventory-20261018T145344Z-d628cad8.md), [cache JSON](../mlops/dvc/inventories/inventory-20261018T145506Z-8bf3a323.json), [cache Markdown](../mlops/dvc/inventories/inventory-20261018T145506Z-8bf3a323.md). Earlier inventories are retained as audit artifacts; the latter two are the corrected parser runs.
- Full disk-backed duplicate/overlap validation was **not run**. Registrable-domain analysis was unavailable because no pinned public suffix list or PSL-aware dependency is installed.
- Local `tain_note.txt` contains a Google Drive access-request link: `https://drive.google.com/drive/folders/13zj-HYEmYAyScb1nd4Lf329VJIHIUvhv?usp=drive_link`. A note and link do not provide the data bytes, a versioned manifest, or a verifiable chain to the current files.
- The public [DEPHIDES README](https://github.com/ebubekirbbr/dephides/blob/main/README.md) describes PhishTank and Common Crawl as sources and lists matching big-split record counts. This is a plausible upstream description, not proof that the inspected bytes came from that source. No exact digest or immutable dataset revision ties the README to this workspace or cached snapshot.
- The Kaggle path in `src/dl.py` is a script configuration clue, not evidence that the deployed incumbent was trained from those bytes.
- `dvc status` ran read-only with a workspace-local temporary site-cache override. It reports the workspace output and pipeline as dirty; it does not establish an authoritative side. Do not run checkout/pull to resolve this: the matching cache train blob is all NULs.
- `.dvc/config` has no remote. DVC diagnoses dirty data and stages. It distinguishes current workspace bytes from the recorded pointer; it cannot establish original source or reproducible training provenance.

## Incumbent and MLflow evidence

The local `mlflow.db` was queried in SQLite read-only mode. It contains eight runs; there are finished model and monitoring runs, plus a running monitoring run. Local artifact files exist for the model run and match the production model and tokenizer SHA-256 values recorded in `docs/MLOPS_AUDIT.md`. Thus those artifacts can be verified locally. The configured tracking URI is `http://127.0.0.1:5000`; no service was contacted or started, so remote retrieval was not verified.

The available model-run parameters/tags identify architecture and model settings and point at a Git commit/repository, but do not record a dataset inventory fingerprint, source revision, train/validation/test fingerprints, or evaluation-use history. The incumbent’s actual training-data history is therefore **unknown**. Existing `raw_test_results.json`, DVC metadata, and a successful artifact checksum do not fill that gap.

## Evidence needed to resolve provenance

1. Obtain the intended dataset from an authorized custodian or source, with immutable revision/version, collection dates, source attribution, label method, and a signed or otherwise trusted manifest listing per-file SHA-256 hashes and schema.
2. Compare those hashes and record counts against both the workspace and DVC cache snapshot. Resolve the NUL-filled pointer train file and extra workspace note with the maintainer before any DVC write or restore.
3. Recover a trustworthy incumbent training record that binds exact train/validation/test fingerprints, preprocessing/tokenizer, code commit, run ID, checkpoint-selection history, and final model/tokenizer hashes. Verify the local MLflow artifact bytes and relevant run metadata against that record.
4. Configure and verify an authorized DVC remote only after the intended version is agreed. Do not treat `dvc add` of the current workspace as provenance repair.

The retraining worker now also requires an independently reviewed provenance manifest binding the source reference and verification evidence to the current train/validation/test SHA-256 values. Missing or stale evidence blocks execution even if a later DVC status happens to be clean. This is a review attestation; the worker does not independently authenticate an upstream custodian’s signature.

## Phase 6 source research and recovery status (2026-10-18)

No original source files were obtained or independently matched. The current upstream DEPHIDES README names PhishTank and Common Crawl, describes the Google Drive as access-controlled, and lists `top100k_cc.txt` as the domain list used for legitimate URLs. The associated paper gives additional collection details: its PhishTank positives were “Valid Phish” entries through August 2018; legitimate URLs were sampled from the top 100,000 domains ranked using Common Crawl, around 30 URLs per domain, and capped at 10 URLs per domain in the final set. These details identify recovery evidence to request but provide no per-file hashes or a revision matching this workspace.

The official PhishTank page currently says its online-valid feeds update hourly and provides submission/verification timestamps; that current feed cannot reproduce the 2018 snapshot. Common Crawl publishes monthly versioned crawls and an URL index with fetch time/registered-domain fields; a crawl capture by itself does not establish that a URL was benign. See [dataset acquisition assessment](DATASET_ACQUISITION.md). The source pages were reviewed 2026-10-18; the mutable DEPHIDES `main` branch was not pinned to a commit SHA, and no downloads were performed.

An offline staged importer now checks manifest-listed source file hashes, preserves the originals separately, writes normalized data and exclusion reports, and keeps operator approval separate. It does not verify source claims cryptographically. A pinned-PSL overlap implementation and prospective temporal evaluation builder are available, but no real data or PSL snapshot was available to run them. Current DVC status and source provenance remain unresolved; no dataset was recovered, changed, or registered.

## Phase 6 implementation verification

- The frozen model, tokenizer, preprocessing and prediction files match the SHA-256 values in the freeze manifest. The active tokenizer also matches the Phase 5 audit hash. The local MLflow database matches its previously recorded SHA-256.
- The workspace train, validation and test files still match the Phase 5 workspace inventory SHA-256 values. They remain distinct from the DVC pointer contents and are not registered as authoritative.
- Focused checks passed for staging import/approval (4), prospective evaluation (5), PSL overlap validation (7), inventory completion markers (6), promotion gates (11), synthetic smoke candidate classification (5), and retraining worker provenance (2). Full suite: 67 tests passed. Tests used synthetic fixtures; no production-data training or full production overlap scan was run.
- `git diff --check` completed without whitespace errors. Git emitted only line-ending conversion notices for existing modified text files.
- The staged importer, temporal evaluation builder, and disk-backed exact/canonical/hostname/registrable-domain audit are ready for operator-supplied sources. A trusted original dataset archive, pinned PSL snapshot, and eligible prospective records are still absent, so source recovery and independent evaluation remain outstanding.
