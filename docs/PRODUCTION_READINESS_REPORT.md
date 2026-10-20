# OPTIMUS production readiness report

**Audit date:** 2026-10-20  
**Decision:** **Not ready; blockers remain.**  
**Release stage:** Ready for local production-build verification only; not ready for private cloud testing or Chrome Web Store submission.

## Summary

Safe preparation changes were made for the API, Docker image, extension consent, and allowlisted package builder. The development extension ZIP was created and structurally validated. No cloud deployment, public service, production model switch, training, or real-data database write occurred.

| Area | Status | Evidence / remaining work |
|---|---|---|
| API configuration | PARTIAL | `backend/app/config.py` validates environment, database path, model pair, log level, and URL limit. Production requires an absolute database path and explicit model source/version. No actual production configuration was supplied or loaded. |
| Model readiness | PARTIAL | `/live` is liveness; `/ready` returns 503 if model load fails; `/health` preserves the existing degraded response. API routes were tested with mocked readiness, not with a live service. |
| Request handling | PARTIAL | URLs are limited to 4,096 characters by default and URL-embedded credentials are rejected. There is no application rate limiter or body-size limit for oversized JSON envelopes; configure these at the hosting layer. |
| Docker | PARTIAL | Docker honors `PORT`, uses an explicit runtime-file allowlist, includes the API's `mlops.data.lifecycle` dependency, stores SQLite outside the mounted module tree, and performs build-time lifecycle-import and model/tokenizer-load checks. Docker was unavailable, so image build/start was not verified. |
| SQLite persistence | BLOCKED | Production mode rejects relative database paths. Render requires an approved persistent disk and writable mount; none exists. SQLite supports only the documented single-instance deployment. Application schema initialization is additive but runs during API startup. |
| Extension consent | PARTIAL | The popup now explicitly describes full-URL transmission for qualifying automatic page visits, exclusions, server storage, and reviewer access. Consent version was bumped to require renewed acceptance; automatic, popup, and feedback requests remain gated. JavaScript/package tests passed, but Chrome UI behavior and store pre-install disclosures were not manually verified. |
| Extension package | PARTIAL | A remediated development ZIP was built, allowlisted, and CRC-checked. The production builder validates DNS hostname syntax, ports, credentials, URL components, and reserved placeholders, but cannot prove hosting/reachability. Production ZIP intentionally not built because no actual HTTPS API or public policy URL is available. |
| Privacy and security | BLOCKED | Draft discloses URL collection, storage, reviewer access, and absent deletion. Public policy host, operator contact, approved retention/deletion process, storage encryption, hosting log practices, rate limiting, and abuse response remain unconfirmed. |
| Model integrity and MLOps | BLOCKED | Existing dataset/DVC mismatch, unknown incumbent training/checkpoint history, missing trusted independent evaluation evidence, and unapproved policy thresholds remain unresolved. Existing safeguards were preserved. |
| Dependencies | PARTIAL | Docker uses pinned Python 3.10/TensorFlow 2.10.0/NumPy 1.23.5 requirements. General development requirements remain loose, and the base image tag is not digest-pinned. No package was installed or upgraded during this work. |

The extension now requires a consent action before sending URLs. This is a necessary change, not a store-compliance determination: the 2026 Chrome Web Store update says collected data must be strictly necessary for the disclosed purpose and collection must be prominently disclosed; enforcement began August 1, 2026. The actual product listing, public policy, permissions rationale, and consent flow still require owner review. See the [current policy](https://developer.chrome.com/docs/webstore/program-policies/policies) and [2026 update](https://developer.chrome.com/blog/cws-policy-updates-2026?hl=en).

## Verification performed

All test fixtures used temporary databases/artifacts or mocks. No command used the project prediction database as its test database.

| Command | Result |
|---|---:|
| `python -m unittest discover -s test -p test_operational_safety.py -v` | 15 passed |
| `python -m unittest discover -s test -p test_chrome_extension_package.py -v` | 4 passed |
| `python -m unittest discover -s test -p test_label_lifecycle.py -v` | 15 passed |
| `python -m unittest discover -s test -p test_retraining_worker.py -v` | 2 passed |
| `python -m unittest discover -s test -p test_retraining_dispatch.py -v` | 10 passed; worker/promotion functions used mocks and temporary SQLite fixtures |
| `python -m unittest discover -s test -p test_candidate_evaluation.py -v` | 3 passed; synthetic probabilities/fixtures only |
| `python -m unittest discover -s test -p test_docker_runtime_context.py -v` | 2 passed; staged runtime imports and schema migration used a temporary database |
| `python -m unittest discover -s test -p test_chrome_extension_package.py -v` (remediation run) | 7 passed; temporary ZIPs and URL syntax fixtures only |
| `python -m unittest discover -s test -p test_retraining_dispatch.py -v` (remediation run) | 11 passed; dispatch enabled explicitly only inside synthetic database fixtures |
| `python -m unittest discover -s test -p test_retraining_worker.py -v` (remediation run) | 2 passed |
| `python -m unittest discover -s test -p test_label_lifecycle.py -v` (remediation rerun) | 15 passed; synthetic fixtures explicitly opt in to job creation |
| `node --check extension/background.js` | passed |
| `node --check extension/content.js` | passed |
| `node --check extension/popup.js` | passed |
| `git diff --check` | exit 0; only Git line-ending conversion warnings |

The full suite was not run because it includes an actual TensorFlow fit on a synthetic fixture and temporary registry-promotion tests, while this task prohibited training/promotion operations. The focused tests above avoid model fitting and actual registry writes.

The current remediated development package is [optimus-chrome-extension-dev-remediated-v2-20261020.zip](../dist/optimus-chrome-extension-dev-remediated-v2-20261020.zip), SHA-256 `9221bb1cc246d84d568bbd550258992d582490b06bacc29e080fa7926e8821b0`. It contains seven allowlisted runtime files, including the manifest icon, and its ZIP CRC passed. It targets local loopback and is **not** a production package. Earlier ZIPs were left untouched.

Docker was not installed, so no image build, model load inside the image, container startup, or live `/live`, `/ready`, and `/predict` smoke test occurred. The staged runtime test verified import/schema migration in a temporary directory and database; it is not a substitute for Docker. No production extension ZIP was generated, no Chrome installation was attempted, and no cloud resources were created.

The post-test model hash remains `f12c216a8e840cbf099288e7b2350acfd956d71dd2f0478da79ad97402558365`; the tokenizer hash remains `c2f3fa9724eda654478e1bc65ddae804f78f9bc85ce23f6711d315a1b91c4a1c`. Both match the earlier recorded hashes in `MLOPS_AUDIT.md`. The default active registry manifest is absent. These hashes do not prove training provenance. The database was not queried or hashed; all database test fixtures used temporary paths, and the project demo database was not a test target.

## Critical blockers and next actions

1. **Choose a real host and deployment plan.** Provision only after approving plan/cost, domain, region, one-instance SQLite constraints, and persistent-disk mount. Set an absolute writable `PHISHGUARD_DATABASE_PATH`; back up before the startup schema initializer runs. Confirm that storage is encrypted at rest and verify restore behavior.
2. **Resolve model and dataset evidence.** Obtain the authorized incumbent model/tokenizer release with trusted per-file checksums and training history. Reconcile the current dataset with its DVC pointer/cache. Do not use current file hashes as proof of historical provenance.
3. **Resolve evaluation and policy.** Obtain a genuinely independent, trusted labeled evaluation source, document overlap review and incumbent history, and obtain risk-approved thresholds. Do not enable promotion or retraining until these gates pass.
4. **Protect public endpoints.** Configure HTTPS, hosting-layer request/body limits, per-client rate limiting, abuse monitoring, restricted history access, and incident response. `/predict` and `/feedback` are unauthenticated, and the extension cannot safely contain a shared secret.
5. **Complete privacy governance.** Confirm responsible entity/contact, provider/subprocessor processing, access, retention/deletion, and at-rest encryption. Complete and publicly host `PRIVACY_POLICY_DRAFT.md`, ensure pre-install listing disclosures and affirmative consent meet current store policies, then build with those real URLs.
6. **Build and test on the selected host.** Docker build was not possible here. Test the image with approved artifacts, writable persistent storage, `/ready`, backups/restores, log review, timeouts/rate limits, and single-instance deployment before any external user data is sent.

See `PRODUCTION_DEPLOYMENT.md`, `EXTENSION_RELEASE.md`, and `CHROME_WEB_STORE_CHECKLIST.md` for the manual procedure. The project must not be described as production-ready until these blockers have verified evidence.
