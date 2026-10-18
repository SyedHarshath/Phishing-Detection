# MLOps audit (2026-10-18)

## Scope and observed baseline

The checkout contains a Streamlit UI (`src/app.py`), a FastAPI service (`backend/app`), a Chrome extension (`extension`), shared TensorFlow inference (`ml/prediction.py`, `ml/preprocessing.py`), smoke training/evaluation (`src/train_smoke.py`, `src/evaluate_smoke.py`), dataset inspection (`mlops/dvc`), MLflow helpers, and SQLite monitoring. The extension scans HTTP(S) tab navigations except configured search domains and posts the URL to `/predict`; it attaches a feedback action to each UI result.

The prediction API lazily loads `test_results/cnn_bilstm/best_model.h5` with `ml/artifacts/tokenizer.json`; tokenization lowercases URLs and pads/truncates to 200. Its class order is `phishing`, `legitimate`. `src/app.py` is a separate Streamlit path with hard-coded `D:\dephides` artifact locations and a trusted-domain bypass; it is not the API implementation.

The existing SQLite `predictions` table contains 77 rows at audit time. It stores raw URLs, prediction, confidence, model version, a feedback label and timestamp. Feedback is user-reported and currently overwritable; it is not ground truth. DB SHA-256 at audit: `0e4ce3db4cdb0a3c43555caed74f207f9e64379df0f69ac84b45b77dd0ad756d`.

The three local dataset split sizes are 326,952,092; 94,967,444; and 46,773,408 bytes. The streaming scan found 3,641,984 / 1,045,772 / 515,080 valid parsed records and respectively 2 / 3 / 0 malformed rows. This differs from the older status report that described a null-filled training split. `.dvc/config` is empty and no cache remote is configured. DVC 3.67.1 resolves its default site cache to `C:\ProgramData\iterative\dvc\Cache`, outside this workspace's writable area; this explains the SQLite open failure in the managed sandbox. A read-only `dvc status` succeeded with a process-only `DVC_SITE_CACHE_DIR=.dvc/tmp/diagnostic-site-cache` override. It reported local computed directory MD5 `aea1efa4e032d893296ea75d6ce31e1a.dir` versus pointer `6e5cc66e19ab49115a2d14ac50f053f0.dir` (pointer size 468,693,266, four files). Pipeline stages are dirty and `dvc.lock` is absent. The local directory contains `train.txt`, `val.txt`, `test.txt`, `meta.txt`, and `tain_note.txt`; do not restore from cache until provenance and the intended file set are established. No checkout, pull, repro, or dataset/cache replacement was run.

Phase 5 read-only inspection found the four cache blobs matching the DVC pointer. In particular, the pointer-matching cache `train.txt` is 326,952,092 NUL bytes and has no parseable records; it does not match the readable workspace train split. The workspace split files differ from all four pointer MD5 values, and `tain_note.txt` is extra. The mismatch’s cause is unknown. The local README-linked [DEPHIDES upstream description](https://github.com/ebubekirbbr/dephides/blob/main/README.md) and Drive access-request note give plausible source clues, but neither binds exact current bytes to an immutable source revision. See [data provenance decision](DATA_PROVENANCE_DECISION.md) and [evaluation readiness](EVALUATION_READINESS.md). The full disk-backed duplicate scan remains unrun; neither hostname-only counts nor fast inventory is an overlap clearance.

Production model SHA-256: `f12c216a8e840cbf099288e7b2350acfd956d71dd2f0478da79ad97402558365`. Tokenizer SHA-256: `c2f3fa9724eda654478e1bc65ddae804f78f9bc85ce23f6711d315a1b91c4a1c`. These artifacts were not changed. These hashes are local audit evidence, not a trusted signed provenance chain.

## Findings

| Area | Observed state / risk |
|---|---|
| Inference | Lazy singleton model load; `/health`, `/model-info`, `/predict`, `/feedback`, `/predictions`. No API authentication or request timeout configuration. Exception response can expose internals. |
| Logging/privacy | SQLite stores full URL including query; no retention, correlation ID, latency, event UUID, or migration system. `/predictions` returns full URLs without auth. |
| Feedback | User claims now remain pending; reviewer CLI controls verification/rejection/dispute and writes an audit trail. No trusted external verification integration exists. |
| Data | Local TSV splits are readable but have malformed records. Existing loader silently skips malformed rows and samples each split. Existing inspector reports parseability, not URL/domain leakage. New strict validator fails closed and reports exact URL/domain overlap. |
| Training | Bounded smoke candidate trainer, train-only tokenizer, validation checkpointing, test metrics, separate `artifacts/` output. It is explicitly limited to 1–3 epochs and is not full production retraining. No incumbent comparison/promotion/rollback. |
| MLflow | Configuration hardcodes localhost and starts a local process; monitoring/training can create runs, but endpoint/artifact retrieval and environment behavior were not verified. |
| DVC | Workspace-local site cache override enables read-only status. Pointer/local dataset mismatch remains; pipeline is dirty and has no lockfile. No DVC data operation was run. |
| Monitoring | Metrics include prediction and user-feedback agreement. That agreement is not model accuracy because feedback is unverified. No established drift thresholds or retraining trigger. |
| Extension | Direct loopback HTTP API, no request timeout, endpoint is fixed. Feedback is an unverified report. |
| Tests | Existing unittest suite and added strict-validator fixtures. A full suite may run a TensorFlow smoke fit; do not use it where compute/time is constrained. |

## Verification performed

- Read-only schema inspection: existing `predictions` table, 77 rows.
- Read-only dataset scan: `python -m mlops.dvc.inspect_dataset` reported parseable splits with malformed rows as above.
- `dvc status`: default failed because the site cache resolves outside writable workspace; process-local cache override succeeded and exposed dataset/pipeline drift.
- Captured model, tokenizer, and DB hashes above.
- Loaded the production model/tokenizer in memory and predicted a synthetic `.invalid` URL successfully (phishing, 98.13%; this is a functional smoke result, not accuracy evidence).
- Exercised FastAPI `/health`, `/predict`, `/feedback`, and `/predictions` with `TestClient` and a temporary database; all returned 200. The real database was not migrated.
- No full training, DVC data mutation, or MLflow server operation was performed.

## Remaining critical limitations

Verified-label lifecycle and retraining/evaluation/promotion components now exist, but the incumbent’s training dataset and checkpoint-selection history remain unknown. Local MLflow run artifacts for the production model and tokenizer were checksum-verified against the active files; run metadata does not provide dataset fingerprints or prove evaluation independence. No MLflow service was started or queried. Evaluation and promotion are blocked on incumbent history, a frozen independently labeled evaluation source, overlap evidence (including PSL-aware registrable domains), and configured policy limits. Keep the existing splits unchanged while resolving source provenance.

## Phase 6 follow-up (2026-10-18)

External source review found no recoverable original archive. The DEPHIDES README and paper describe the source families and collection approach; the README points to an access-controlled Drive, and neither source provides hashes tying the current files to the released set. The current PhishTank feed is hourly and cannot reproduce the paper’s stated 2018 positives. Common Crawl has versioned monthly snapshots, but the original version/ranking input and sample seed are absent. See `docs/DATASET_ACQUISITION.md`.

The current production model, tokenizer, preprocessing module and predictor code were copied to `mlops/promotion/legacy-freeze-20261018T160840Z/` with SHA-256 verification. Freeze time is `2026-10-18T16:08:40.134246+00:00`; its manifest preserves the historical training provenance as unknown. No active artifact changed.

An offline staging importer, prospective evaluation record builder, and pinned-PSL overlap scanner were added. They were tested with synthetic fixtures only; no dataset, PSL or prospective labels were available, so no real import, full production scan or evaluation report was produced. DVC mismatch remains unresolved and the incumbent history remains unknown.
