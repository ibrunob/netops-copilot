# Worker service boundary

This directory is reserved for the isolated Temporal worker and transactional-outbox
publisher described in the production architecture. No worker process exists yet.

Future activities must consume persisted jobs, run without device-network egress,
and keep parsing/validation deterministic. Do not add a background-task substitute
to the API service.
