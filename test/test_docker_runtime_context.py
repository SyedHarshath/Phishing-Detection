import os
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
RUNTIME_MODULES = (
    "backend/app/__init__.py",
    "backend/app/config.py",
    "backend/app/database.py",
    "backend/app/main.py",
    "backend/app/schemas.py",
    "backend/app/services/__init__.py",
    "backend/app/services/prediction.py",
    "ml/__init__.py",
    "ml/prediction.py",
    "ml/preprocessing.py",
    "mlops/__init__.py",
    "mlops/data/__init__.py",
    "mlops/data/lifecycle.py",
    "src/dl_models.py",
)


class DockerRuntimeContextTests(unittest.TestCase):
    def test_dockerfile_copies_required_runtime_modules_and_only_selected_artifacts(self):
        dockerfile = (ROOT / "docker/Dockerfile").read_text(encoding="utf-8")
        dockerignore = (ROOT / ".dockerignore").read_text(encoding="utf-8")
        for relative in RUNTIME_MODULES:
            self.assertIn(f"COPY {relative} ./{relative}", dockerfile)
        self.assertIn("COPY ml/artifacts/tokenizer.json ./ml/artifacts/tokenizer.json", dockerfile)
        self.assertIn("COPY test_results/cnn_bilstm/best_model.h5 ./test_results/cnn_bilstm/best_model.h5", dockerfile)
        self.assertIn("RUN python -c \"from ml.prediction import PhishingPredictor; PhishingPredictor()\"", dockerfile)
        self.assertIn("from mlops.data.lifecycle import initialize_schema, record_feedback", dockerfile)
        self.assertNotIn("COPY ml ./ml", dockerfile)
        self.assertNotIn("COPY backend ./backend", dockerfile)
        self.assertNotIn("COPY ml/artifacts/tokenizer.pkl", dockerfile)
        self.assertNotIn("COPY ml/artifacts/best_model.h5", dockerfile)
        self.assertIn("!mlops/data/lifecycle.py", dockerignore)
        self.assertNotIn("!ml/**", dockerignore)
        self.assertIn("PHISHGUARD_DATABASE_PATH=/var/lib/phishguard/predictions.db", dockerfile)
        compose = (ROOT / "docker/compose.yaml").read_text(encoding="utf-8")
        self.assertIn("phishguard-data:/var/lib/phishguard", compose)
        self.assertNotIn("/app/mlops/data", compose)

    def test_runtime_import_and_database_migration_in_staged_copy(self):
        with tempfile.TemporaryDirectory(prefix="optimus-docker-context-test-") as temp:
            runtime_root = Path(temp) / "image"
            for relative in RUNTIME_MODULES:
                source = ROOT / relative
                self.assertTrue(source.is_file(), relative)
                destination = runtime_root / relative
                destination.parent.mkdir(parents=True, exist_ok=True)
                shutil.copy2(source, destination)
            script = (
                "from backend.app.database import DB_PATH, initialize_database; "
                "from mlops.data.lifecycle import require_schema; "
                "initialize_database(); require_schema(DB_PATH); print('runtime import and schema migration ok')"
            )
            env = os.environ.copy()
            env["PHISHGUARD_ENV"] = "test"
            env["PHISHGUARD_DATABASE_PATH"] = str(Path(temp) / "isolated.sqlite")
            result = subprocess.run([sys.executable, "-c", script], cwd=runtime_root,
                env=env, text=True, capture_output=True, check=True)
            self.assertIn("runtime import and schema migration ok", result.stdout)
            self.assertTrue((Path(temp) / "isolated.sqlite").is_file())


if __name__ == "__main__":
    unittest.main()
