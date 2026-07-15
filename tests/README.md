# Cross-service test boundary

This directory is reserved for repository-level integration, contract, workflow,
security, and evaluation suites. Package-local unit tests remain next to their
respective packages until a cross-service boundary is exercised.

There are no integration fixtures here yet because the tenant-safe persistence and
workflow contracts they require have not been implemented.
