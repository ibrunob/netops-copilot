# Worker service boundary

This directory contains the isolated Temporal worker runtime. It connects to the
configured Temporal namespace and task queue, checks the server and completes a
bounded, side-effect-free platform probe workflow/activity before declaring
readiness, emits structured lifecycle logs and optional OTLP spans, and drains via
Temporal's graceful worker shutdown on `SIGTERM`/`SIGINT`.

Apart from the platform probe, the runtime intentionally registers no domain
triage workflow or activity. M2 and M6 must first define persisted jobs,
idempotency keys, retry policy, and side-effect rules.
Future activities must run without device-network egress and keep parsing and
validation deterministic. Do not add a background-task substitute to the API.
