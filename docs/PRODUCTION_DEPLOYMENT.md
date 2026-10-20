# Production deployment preparation

## Release state

**Not ready for public deployment.** This guide prepares a single-instance Docker deployment, but the actual service must remain private until the blockers in `PRODUCTION_READINESS_REPORT.md` are resolved. No cloud service, public endpoint, or production database has been created by this repository change.

## Runtime and artifact contract

The supported container runtime is Python 3.10 with TensorFlow 2.10.0 and NumPy 1.23.5, pinned in `docker/requirements-docker.txt`. The image expects the existing `test_results/cnn_bilstm/best_model.h5` and `ml/artifacts/tokenizer.json` files at build time. It packages both and explicitly configures their paths and model version. The image does not include datasets, DVC cache, MLflow state, SQLite files, or extension source. The `.dockerignore` allowlist limits the build context to runtime code, the tokenizer, and the model file.

The current hashes recorded in earlier audits are byte identity evidence only; they do not establish trusted training provenance or quality. Obtain the artifacts from an authorized source and compare against an independently reviewed release manifest before a public release. The Docker build was not run as part of this change.

## Local container smoke test

Run from the repository root after verifying that the two required artifact files are present and Docker is available. The `docker run` smoke test below has no host database mount: it puts the database in a temporary container `tmpfs`, uses a dedicated loopback port, and deletes the container and database when stopped. It writes one synthetic `.invalid` prediction row to that temporary database.

```powershell
docker build -t optimus-api:readiness -f docker/Dockerfile .
docker run --rm --name optimus-readiness-test `
  -p 127.0.0.1:18000:8000 `
  --tmpfs /tmp/optimus-readiness-db:rw,noexec,nosuid,size=32m `
  -e PHISHGUARD_DATABASE_PATH=/tmp/optimus-readiness-db/predictions.sqlite `
  optimus-api:readiness
Invoke-RestMethod http://127.0.0.1:18000/live
Invoke-RestMethod http://127.0.0.1:18000/ready
python docker/verify_container.py
```

Expected: `/live` returns `alive`; `/ready` returns `ready` with `model_loaded: true`; the smoke request returns a prediction response. The smoke URL is a reserved `.invalid` fixture and is not a claim about model quality. The database path is explicitly redirected to the temporary `tmpfs`, so this procedure cannot use the project demo database. Stop the foreground container with `Ctrl+C`; `--rm` then removes the container. Do not replace this command with a persistent-volume or host-database mount during isolated validation.

## Render private test setup (manual dashboard only)

Render's Docker service must bind to `0.0.0.0` and the supplied `PORT`; this Dockerfile now honors that variable and defaults to port 8000 for local Compose. In the Render Dashboard, before any deployment:

1. Connect the private repository and select Docker runtime.
2. Set Dockerfile path to `docker/Dockerfile` and build context to the repository root.
3. Review the service plan, region, and cost before creation. TensorFlow memory needs have not been benchmarked here.
4. Attach a persistent disk mounted at `/var/data`, then set `PHISHGUARD_DATABASE_PATH=/var/data/predictions.sqlite`. The database path must be absolute and the runtime user must be able to write to it; verify this on a private test service before sending user data.
5. Set `PHISHGUARD_PREDICTIONS_TOKEN` through the dashboard's secret field only if authenticated history access is needed. Generate the value locally. Do not put it in the extension or repository.
6. Set health-check path to `/ready`. Keep one instance with SQLite. Never configure autoscaling for this database design.
7. Confirm the actual model/tokenizer files were included and that `/ready` is successful before connecting an extension.
8. Keep the service private during smoke testing. Do not send real browsing URLs until consent, privacy, access control, retention, and incident procedures are approved.

Render documentation says only writes beneath a disk's mount path survive deploys/restarts. A persistent disk is available to one service instance and prevents scaling that service to multiple instances; disk-backed deploys also stop the existing instance before bringing up the new one. These properties fit a single SQLite writer but imply downtime and no horizontal scale. Confirm current pricing and plan requirements in the Dashboard before creating a service. [Render web-service port requirements](https://render.com/docs/web-services), [persistent disk behavior](https://render.com/docs/disks).

No `render.yaml` is checked in. A Blueprint would provision or modify external infrastructure and needs an approved service plan, region, persistent disk size, and model artifact delivery decision. Keep those choices in the Dashboard until they are explicit.

## Health semantics and rollback

- `GET /live` reports process liveness and does not require a model.
- `GET /ready` loads/checks the lazy predictor and returns HTTP 503 when the model cannot load. Use it for hosting health checks.
- `GET /health` preserves the existing response contract and returns HTTP 200 with `status: degraded` when the model is unavailable. Do not use it as the platform readiness probe.
- `GET /model-info` returns the loaded model name/version/sequence length without exposing local paths.

Back up the SQLite database using its online backup API to a new protected destination. Back up the entire deployment definition, model/tokenizer release bundle, and checksums together. For rollback, redeploy the previously verified image and verify `/ready` and `/model-info`; do not overwrite individual model or tokenizer files inside a running service. Restore a database only to a new staged path after checking the backup and schema. The startup schema initializer is additive but writes schema changes, so test a backup copy first.

## Security boundary required before public access

`/predict` and `/feedback` are unauthenticated by design for the extension; `/predictions` is disabled unless its shared token is configured and redacts URLs by default. The extension contains no server secret. Before a public endpoint is exposed, configure hosting-layer rate and request-size limits, abuse monitoring, restricted administrative access, HTTPS, encrypted persistent storage, and a reviewed retention/deletion process. No application rate limiter, account authentication, URL deletion interface, or database encryption is currently implemented. Do not use public deployment as the workaround for these gaps.

The database stores submitted URL strings including paths/query data and may include fragments supplied by the browser. Feedback is a user assertion. Authorized operators can review URLs for label verification. See `PRIVACY_POLICY_DRAFT.md`; it must be completed, hosted publicly, and linked from the production extension before any store submission.
