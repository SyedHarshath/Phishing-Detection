# Production deployment preparation

## Release state

**Not ready for public deployment.** This guide prepares a single-instance Docker deployment, but the actual service must remain private until the blockers in `PRODUCTION_READINESS_REPORT.md` are resolved. No cloud service, public endpoint, or production database has been created by this repository change.

## Runtime and artifact contract

The supported container runtime is Python 3.10 with TensorFlow 2.10.0 and NumPy 1.23.5, pinned in `docker/requirements-docker.txt`. The image expects the existing `test_results/cnn_bilstm/best_model.h5` and `ml/artifacts/tokenizer.json` files at build time. It packages both and explicitly configures their paths and model version. The image does not include datasets, DVC cache, MLflow state, SQLite files, or extension source. The `.dockerignore` allowlist limits the build context to runtime code, the tokenizer, and the model file.

Production requires `DATABASE_URL` for PostgreSQL. SQLite remains the local-development and isolated-test backend. When `DATABASE_URL` is set, the API and feedback/retraining lifecycle use PostgreSQL and do not fall back to SQLite after a connection failure. Keep the URL in the hosting secret settings; do not put it in source control or logs.

The current hashes recorded in earlier audits are byte identity evidence only; they do not establish trusted training provenance or quality. Obtain the artifacts from an authorized source and compare against an independently reviewed release manifest before a public release. The Docker build was not run as part of this change.

## Local container smoke test

Run from the repository root after verifying that the two required artifact files are present and Docker is available. The `docker run` smoke test below has no host database mount: it puts the database in a temporary container `tmpfs`, uses a dedicated loopback port, and deletes the container and database when stopped. It writes one synthetic `.invalid` prediction row to that temporary database.

```powershell
docker build -t optimus-api:readiness -f docker/Dockerfile .
docker run --rm --name optimus-readiness-test `
  -p 127.0.0.1:18000:8000 `
  --tmpfs /tmp/optimus-readiness-db:rw,noexec,nosuid,size=32m `
  -e PHISHGUARD_ENV=test `
  -e PHISHGUARD_DATABASE_PATH=/tmp/optimus-readiness-db/predictions.sqlite `
  optimus-api:readiness
Invoke-RestMethod http://127.0.0.1:18000/live
Invoke-RestMethod http://127.0.0.1:18000/ready
python docker/verify_container.py
```

Expected: `/live` returns `alive`; `/ready` returns `ready` with `model_loaded: true`; the smoke request returns a prediction response. The smoke URL is a reserved `.invalid` fixture and is not a claim about model quality. `PHISHGUARD_ENV=test` permits this isolated SQLite smoke test; the database path is redirected to temporary container `tmpfs`, so it cannot use the project demo database. Stop the foreground container with `Ctrl+C`; `--rm` then removes the container. Do not replace this command with a persistent-volume or host-database mount during isolated validation.

## Render private test setup (manual dashboard only)

Render's Docker service must bind to `0.0.0.0` and the supplied `PORT`; this Dockerfile now honors that variable and defaults to port 8000 for local Compose. To configure PostgreSQL in the Render Dashboard:

1. Create a PostgreSQL database in Render and select the same region as the existing backend service. Check current plans, pricing, storage, backups, and region availability in the dashboard.
2. Copy the database's **Internal Database URL** for a service in the same region.
3. Add it to the backend web service as the secret environment variable `DATABASE_URL`. Do not add the URL to this repository or print it in logs.
4. Keep `PHISHGUARD_ENV=production`; remove the old `/tmp/predictions.sqlite` path setting because production requires `DATABASE_URL`.
5. Redeploy the backend. Startup initializes the PostgreSQL schema additively; it does not import any old SQLite records.
6. Check `/live` and `/ready`. To use `/admin/db-status`, set a separate high-entropy `OPTIMUS_DIAGNOSTIC_TOKEN` secret in the dashboard and provide it in the `X-OptimUS-Diagnostic-Token` header. The protected route reports backend, table availability, and count without exposing stored URLs.
7. Submit a synthetic test prediction, confirm the count increases, restart/redeploy, and confirm the row remains. Do this on a private test service before connecting the extension.
8. Set `PHISHGUARD_PREDICTIONS_TOKEN` through the dashboard only if authenticated history access is needed. Keep the service private until consent, privacy, access control, retention, and incident procedures are approved.

Render requires the web service and database to be configured explicitly. Confirm current network, backup, encryption, pricing, and plan behavior in Render's dashboard/docs. [Render PostgreSQL](https://render.com/docs/postgresql), [Render web-service port requirements](https://render.com/docs/web-services).

No `render.yaml` is checked in. A Blueprint would provision or modify external infrastructure and needs an approved service plan, region, persistent disk size, and model artifact delivery decision. Keep those choices in the Dashboard until they are explicit.

## Health semantics and rollback

- `GET /live` reports process liveness and does not require a model.
- `GET /ready` loads/checks the lazy predictor and returns HTTP 503 when the model cannot load. Use it for hosting health checks.
- `GET /health` preserves the existing response contract and returns HTTP 200 with `status: degraded` when the model is unavailable. Do not use it as the platform readiness probe.
- `GET /model-info` returns the loaded model name/version/sequence length without exposing local paths.

Back up PostgreSQL using a reviewed database backup/export procedure and validate restores in a separate database. If migrating from SQLite, use `python -m mlops.data.migrate_sqlite_to_postgres --source <sqlite-file>` with `DATABASE_URL` set in the environment; the utility is opt-in, reads the source in read-only mode, imports supported prediction/lifecycle tables, checks existing primary-key conflicts, and does not run at startup. It cannot recover records from an inaccessible ephemeral filesystem. Back up the deployment definition, model/tokenizer release bundle, and checksums together. For rollback, redeploy the previously verified image and verify `/ready` and `/model-info`.

## Security boundary required before public access

`/predict` and `/feedback` are unauthenticated by design for the extension; `/predictions` is disabled unless its shared token is configured and redacts URLs by default. The extension contains no server secret. Before a public endpoint is exposed, configure hosting-layer rate and request-size limits, abuse monitoring, restricted administrative access, HTTPS, encrypted persistent storage, and a reviewed retention/deletion process. No application rate limiter, account authentication, URL deletion interface, or database encryption is currently implemented. Do not use public deployment as the workaround for these gaps.

The database stores submitted URL strings including paths/query data and may include fragments supplied by the browser. Feedback is a user assertion. Authorized operators can review URLs for label verification. See `PRIVACY_POLICY_DRAFT.md`; it must be completed, hosted publicly, and linked from the production extension before any store submission.
