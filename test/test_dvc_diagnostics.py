import unittest
from pathlib import Path
from unittest.mock import patch

from mlops.dvc.diagnostics import diagnose


class DvcDiagnosticsTests(unittest.TestCase):
    @patch("mlops.dvc.diagnostics.shutil.which", return_value=None)
    def test_missing_dvc_has_clear_result(self, _which):
        result = diagnose(Path("."))
        self.assertFalse(result["available"])
        self.assertIn("not found", result["error"])

    @patch("mlops.dvc.diagnostics.subprocess.run")
    @patch("mlops.dvc.diagnostics.shutil.which", return_value="dvc")
    def test_status_uses_workspace_site_cache_override(self, _which, run):
        run.return_value.returncode = 0
        run.return_value.stdout = "clean"
        run.return_value.stderr = ""
        result = diagnose(Path("C:/workspace"))
        self.assertTrue(result["ok"])
        env = run.call_args.kwargs["env"]
        self.assertEqual(env["DVC_SITE_CACHE_DIR"], str(Path("C:/workspace") / ".dvc" / "tmp" / "diagnostic-site-cache"))


if __name__ == "__main__":
    unittest.main()
