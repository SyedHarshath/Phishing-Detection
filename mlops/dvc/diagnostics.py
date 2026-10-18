"""Read-only DVC status diagnostic using a workspace-local temporary site cache."""
from __future__ import annotations

import argparse
import os
import shutil
import subprocess
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]


def diagnose(root: Path = ROOT) -> dict:
    executable = shutil.which("dvc")
    if not executable:
        return {"available": False, "ok": False, "error": "DVC executable not found on PATH"}
    cache = root / ".dvc" / "tmp" / "diagnostic-site-cache"
    env = os.environ.copy()
    env["DVC_SITE_CACHE_DIR"] = str(cache)
    try:
        result = subprocess.run([executable, "status"], cwd=root, env=env,
                                capture_output=True, text=True, timeout=180)
    except (OSError, subprocess.TimeoutExpired) as exc:
        return {"available": True, "ok": False, "error": str(exc),
                "site_cache": str(cache)}
    return {"available": True, "ok": result.returncode == 0,
            "return_code": result.returncode, "site_cache": str(cache),
            "stdout": result.stdout, "stderr": result.stderr}


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", type=Path, default=ROOT)
    args = parser.parse_args()
    result = diagnose(args.root)
    print(result.get("stdout", ""), end="")
    print(result.get("stderr", ""), end="")
    if not result["ok"]:
        print(result.get("error", "DVC status failed"))
        return 1
    print(f"DVC status completed using temporary workspace cache: {result['site_cache']}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
