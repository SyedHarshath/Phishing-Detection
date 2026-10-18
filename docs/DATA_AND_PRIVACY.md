# Data and privacy

The prediction database currently stores full submitted URLs. URL paths and query parameters can contain credentials, session tokens, personal data, or private identifiers. The current API returns URLs through `/predictions` and has no authentication or retention/deletion policy. Restrict filesystem/API access and avoid production exposure until those controls are addressed. Do not send collected URLs to external services.

Feedback is an unverified user assertion. It must not be treated as a verified label or used for training. A future approved lifecycle needs reviewer identity/provenance, immutable audit events, verification/dispute status, and explicit eligibility approval. The current project does not implement that workflow or immutable training batch export. Dataset source files are untrusted inputs; the strict validator never visits URLs and reports content fingerprints without rewriting the source data.
