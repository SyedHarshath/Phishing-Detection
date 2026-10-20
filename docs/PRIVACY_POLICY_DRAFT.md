# OPTIMUS privacy policy draft — not ready to publish

**Status:** Draft. The operator must fill in the named party, contact address, hosting provider, retention period, and jurisdictional/legal review, then host this policy at a public HTTPS URL. This draft is not that hosted policy.

## Information collected

After a user affirmatively enables scanning, the extension sends the complete current page URL to the configured OPTIMUS API for prediction. The URL may include its scheme, host, path, query parameters, and fragment. A URL can contain account identifiers, session values, private search terms, or other sensitive information; do not scan private pages or URLs containing secrets.

For each successful prediction, the API stores the submitted URL, prediction timestamp, predicted class, confidence score, and model version. If the user submits feedback, the API also stores the asserted label and feedback timestamp. An authorized reviewer may access a URL associated with submitted feedback to verify its label and record reviewer/provenance audit information. Feedback is not treated as verified merely because the user submitted it.

## Purpose and use

The URL is used to provide phishing-URL classification. Prediction and feedback records may be reviewed to verify labels and, only after the project's separate provenance and eligibility safeguards pass, may contribute to a future training batch. The current extension sends data only to the API origin embedded in the installed build. The project source contains no advertising or analytics integration; hosting-provider and network logging practices must still be confirmed for the deployed service.

## Storage, access, and retention

The backend stores data in SQLite. The current implementation does not encrypt the database itself and does not implement automatic retention or a user-facing deletion function. Stored data remains until an authorized operator deletes it through an approved operational process. Operators with filesystem/database access and authorized feedback reviewers may access stored information. `/predictions` requires a configured shared token and redacts URLs by default, but that endpoint is not a user-level access-control system.

Whether the selected host encrypts persistent storage at rest, what infrastructure logs are retained, and which provider personnel may access data must be confirmed and described before publication. Do not claim encryption at rest based solely on TLS or a cloud provider's generic service description.

## Transmission and sharing

Production extension packages require an HTTPS API URL. The API operator and the selected hosting provider necessarily process the request to deliver the service. No other sharing destination is established by this repository. If the deployment adds logging, support, analytics, or another data processor, this policy and the store disclosures must be updated before use.

## User choice

The extension displays a notice and requires explicit acceptance before automatic or manual scanning. Disabling scanning in the popup stops future requests from that browser profile but does not delete records already stored on the server. Users can avoid collection by not enabling the extension or by disabling it. A published service must provide a verified contact channel for access/deletion requests and state which requests can be honored.

## Operator details to complete before hosting

- Responsible organization/person: **[REQUIRED]**
- Contact email and privacy request process: **[REQUIRED]**
- Production API host and hosting provider: **[REQUIRED]**
- Storage encryption and access-control details: **[REQUIRED]**
- Retention duration and deletion procedure: **[REQUIRED]**
- Applicable jurisdiction and legal review: **[REQUIRED]**
- Publicly hosted HTTPS URL: **[REQUIRED]**

