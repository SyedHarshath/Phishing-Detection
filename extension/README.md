# OPTIMUS Chrome extension professor demo

## What it does

After a user accepts the first-use data notice, the service worker sends
eligible HTTP/HTTPS navigation URLs to `POST http://127.0.0.1:8000/predict`,
except the listed search domains. The API records each prediction and full URL
in its configured SQLite database. The page content script displays a result
and feedback buttons; the popup also has a **Scan URL** action. Concurrent
duplicate scans of the same URL share an in-flight request. Feedback is stored
as an unverified user claim.

Avoid private pages: full URLs including paths, queries, and any fragment sent
by Chrome are transmitted and stored. A submitted feedback URL may be reviewed
by an authorized human reviewer. There is no automatic database retention or
deletion. Disabling scanning in the popup stops future requests but does not
remove already stored server records.

## 1. Start FastAPI with a separate demo database

From the repository root in PowerShell, use a database path separate from both
the project database and prior demo database:

```powershell
$env:PHISHGUARD_DATABASE_PATH = Join-Path $env:TEMP 'optimus-professor-demo\predictions.sqlite'
python -m uvicorn backend.app.main:app --host 127.0.0.1 --port 8000
```

Keep this terminal open. The API uses `PHISHGUARD_DATABASE_PATH` captured when
`backend.app.database` is imported. If it is unset, the default is
`mlops\data\predictions.db`. Set an absolute path as above before starting the
server. The API loads the current serving model at startup.

The predictor loads lazily on the first readiness, model-info, or prediction
request. API startup initializes/migrates the selected SQLite schema.
Check API reachability in another PowerShell window:

```powershell
Invoke-RestMethod http://127.0.0.1:8000/health
Invoke-RestMethod http://127.0.0.1:8000/model-info
```

## 2. Install the unpacked extension

1. Open `chrome://extensions`.
2. Turn on **Developer mode**.
3. Choose **Load unpacked** and select the extracted `extension` directory.
4. Pin OPTIMUS to the toolbar.
5. Open a harmless public test page using HTTP or HTTPS, such as `https://example.com/`.

The extension scans HTTP/HTTPS navigation after consent. The popup's
**Scan URL** button makes a scan and shows class, confidence, and model version.
The feedback controls are in the result UI
injected into the web page, not the popup. Select **Report as Phishing** or
**Report as Legitimate** to submit the displayed prediction's feedback.

## 3. Confirm persistence without displaying URLs

After submitting feedback, run this read-only aggregate query in the second
PowerShell window:

```powershell
$db = Join-Path $env:TEMP 'optimus-professor-demo\predictions.sqlite'
@'
import sqlite3, sys
from pathlib import Path
p = Path(sys.argv[1]).resolve()
c = sqlite3.connect(p.as_uri() + "?mode=ro", uri=True)
print("prediction rows:", c.execute("SELECT COUNT(*) FROM predictions").fetchone()[0])
print("feedback rows:", c.execute("SELECT COUNT(*) FROM predictions WHERE feedback IS NOT NULL").fetchone()[0])
print("verification states:", c.execute("SELECT verification_status, COUNT(*) FROM predictions GROUP BY verification_status").fetchall())
print("training-eligible rows:", c.execute("SELECT COUNT(*) FROM predictions WHERE training_eligible=1").fetchone()[0])
c.close()
'@ | python - $db
```

Expected: prediction and feedback counts increase; feedback submitted through
the API remains pending verification and training-ineligible. Do not verify
demo feedback without independent supporting evidence.

## Development and production API URLs

The unpacked development source uses `http://127.0.0.1:8000`. Build a separate
production ZIP only after the HTTPS API and public privacy-policy URLs exist;
see [`docs/EXTENSION_RELEASE.md`](../docs/EXTENSION_RELEASE.md). The builder
stages a copy, substitutes the API origin and privacy link, updates the API
host permission, and prints the ZIP hash. It refuses to overwrite an existing
file. The manifest uses `tabs` for tab/navigation reading and `storage` for the
explicit consent setting.

## Troubleshooting

- **“API offline”**: check the backend terminal, port 8000, and
  `http://127.0.0.1:8000/health`. Confirm the environment variable was set in
  the same terminal before starting Uvicorn.
- **API responds but model is unavailable**: inspect `/health` and
  `/model-info`; restarting FastAPI reloads the model selected at startup.
- **No page feedback controls**: refresh an ordinary HTTP/HTTPS page after the
  backend is running. Internal browser pages are not injected; listed search
  domains are skipped by the service worker. Confirm the extension is enabled
  and reloaded at `chrome://extensions`. The legitimate-result notice closes
  after five seconds, so submit feedback while it is visible or refresh the page.
- **Prediction succeeds but feedback fails**: keep the page open and check the
  backend response. The feedback request needs the `prediction_id` returned by
  that prediction; it cannot update a missing ID.

Stop FastAPI with `Ctrl+C`. No public backend or Chrome Web Store publication
is part of this demo.
