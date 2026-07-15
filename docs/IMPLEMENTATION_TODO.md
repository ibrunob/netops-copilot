# NetOps Copilot — Real Product Implementation TODO

This is the executable implementation sequence. Stages are intentionally gated: do not start an item marked dependent until its acceptance criteria pass. `Parallel` identifies work that can proceed together without creating a false frontend-only application.

## Milestone 0 — Replace the prototype with a real workspace

- [x] Convert the repository to the target monorepo layout: `apps/web`, `services/api`, `services/worker`, `services/connector-agent`, `packages/api-client`, `infra`, `tests`, and `docs/adr`.
- [x] Add a root `Makefile`/task runner with `bootstrap`, `up`, `test`, `lint`, `typecheck`, `migrate`, and `seed` commands. `migrate` and `seed` intentionally fail until their M1 persistence contracts exist.
- [ ] Add `.env.example`, secret handling guidance, pre-commit checks, CODEOWNERS, CI, signed-image/SBOM workflow, and contribution instructions.
- [x] Remove the static prototype after the web application shell is running; no browser-local case data was carried into the product.

**Accept:** a clean clone starts the complete local stack with one command; CI validates every package and no credential is committed.

## Milestone 1 — Platform and security foundation

**Depends on:** Milestone 0.
**Parallel:** API persistence and web shell can proceed after the contracts below exist.

- [ ] Compose profiles: core (web, API, worker, PostgreSQL/pgvector, Redis, MinIO, Temporal, Keycloak), events (Redpanda), and observability (OTel, Grafana stack).
- [ ] Provision PostgreSQL migrations, extensions, backup/restore documentation, and scoped test databases.
- [ ] Create OIDC development realm and API JWT verification; define roles and organization/asset scopes.
- [ ] Enforce PostgreSQL RLS for every organization-owned table; establish request/transaction tenant context.
- [ ] Add structured logs, correlation IDs, OpenTelemetry traces, health/readiness endpoints, Sentry redaction, and baseline rate limits.
- [ ] Define artifact classification, encryption, retention, deletion, access audit, and model-data handling policies.

**Accept:** cross-organization reads fail at database level; a trace follows a signed-in test user to the health endpoint; restore drill works against local backups.

## Milestone 2 — Case spine and API contract

**Depends on:** Milestone 1.

- [ ] Implement `cases`, immutable `case_inputs`, `case_events`, `case_transitions`, `audit_events`, and transactional `outbox_events` migrations.
- [ ] Build the domain state machine with database/API validation, optimistic concurrency, idempotency keys, and actor/correlation capture.
- [ ] Deliver OpenAPI-first case creation, list, detail, timeline, transition, resolution, and feedback endpoints.
- [ ] Add an outbox publisher plus consumer inbox deduplication. Persist then stream timeline events using SSE with last-event recovery.
- [ ] Generate a TypeScript API client and contract tests from OpenAPI.

**Accept:** two concurrent state changes yield a deterministic version conflict; invalid transitions cannot be committed; an outbox publisher crash loses no event; queue/detail/timeline operate with no AI dependency.

## Milestone 3 — Real web application

**Depends on:** Milestone 2.

- [ ] Create Next.js product shell, enterprise OIDC session boundary, RBAC-aware navigation, error boundaries, loading/empty states, and CSS design tokens.
- [ ] Build `/cases` queue with cursor pagination, filters, durable URL state, full-text search, SLA/update fields, and live SSE updates.
- [ ] Build `/cases/[caseId]` workbench with immutable activity timeline, state rail, case evidence placeholders, routing status, and conflict-aware state/resolution actions.
- [ ] Use TanStack Query for server data, generated OpenAPI types, Zod validation at the UI boundary, and a narrow authenticated BFF/SSE proxy where required.
- [ ] Include WCAG 2.2 AA baseline: keyboard flow, semantic controls, focus management, non-color severity, reduced motion, and screen-reader-friendly timeline.

**Accept:** two browsers observe live case changes; a stale operator must refresh/reapply a conflicting transition; keyboard-only users can complete a resolution.

## Milestone 4 — Secure artifact intake

**Depends on:** Milestone 2.
**Parallel:** the web intake work can run alongside the parser once upload contract is stable.

- [ ] Create signed upload intents and multipart alternatives for config/audio; validate size, MIME, hash, and organization scope.
- [ ] Add malware scanning, object encryption, immutable metadata, artifact access audit, and storage lifecycle policy.
- [ ] Implement redaction with test fixtures for pre-shared keys, SNMP communities, passwords, private keys, tokens, and API keys.
- [ ] Build config paste/upload UI with redaction preview and explicit operator confirmation.
- [ ] Build `MediaRecorder` capture and audio-file upload UI; allow transcript correction and never use browser STT as source of truth.

**Accept:** secret-bearing fixture is redacted before an embedding/model request; unauthorized signed URL use fails; raw audio can be expired by retention policy.

## Milestone 5 — Deterministic Cisco IOS parser and IPsec validator

**Depends on:** Milestone 4.

- [ ] Implement tokenizer, block parser, typed Cisco IOS intermediate representation, parse warnings, and exact source-line mapping.
- [ ] Parse IKEv1/IKEv2 proposals, transform sets, IPsec profiles/maps, PFS/DH, authentication, and lifetime inheritance/overrides.
- [ ] Implement IPsec proposal-intersection and Phase 1/2 lifetime validators with `pass/fail/warn/not_applicable/insufficient_context` results.
- [ ] Persist parser/rule versions, config hash, observed/expected values, and evidence spans.
- [ ] Add golden parser IR fixtures, malformed/whitespace/comment ordering tests, rule matrices, and property-based tests.

**Accept:** every IPsec finding is reproducible offline, points to exact artifact lines, has a rule version, and never reports a mismatch solely because peer context is absent.

## Milestone 6 — Durable triage workflow

**Depends on:** Milestones 2, 4, and 5.

- [ ] Add a Temporal workflow and worker with analysis run records, idempotency keys, visible progress, retry policy, and failure state.
- [ ] Implement activities for scan/transcribe/redact/parse/validate/retrieve/analyze/render diff/notify.
- [ ] Publish persisted progress events to SSE and record response timings/errors without raw content.
- [ ] Support case retry, cancellation policy, needs-information requests, and a durable human approval/rejection signal.

**Accept:** a worker restart resumes a case; transient failures retry without duplicate side effects; approved and rejected paths preserve complete history.

## Milestone 7 — AI gateway and evidence-bound diagnosis

**Depends on:** Milestone 6.

- [ ] Build the OpenAI provider adapter with configurable models, timeouts, budgets, retry categorization, `store=False` policy, request metadata, and prompt/model version capture.
- [ ] Implement strict Pydantic/JSON schemas for `CaseClassification`, `Diagnosis`, and `FixPlan`; only expose scoped, read-only context tools.
- [ ] Require diagnosis claims to cite validator evidence or approved memory IDs; reject invalid schemas, unsupported claims, and direct secret changes.
- [ ] Render an edit plan as a server-side unified diff, reparse it, and rerun affected validators before presenting it.
- [ ] Build operational UI panels that visibly separate deterministic findings, historical references, unknowns, and model inference; use an accessible unified diff with Monaco as an optional desktop enhancement.

**Accept:** an IPsec ticket with a configuration mismatch produces an evidence-cited diagnosis and a revalidated diff; a prompt-injection fixture cannot cause an external action or a false case transition.

## Milestone 8 — Persistent memory and closed-loop learning

**Depends on:** Milestones 2, 6, and 7.

- [ ] Define knowledge item/chunk/link models; only ingest sanitized case summaries, config derivatives, approved resolutions, and curated runbooks.
- [ ] Add embeddings, pgvector HNSW, PostgreSQL FTS, scoped hybrid search, ranking fusion, recurrence counting, and memory provenance.
- [ ] Build confirmation/resolution workflows that require a verification note and explicit authorized actor.
- [ ] Embed and link only verified resolution records, then transition to `learned`; surface indexing failure as an operational state.
- [ ] Create a retrieval evaluation corpus with Recall@k/NDCG and permission-boundary tests.

**Accept:** a verified resolution is recalled in a new scoped similar case with its provenance; a cross-organization or unauthorized memory record is never returned.

## Milestone 9 — Routing and integrations

**Depends on:** Milestones 2, 6, and 8.

- [ ] Implement deterministic routing rules based on category, urgency, asset, environment, and ownership.
- [ ] Add Slack and Teams adapters with signed/webhook safeguards, idempotency, retry, delivery receipts, and visible failure status.
- [ ] Add ticketing and inventory/CMDB adapters behind the same consumer contract.
- [ ] Deliver an outbound-mTLS, read-only private-network connector using a least-privilege identity and Secret Manager/Vault. Device writes stay unavailable.

**Accept:** notification retries do not duplicate messages; operator UI shows delivery status; connector cannot execute a device change.

## Milestone 10 — BGP, GRE, and production readiness

**Depends on:** Milestones 5–9.

- [ ] Add BGP config facts first (remote AS, duplicate/malformed neighbors, address-family activation, update-source, route-map attachment) and require RIB/policy intent for topology claims.
- [ ] Add GRE source/destination, source interface resolution, MTU/MSS overhead, and peer keepalive checks; mark missing context instead of guessing.
- [ ] Add formal SLOs, dashboards, alerts, incident runbooks, budget controls, retention deletion verification, and quarterly restore drills.
- [ ] Execute load testing, DAST/SAST/dependency/container scans, tenant isolation tests, model/retrieval evaluation gates, accessibility tests, and disaster recovery exercise.
- [ ] Deploy separate development/staging/production infrastructure using Terraform; start with ECS Fargate/managed services and make Kubernetes a measured future decision.

**Accept:** staging supports the agreed workload and latency SLOs, restore and rollout rollback are documented and tested, and a model/prompt regression blocks promotion.

## Agent work packages

Use these as independent, reviewable implementation tasks. No agent may bypass a listed dependency.

| Package | Scope | Dependencies | Definition of done |
| --- | --- | --- | --- |
| `platform-foundation` | Compose, CI, config, OTel, Keycloak dev realm | M0 | `make up` yields trace-visible healthy services |
| `tenant-case-spine` | Migrations, RLS, case state machine, audit/outbox, OpenAPI | M1 | Contract + adversarial RLS tests pass |
| `web-case-workbench` | Authenticated queue/detail/SSE/transition UX | M2 | Live updates and version conflict UX pass E2E |
| `artifact-security` | Uploads, scanner, redaction, retention | M2 | No raw secret reaches AI boundary |
| `cisco-ipsec` | Parser IR + IPsec rules/fixtures | M4 | Offline deterministic fixture suite passes |
| `triage-workflow` | Temporal runs, progress, retry, approval signal | M2+M4+M5 | Replay/restart and duplicate prevention pass |
| `ai-evidence-gateway` | Provider adapter, schemas, diagnosis/fix revalidation | M6 | Evidence citation and injection tests pass |
| `memory-learning` | Chunking, embeddings, hybrid retrieval, learning | M2+M6+M7 | Scoped recall evaluation passes |
| `routing-connectors` | Routing policy, notifications, collector agent | M2+M6+M8 | Idempotent delivery and no-write boundary pass |
| `hardening-vendor-rules` | BGP/GRE, SLOs, IaC, production gates | M5–M9 | Staging readiness scorecard approved |
