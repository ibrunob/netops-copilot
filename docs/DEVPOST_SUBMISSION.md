# OpenAI Build Week submission pack

This pack is written for the **Developer Tools** track. It reflects the
implemented demo as of July 2026; do not claim an AI diagnosis, parser result,
or device action that is not shown in the video.

## Submission fields

| Devpost field | Ready-to-paste value |
| --- | --- |
| Project name | **NetOps Copilot** |
| Tagline | **Evidence-first network incident triage with human-controlled remediation.** |
| Category | **Developer Tools** |
| Repository | `https://github.com/ibrunob/netops-copilot` |
| Demo video | **Replace with the public YouTube URL before submitting.** |
| Codex `/feedback` session ID | **Replace with the session ID that built the majority of the core functionality.** |

## Description

Network incidents become difficult to resolve when tickets, logs, configuration
snippets, approvals, and customer follow-ups live in separate tools. NetOps
Copilot is an evidence-first operations workspace that turns each incident into
a durable case: who changed its state, what evidence was attached, what the
customer still needs to answer, and which human approved the next step are all
kept together.

The demo is intentionally more than a dashboard. It uses a signed OIDC session,
tenant-scoped PostgreSQL rows with row-level security, immutable case events,
idempotency keys, optimistic concurrency, and server-sent events. Configuration
evidence is redacted before preview, while audio/config uploads use short-lived
object-store capabilities, malware scanning, and persisted processing status.

The product never writes to a network device. NetOps Copilot makes a future AI
assistant accountable to evidence and human approval rather than giving it
unbounded operational access.

## About the project

### Inspiration

Network incidents rarely fail because there is no alert. They fail because the
context is fragmented: a support ticket is in one system, configuration output
is pasted into chat, the customer has not supplied a key detail, and the actual
approval trail is difficult to reconstruct later. In network operations, a
plausible answer is not enough—operators need to know what evidence supports a
decision, who made it, and whether a change was actually authorized.

NetOps Copilot was inspired by that gap. We wanted to make the case record—not
the chat window—the center of an AI-assisted operations workflow. The result is
an evidence-first workspace where customer follow-up, artifacts, state changes,
and human approvals belong to the same durable incident.

### What it does

NetOps Copilot gives operations teams a secure case queue and a case workbench
for network incidents and customer requests. Operators can create cases, track
them through an explicit lifecycle, ask for customer information, attach
configuration or audio evidence, and keep an immutable activity history.

The demo is intentionally conservative: it does not push any configuration to
a router, firewall, or VPN device. Instead, it proves the product foundation a
future assistant must respect: verified identity, tenant isolation, evidence
redaction, versioned state transitions, and human-controlled remediation.

### How we built it

We built a monorepo around a Next.js 16 web application and a FastAPI domain
API. The web tier uses a narrow server-side BFF, authorization-code PKCE, and
encrypted HttpOnly session cookies so API bearer tokens are not exposed to the
browser. The API persists cases, inputs, transitions, audit events, and outbox
events in PostgreSQL with tenant row-level security and uses optimistic
concurrency plus idempotency keys to make concurrent operator work safe.

For evidence intake, configuration previews are redacted before display and
uploads use short-lived MinIO capabilities, persisted metadata, and ClamAV
scanning. Redis, Temporal, Keycloak, and Docker Compose complete the local
platform; a generated TypeScript client keeps the web/API contract typed.

Codex accelerated the implementation by helping decompose the architecture into
small reviewable workstreams, then iterating on migrations, typed contracts,
security tests, UI flows, and the reproducible local demo. Use the GPT-5.6
wording in this submission only if it accurately matches the Codex session ID
you provide.

### Challenges we ran into

The hard part was not rendering a convincing dashboard. It was keeping the
demo's product experience honest while preserving real operational safeguards.

- **Authentication across a local multi-service stack.** Browser-visible OIDC
  URLs and Docker-internal service URLs are different address spaces. We solved
  this with a server-side PKCE exchange, a verified issuer, an encrypted cookie,
  and an explicit local-browser compatibility path.
- **Preventing a duplicate case during an unreliable browser submission.** We
  use idempotency keys and added a normal HTML form fallback behind the same
  authenticated endpoint, so failure of client-side transport cannot silently
  create duplicate incidents.
- **Making sample data believable without using mock state.** The five demo
  cases are inserted idempotently into the real local PostgreSQL tables and
  include immutable events and transitions. Queue filters, workbench history,
  and state changes still use the live API.
- **Handling sensitive evidence responsibly.** We chose redaction-before-
  preview/model boundaries and retained a human approval boundary rather than
  representing an autonomous device-changing capability that the product does
  not yet support.

### What we learned

We learned that operational AI needs a strong product boundary before it needs
more autonomy. The most valuable capabilities are often the unglamorous ones:
reproducible state, scoped identity, visible unknowns, stable contracts, and a
clear record of human judgment. Building the demo also reinforced that a
multi-service project is much easier to evaluate when it has one command to
start, deterministic sample data, and a short walkthrough focused on a real
operator decision.

## How it works

The Next.js workspace is a narrow, authenticated BFF over a FastAPI domain API.
Keycloak provides local OIDC identity; PostgreSQL and pgvector provide durable
tenant-scoped persistence; MinIO stores artifacts; Temporal hosts the worker
boundary; Redis supports platform services; and Docker Compose makes the full
demo reproducible. The app includes a live case queue, case workbench, state
transitions, customer-answer handling, redacted configuration intake, and audio
evidence intake.

## How Codex and GPT-5.6 were used

Use this section only after confirming the submission's required session ID is
available. The project was built iteratively with Codex: the implementation was
broken into independently reviewable backend, workflow, security, and web
workstreams; Codex helped turn the architecture into migrations, typed API
contracts, tests, UI flows, and a reproducible local stack. GPT-5.6 was used
through Codex to accelerate implementation and review, while the application
itself deliberately does not yet make unattended model decisions or device
changes.

## Video script (about 2 min 35 sec)

**0:00–0:18 — Problem.** “A network incident is rarely one alert. It is a ticket,
configuration evidence, a customer follow-up, and an approval trail spread
across tools. NetOps Copilot brings those pieces into one accountable case.”

**0:18–0:42 — Queue.** Sign in and show the queue. “This is live local data,
not browser mock data. The states show new work, active investigation, customer
follow-up, approval, and resolution.”

**0:42–1:08 — Customer request.** Open the firewall inbound NAT case. “A support
request can be explicitly pending customer answer. The timeline preserves the
question and the operator’s state change.”

**1:08–1:34 — Evidence.** Open the OSPF case and show configuration preview or
audio intake. “Evidence enters through short-lived upload capabilities, is
scanned, and configuration is redacted before it is previewed.”

**1:34–2:03 — Workflow.** Begin investigation or create a case. “State changes
are authenticated, idempotent, optimistic-concurrency checked, and written as
immutable events. Other operators receive live updates.”

**2:03–2:25 — Safety.** “The workflow can reach human-approved and resolved
states, but it never pushes configuration to infrastructure. That boundary is
intentional.”

**2:25–2:35 — Codex.** Say this only if it matches the session you submit:
“We used Codex with GPT-5.6 to build and review this multi-service product—from
typed API contracts and security tests to the operational UI and reproducible
demo stack.”

## Final checklist

- [ ] Repository is public with this README and MIT license, or shared with
  `testing@devpost.com` and `build-week-event@openai.com`.
- [ ] Replace the video and `/feedback` placeholders above.
- [ ] Upload an unlisted/public YouTube video shorter than three minutes.
- [ ] Run `make demo` from a clean-ish local environment and record only the
  supported flows in `docs/DEMO_RUNBOOK.md`.
- [ ] Verify the Devpost account eligibility and submit by **July 21, 2026,
  5:00 PM PDT**.
