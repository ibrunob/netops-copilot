# Data governance and telemetry policy

## Scope and classification

All production data is classified before collection. Public product documentation
may be stored in source control. Internal operational metadata (case identifiers,
timestamps, asset aliases, and run status) is confidential. Network
configurations, audio, tickets, IP addressing, raw artifact hashes, identities,
and authentication material are restricted. Secrets (private keys, pre-shared
keys, passwords, SNMP communities, tokens, and credentials) are never acceptable
telemetry and must be redacted at ingress.

## Artifact controls

Raw artifacts are encrypted in the object store with environment-specific KMS
keys, immutable content hashes, malware-scan status, organization ownership, and
an access policy. Downloads use short-lived, single-purpose signed URLs and every
authorization decision is audited. Raw artifact bytes, object keys, request
bodies, and secret-bearing parser output must not enter logs, metrics, traces,
Sentry, analytics, or model prompts. A separate redacted derivative is the only
artifact form eligible for deterministic parsing, retrieval, or AI use.

## Retention and deletion

The data owner sets retention by artifact class, organization contract, and legal
hold. The initial product targets are: raw audio 30 days; raw configuration and
uploads 90 days; redacted derivatives and case evidence 365 days after resolution;
immutable audit records 7 years; operational logs/traces 30 days; and aggregated,
non-identifying service metrics 13 months. Values are maximum defaults, not a
promise to retain data. A deletion request removes object versions and derivatives,
revokes signed URLs, tombstones searchable/indexed copies, and emits an audit
event. Legal hold overrides scheduled deletion and is itself audited.

## Access audit requirements

Every artifact read/write/delete, signed-URL issuance, case access, role change,
model submission, export, and retention override writes an append-only audit
event. Audit events include UTC time, actor/workload identity, organization,
action, object type and ID, authorization decision, correlation ID, and result;
they never contain raw evidence or secrets. Audit records are organization-scoped,
access-controlled, monitored for failed access patterns, and retained separately
from mutable case projections.

## Model-data handling

Only redacted, tenant-authorized, minimum-necessary derivatives may be supplied to
an AI provider. The gateway sets provider retention controls (including
`store=False` where supported), uses no model training opt-in, records provider,
model, prompt/template version, redaction version, evidence IDs, token/cost
metadata, and correlation ID, but does not persist raw prompts or responses in
telemetry. The model has no credentials, device-write capability, cross-tenant
retrieval, or autonomous external-action tool. Operators can require a no-model
path; deterministic validators remain the source of truth.

## Observability data handling

API logs are JSON events containing bounded metadata only: service, severity,
route template, response status, duration, correlation ID, and trace ID.
Incoming `traceparent` values are validated; query strings, headers, bodies, and
client-supplied correlation values other than valid UUIDs are not logged. Sentry
is opt-in, disables default PII collection, strips request payload/header data,
and applies the same redaction routine. Production alert payloads link to an audit
or case ID instead of including evidence.

## Operational review

Security reviews the classification and retention matrix at least quarterly and
before enabling a new connector, artifact class, provider, or telemetry sink.
Engineering must test redaction and tenant boundaries for each new ingestion route.
Any telemetry or provider exposure is handled as a security incident: stop the
sink where possible, rotate affected credentials, assess scope from audit records,
and notify the responsible data owner under the incident process.
