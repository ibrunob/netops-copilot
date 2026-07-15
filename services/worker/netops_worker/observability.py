"""OpenTelemetry lifecycle spans for the worker runtime, without workflow payload data."""

from __future__ import annotations

from contextlib import AbstractContextManager, nullcontext
from typing import Any

from netops_worker.config import WorkerSettings


def configure_opentelemetry(settings: WorkerSettings) -> None:
    """Configure an OTLP exporter only when an approved endpoint is provided."""
    if not settings.otel_traces_enabled or not settings.otel_exporter_otlp_endpoint:
        return
    from opentelemetry import trace
    from opentelemetry.exporter.otlp.proto.http.trace_exporter import OTLPSpanExporter
    from opentelemetry.sdk.resources import Resource
    from opentelemetry.sdk.trace import TracerProvider
    from opentelemetry.sdk.trace.export import BatchSpanProcessor

    provider = TracerProvider(resource=Resource.create({"service.name": settings.service_name}))
    provider.add_span_processor(
        BatchSpanProcessor(OTLPSpanExporter(endpoint=settings.otel_exporter_otlp_endpoint))
    )
    trace.set_tracer_provider(provider)


def lifecycle_span(name: str) -> AbstractContextManager[Any]:
    """Create a coarse lifecycle span after optional OTel initialization."""
    try:
        from opentelemetry import trace
    except ImportError:
        return nullcontext()
    return trace.get_tracer("netops_worker.runtime").start_as_current_span(name)
