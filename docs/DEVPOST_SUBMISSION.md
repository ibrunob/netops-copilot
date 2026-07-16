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
