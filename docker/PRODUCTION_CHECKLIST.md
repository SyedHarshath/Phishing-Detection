# Phase 7 release checklist

Before releasing the image:

- Docker build succeeds.
- Approved model and tokenizer artifacts are restored before build.
- `/health` returns `model_loaded=true`.
- `/predict` returns prediction, confidence, model version and prediction id.
- `/feedback` still works.
- `/predictions` still works.
- Restarting the container preserves SQLite data.
- The training dataset is not inside the image.
- Secrets are not copied into the image.
- The extension still reaches `127.0.0.1:8000`.
- The served model version matches the approved artifact.

Release flow:

DVC -> train/evaluate -> MLflow -> approve model -> Docker build -> smoke test
-> CI/CD
