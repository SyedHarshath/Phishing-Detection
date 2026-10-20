# Chrome Web Store readiness checklist

## Product and behavior

- [ ] State the single purpose: classify a webpage URL for phishing indicators.
- [ ] Explain that the classifier is an estimate and does not guarantee a page is safe.
- [ ] Describe that navigation scanning covers HTTP/HTTPS pages after affirmative consent and identify the popup/manual scan and page feedback UI.
- [ ] Disclose full URL transmission, local/service-side storage, feedback use, reviewer access, and retention limitations before installation and in-product.
- [ ] Test scanning disabled, accepted, and revoked; confirm no `/predict` or `/feedback` call occurs before consent or after revocation.
- [ ] Test unsupported browser pages, unavailable service, request timeout, malformed responses, and feedback failures.

## Permissions and package

- [ ] Keep permissions limited to `tabs` and `storage`; document why active tab/navigation reading and consent storage are needed.
- [ ] Keep the API host permission restricted to the actual HTTPS API origin in the production ZIP.
- [ ] Review broad HTTP/HTTPS content-script matches against the actual automatic scanning feature and provide a clear explanation to users.
- [ ] Confirm Manifest V3, local executable code only, no remote code, no secrets, and an allowlisted package.
- [ ] Extract and load the exact production ZIP in Chrome; test the built origin and privacy-policy link.
- [ ] Record the ZIP SHA-256, contents, extension version, source revision, and reviewer sign-off.

## Privacy and security

- [ ] Complete and host `PRIVACY_POLICY_DRAFT.md` at a public HTTPS URL and configure that URL into the production package.
- [ ] Confirm identity/contact details, hosting provider and subprocessors, storage encryption, access controls, retention and deletion, and incident contact.
- [ ] Obtain legal/privacy review for the jurisdictions where the extension will be distributed.
- [ ] Ensure the extension's product listing and in-product disclosure accurately match collection and use.
- [ ] Verify HTTPS end to end and configure a hosting-layer rate limit and abuse monitoring for public `/predict` and `/feedback`.
- [ ] Keep administrative prediction history disabled or protect it with a secret stored only in the host's secret manager.
- [ ] Confirm database backups, restore drills, access restriction, and deletion procedures.

## Listing and manual release

- [ ] Complete the store listing, screenshots, icon, support contact, category, and data-practice declarations.
- [ ] Confirm the Developer Dashboard's current policy and permission requirements immediately before submission.
- [ ] Confirm the version number is new for the actual listing.
- [ ] Submit only after the API, policy URL, consent experience, security review, and tests are complete.

The Chrome Web Store policies treat browsing URLs as user data, require prominent disclosure and use limited to the disclosed purpose, and call for the narrowest necessary permissions. Policy changed in 2026; review the [current Program Policies](https://developer.chrome.com/docs/webstore/program-policies/policies) and [2026 update](https://developer.chrome.com/blog/cws-policy-updates-2026?hl=en) before release. This checklist does not certify compliance or guarantee store approval.
