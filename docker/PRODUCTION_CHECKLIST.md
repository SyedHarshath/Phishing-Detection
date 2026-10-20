# Phase 7 release checklist

Before releasing the image:

- Docker build succeeds.
- The image build's runtime import, schema-initializer import, and configured model/tokenizer load checks succeed.
- Model and tokenizer artifacts are obtained through an authorized source and independently hash-checked before build; current training provenance remains unresolved.
- `/ready` returns HTTP 200 with `model_loaded=true`.
- `/predict` returns prediction, confidence, model version and prediction id.
- `/feedback` still works.
- `/predictions` still works.
- The production host has an approved persistent disk and the absolute `PHISHGUARD_DATABASE_PATH` points inside it.
- Restarting the container preserves SQLite data on that disk.
- The training dataset is not inside the image.
- Secrets are not copied into the image.
- The production extension package contains only the explicitly configured HTTPS API origin and hosted privacy-policy URL.
- User notice/affirmative consent, URL storage, reviewer access, retention, deletion, and hosting-provider data handling have been approved and disclosed.
- Hosting-layer rate limiting, request-size restrictions, abuse monitoring, and encrypted persistent storage are verified.
- The served model version and artifact hashes match the reviewed release record.

Release flow:

DVC -> train/evaluate -> MLflow -> approve model -> Docker build -> smoke test
-> CI/CD
