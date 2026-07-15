# Security policy

## Reporting a vulnerability

Do not report vulnerabilities, exposed credentials, customer data, or network
configuration in a public issue, pull request, discussion, or chat transcript.
Use the repository host's private security-advisory channel. If that channel is
not enabled, contact the repository owner privately and include only the minimum
redacted reproduction needed to assess impact.

Include the affected revision, component, impact, reproduction steps, and any
known mitigations. Do not attach secrets, production configuration, private keys,
access tokens, packet captures, or customer artifacts.

## Scope

This policy covers the production application, API, workers, connector agent,
infrastructure, CI, and dependency supply chain. The root static prototype
(`index.html`, `app.js`, and `styles.css`) is historical material, excluded from
the deployment workflow, and must not be used as a production persistence path.

## Security expectations

- Treat organization identity, authorization decisions, case history, evidence,
  and artifact metadata as sensitive.
- Keep raw configuration and audio encrypted in approved storage. Only a
  policy-governed redacted derivative may be embedded or sent to an AI provider.
- Never add device-write capabilities to the connector without a separately
  approved policy and threat model.
- Use short-lived OIDC credentials and managed secrets. `.env` files and private
  key formats are ignored by default and must never be committed.
- Validate signed uploads, webhook signatures, parser input limits, and all API
  authorization server-side. UI state never proves access.
- Rotate an accidentally exposed secret immediately, then report it privately;
  removing it from a later commit is not sufficient remediation.

## Supported versions

Until a release process exists, the default branch and its deployed artifacts are
the only supported line. Security fixes should include tests and an operational
rollback note before release.
