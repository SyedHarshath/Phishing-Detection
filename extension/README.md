# OPTIMUS Chrome Extension

This extension connects the browser to the FastAPI phishing detection API.

## Architecture

```text
Chrome Extension
-> background service worker
-> POST http://127.0.0.1:8000/predict
-> CNN + BiLSTM
-> prediction + confidence + model version
```

## Requirements

Start the FastAPI backend first:

```powershell
python -m uvicorn backend.app.main:app --reload
```

The API should be available at:

```text
http://127.0.0.1:8000
```

## Install in Chrome or Edge

1. Open the browser extensions page.
2. Enable Developer mode.
3. Select Load unpacked.
4. Select this `extension` folder.
5. Pin OPTIMUS to the browser toolbar.
6. Open a normal HTTP/HTTPS website.
7. Click the OPTIMUS extension.
8. Click Scan URL.

## Expected Result

The popup displays:

- PHISHING or LEGITIMATE
- confidence
- model version
- API/model connection status

## Troubleshooting

If the popup says:

```text
API offline - Start FastAPI on port 8000
```

make sure the FastAPI server is running.

If the API is running but prediction fails, open:

```text
http://127.0.0.1:8000/docs
```

and verify that `POST /predict` works there.

Pages such as `chrome://extensions`, `chrome://settings`, and browser internal
pages are intentionally not scanned.
