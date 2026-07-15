"""Optional Sentry bootstrap with defensive event redaction."""

from __future__ import annotations

import logging
from typing import Any, cast

from netops_api.core.config import Settings
from netops_api.core.logging import redact_for_observability

logger = logging.getLogger(__name__)


def configure_sentry(settings: Settings) -> None:
    """Enable Sentry only when a DSN and its locked package are both present."""
    if settings.sentry_dsn is None:
        return
    try:
        import sentry_sdk
    except ImportError:
        logger.warning("sentry_dependency_unavailable", extra={"sentry_dsn_configured": True})
        return

    sentry_sdk.init(
        dsn=settings.sentry_dsn.get_secret_value(),
        environment=settings.environment.value,
        release=settings.api_version,
        traces_sample_rate=settings.sentry_traces_sample_rate,
        send_default_pii=False,
        before_send=cast(Any, _before_send),
    )
    logger.info("sentry_configured", extra={"sentry_dsn_configured": True})


def _before_send(event: dict[str, Any], hint: dict[str, Any]) -> dict[str, Any] | None:
    """Strip request payloads and recursively scrub Sentry event metadata."""
    del hint  # Hook signature is defined by Sentry; no hint data is safe to retain.
    sanitized = redact_for_observability(event)
    if not isinstance(sanitized, dict):
        return None
    # Logger messages, breadcrumbs, and exception values can contain rejected
    # configuration even when their field names do not reveal that fact. Preserve
    # only the exception type, which is sufficient for error grouping.
    sanitized.pop("logentry", None)
    sanitized.pop("breadcrumbs", None)
    sanitized.pop("threads", None)
    exception = sanitized.get("exception")
    if isinstance(exception, dict):
        values = exception.get("values")
        if isinstance(values, list):
            exception["values"] = [
                {"type": item.get("type", "Exception")} for item in values if isinstance(item, dict)
            ]
    request = sanitized.get("request")
    if isinstance(request, dict):
        for key in ("data", "headers", "cookies", "query_string", "env"):
            request.pop(key, None)
    return sanitized
