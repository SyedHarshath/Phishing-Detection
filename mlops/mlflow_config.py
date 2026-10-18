import os
import subprocess
import sys
import time
from pathlib import Path

import mlflow
import requests

os.environ.setdefault("PYTHONIOENCODING", "utf-8")
if hasattr(sys.stdout, "reconfigure"):
    try:
        sys.stdout.reconfigure(encoding="utf-8")
    except Exception:
        pass
if hasattr(sys.stderr, "reconfigure"):
    try:
        sys.stderr.reconfigure(encoding="utf-8")
    except Exception:
        pass

TRACKING_URI = "http://127.0.0.1:5000"
EXPERIMENT_NAME = "OPTIMUS"


def _mlflow_server_ready() -> bool:
    try:
        response = requests.get(f"{TRACKING_URI}/health", timeout=2)
        return response.status_code == 200
    except requests.RequestException:
        return False


def _start_local_mlflow_server() -> None:
    project_root = Path(__file__).resolve().parents[1]
    mlflow_exe = project_root / "mlflow_venv" / "Scripts" / "mlflow.exe"
    if not mlflow_exe.exists():
        raise FileNotFoundError(f"MLflow executable not found: {mlflow_exe}")

    subprocess.Popen(
        [
            str(mlflow_exe),
            "server",
            "--backend-store-uri",
            "sqlite:///mlflow.db",
            "--default-artifact-root",
            "mlartifacts",
            "--host",
            "127.0.0.1",
            "--port",
            "5000",
        ],
        cwd=str(project_root),
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        creationflags=getattr(subprocess, "CREATE_NEW_PROCESS_GROUP", 0),
        env=os.environ.copy(),
    )

    for _ in range(30):
        time.sleep(1)
        if _mlflow_server_ready():
            return

    raise RuntimeError("MLflow server did not become ready on http://127.0.0.1:5000")


def configure_mlflow():
    if not _mlflow_server_ready():
        _start_local_mlflow_server()

    mlflow.set_tracking_uri(TRACKING_URI)
    mlflow.set_experiment(EXPERIMENT_NAME)
    return TRACKING_URI