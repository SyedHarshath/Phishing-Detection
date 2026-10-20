# OPTIMUS professor demonstration

## Retraining lifecycle sandbox

From the repository root in PowerShell:

```powershell
python scripts/demo_retraining_sandbox.py
```

The sandbox writes only below
`%TEMP%\optimus-retraining-demo-sandbox`. To remove only its manifested output
files and create a fresh run:

```powershell
python scripts/demo_retraining_sandbox.py --reset
```

The run demonstrates synthetic feedback, synthetic review audit records, the
real eligibility checks, and registered batch export. The real worker is called
only with `execute=False`; it reports the missing baseline validation,
provenance, and DVC requirements. Therefore this repository cannot safely
demonstrate candidate training, evaluation, approval, or promotion yet. See
[RETRAINING_SANDBOX_DEMO.md](RETRAINING_SANDBOX_DEMO.md) for exact stage status.

## Chrome extension and FastAPI

Use an isolated feedback database so the project and previous demo databases
are not changed. Start from the project root:

```powershell
$env:PHISHGUARD_DATABASE_PATH = Join-Path $env:TEMP 'optimus-professor-demo\predictions.sqlite'
python -m uvicorn backend.app.main:app --host 127.0.0.1 --port 8000
```

Keep the server terminal open. Open `chrome://extensions`, enable Developer
mode, select **Load unpacked**, and choose the extracted extension directory.
To extract the distributable ZIP to a new temporary directory in PowerShell:

```powershell
$extensionDir = Join-Path $env:TEMP 'optimus-professor-extension'
if (Test-Path -LiteralPath $extensionDir) { throw "Choose a new extraction directory: $extensionDir" }
Expand-Archive -LiteralPath '.\dist\optimus-chrome-extension.zip' -DestinationPath $extensionDir
```

Pin OPTIMUS, open a harmless HTTP/HTTPS test page (for example
`https://example.com/`), and inspect the page result or click the toolbar popup's
**Scan URL** action. The popup displays class, confidence, and model version.
Feedback buttons are on the injected page result; select **Report as Phishing**
or **Report as Legitimate**. The API stores a prediction when `/predict` runs
and stores feedback as an unverified claim at `/feedback`.

Inspect counts without printing URLs:

```powershell
$db = Join-Path $env:TEMP 'optimus-professor-demo\predictions.sqlite'
@'
import sqlite3, sys
from pathlib import Path
p = Path(sys.argv[1]).resolve()
c = sqlite3.connect(p.as_uri() + "?mode=ro", uri=True)
print("predictions:", c.execute("SELECT COUNT(*) FROM predictions").fetchone()[0])
print("feedback:", c.execute("SELECT COUNT(*) FROM predictions WHERE feedback IS NOT NULL").fetchone()[0])
print("states:", c.execute("SELECT verification_status, COUNT(*) FROM predictions GROUP BY verification_status").fetchall())
c.close()
'@ | python - $db
```

The extension automatically scans HTTP/HTTPS navigation (except configured
search domains) and stores full submitted URLs locally. Avoid sensitive pages.
Its backend base URL is documented in `extension/README.md`; if changing it,
update both source constants and `manifest.json` host permission, rebuild the
ZIP, then reload the extension.

Build the shareable allowlisted package (the command refuses to overwrite):

```powershell
python scripts/package_chrome_extension.py
```

Output: `dist\optimus-chrome-extension.zip`. It contains only the manifest,
service worker, content script, popup files, and referenced icon. Do not upload
it to the Chrome Web Store or point it at a public backend. The package command
refuses to overwrite an existing ZIP; pass `--output` with a new destination to
build another copy.

## Tests

```powershell
python -m unittest discover -s test -p 'test_retraining_sandbox_demo.py'
python -m unittest discover -s test -p 'test_chrome_extension_package.py'
```

These focused tests use temporary data and do not train or promote a model.
