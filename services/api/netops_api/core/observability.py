"""Optional OpenTelemetry bootstrap with a safe no-dependency fallback."""

from __future__ import annotations

import logging
from typing import Any

from fastapi import FastAPI

from netops_api.core.config import Settings

logger = logging.getLogger(__name__)


def configure_opentelemetry(app: FastAPI, settings: Settings) -> None:
    """Instrument FastAPI when the locked OpenTelemetry distribution is available.

    The API retains W3C ``traceparent`` correlation without these packages. A missing
    optional distribution therefore cannot make the service unavailable, but an OTLP
    endpoint is never silently claimed to be exporting traces.
    """
    if not settings.otel_traces_enabled or not settings.otel_exporter_otlp_endpoint:
        return
    if getattr(app.state, "opentelemetry_configured", False):
        return
    try:
        from opentelemetry.exporter.otlp.proto.http.trace_exporter import OTLPSpanExporter
        from opentelemetry.instrumentation.fastapi import FastAPIInstrumentor
        from opentelemetry.sdk.resources import Resource
        from opentelemetry.sdk.trace import TracerProvider
        from opentelemetry.sdk.trace.export import BatchSpanProcessor
    except ImportError:
        logger.warning(
            "opentelemetry_dependencies_unavailable",
            extra={"otlp_endpoint_configured": True},
        )
        return

    resource = Resource.create({"service.name": settings.service_name})
    provider = TracerProvider(resource=resource)
    provider.add_span_processor(
        BatchSpanProcessor(OTLPSpanExporter(endpoint=settings.otel_exporter_otlp_endpoint))
    )
    _set_global_tracer_provider(provider)
    FastAPIInstrumentor.instrument_app(app, tracer_provider=provider)
    app.state.opentelemetry_configured = True
    logger.info("opentelemetry_configured", extra={"otlp_endpoint_configured": True})


def _set_global_tracer_provider(provider: Any) -> None:
    """Install the provider without importing optional OTel symbols at module load."""
    from opentelemetry import trace

    trace.set_tracer_provider(provider)
