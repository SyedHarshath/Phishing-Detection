# Chrome extension release procedure

## Current behavior and permission rationale

Manifest V3 uses the `tabs` permission to read the active tab URL and observe navigation, `storage` to remember explicit consent, and a narrowly scoped host permission for the configured API origin. The content script is registered on HTTP/HTTPS pages for result notifications and feedback UI. After consent, automatic scanning transmits the full URL, including path, query, and fragment where present, for each qualifying HTTP/HTTPS page visit except the listed search domains. The popup also sends a URL when manually scanned. Successful predictions store full URLs server-side; URLs associated with feedback may be reviewed by an authorized reviewer. Disabling scanning removes consent for future requests but does not delete server records.

The source extension targets the local API at `http://127.0.0.1:8000`. Do not edit this source value to produce a production package. The builder creates an isolated staged copy, substitutes the requested HTTPS API origin and hosted privacy-policy URL, updates the API host permission, checks an allowlist, refuses overwrite, verifies ZIP CRC/content, and prints a SHA-256 checksum.

## Local unpacked test

1. Start FastAPI with an isolated demo database as described in `PROFESSOR_DEMO_README.md`.
2. Open `chrome://extensions`, enable **Developer mode**, choose **Load unpacked**, and select `extension/`.
3. Open the extension popup, review the full-URL and reviewer-access notice, and explicitly accept it before scanning.
4. Scan a harmless test URL, check the class/confidence display, and optionally submit feedback. Feedback remains unverified.
5. Use only the isolated demo database. Revoke scanning in the popup to stop future scans; this does not delete server-side records.

## Production package

Production packaging intentionally requires the actual deployed HTTPS API origin and a publicly hosted HTTPS privacy policy. No production endpoint or hosted policy URL is available yet, so no production ZIP is emitted by this change.

After the service and policy are approved, from the repository root in PowerShell:

```powershell
$apiOrigin = 'https://YOUR-DEPLOYED-API-HOST'
$privacyUrl = 'https://YOUR-PUBLIC-SITE/privacy'
$output = 'dist/optimus-chrome-extension-production.zip'
python scripts/package_chrome_extension.py `
  --production-api-url $apiOrigin `
  --privacy-policy-url $privacyUrl `
  --output $output
Get-FileHash -Algorithm SHA256 $output
```

Replace both values with real, reviewed URLs; do not package the example placeholders. The builder validates HTTPS syntax, hostname form, ports, and URL components; it rejects reserved placeholder domains but cannot prove domain ownership, reachability, TLS deployment, or that the policy page contains the approved text. Manually open the API URL and confirm the API serves through HTTPS; independently open the public privacy-policy URL and confirm it displays the current approved policy. The command refuses to overwrite an existing output. No secret belongs in the extension because package contents are public.

Before release, extract the ZIP into a new empty directory and load that directory as unpacked. Verify the API origin in `manifest.json`, `background.js`, and `popup.js`; test first-use consent, automatic navigation, manual popup scanning, revocation, a prediction, network timeout, and feedback success/failure. Increment the consent version in both `background.js` and `popup.js` whenever collection scope, destinations, storage, or use changes. The automated test checks ZIP structure and URL syntax but cannot verify that the deployed service or policy URL exists. Chrome loading was not performed in this environment.

Keep the local development build and production build separate. Confirm the extension version has been incremented appropriately for the actual store listing before uploading. Do not publish until `CHROME_WEB_STORE_CHECKLIST.md` is complete.
