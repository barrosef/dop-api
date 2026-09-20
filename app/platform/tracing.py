"""OpenTelemetry for the edge (ADR-0024 §4, phase 1).

The same shape as the core's: the exporter is configuration (none, OTLP over
gRPC, Cloud Trace), the propagator is always installed so the W3C trace
context crosses the process even when nothing is exported. FastAPI, the gRPC
client to the core and the edge's own gRPC server are instrumented; every log
line written under a span carries trace_id and span_id.
"""

from __future__ import annotations

import logging

from opentelemetry import trace
from opentelemetry.propagate import set_global_textmap
from opentelemetry.propagators.composite import CompositePropagator
from opentelemetry.sdk.resources import SERVICE_NAME, SERVICE_VERSION, Resource
from opentelemetry.sdk.trace import TracerProvider
from opentelemetry.sdk.trace.export import BatchSpanProcessor
from opentelemetry.sdk.trace.sampling import ParentBased, TraceIdRatioBased
from opentelemetry.trace.propagation.tracecontext import TraceContextTextMapPropagator

from app.settings import settings

log = logging.getLogger(__name__)

_provider: TracerProvider | None = None


def setup() -> None:
    """Installs the propagator and, when a backend is configured, the provider.

    An unknown backend is a boot error: a typo must not silently export nothing.
    """
    global _provider
    set_global_textmap(CompositePropagator([TraceContextTextMapPropagator()]))

    backend = settings.trace_backend
    if backend in ("", "none"):
        return
    if backend == "otlp":
        from opentelemetry.exporter.otlp.proto.grpc.trace_exporter import OTLPSpanExporter

        if not settings.otel_exporter_otlp_endpoint:
            raise RuntimeError("TRACE_BACKEND=otlp needs OTEL_EXPORTER_OTLP_ENDPOINT")
        exporter = OTLPSpanExporter(endpoint=settings.otel_exporter_otlp_endpoint, insecure=True)
    elif backend == "gcp":
        from opentelemetry.exporter.cloud_trace import CloudTraceSpanExporter

        exporter = CloudTraceSpanExporter(project_id=settings.trace_gcp_project or None)
    else:
        raise RuntimeError(f"unknown TRACE_BACKEND {backend!r} (use none, otlp or gcp)")

    ratio = settings.trace_sample_ratio
    if ratio <= 0 or ratio > 1:
        ratio = 1.0
    _provider = TracerProvider(
        resource=Resource.create(
            {SERVICE_NAME: settings.app_name, SERVICE_VERSION: settings.version}
        ),
        sampler=ParentBased(TraceIdRatioBased(ratio)),
    )
    _provider.add_span_processor(BatchSpanProcessor(exporter))
    trace.set_tracer_provider(_provider)
    log.info("tracing on", extra={"trace_backend": backend})


def shutdown() -> None:
    if _provider is not None:
        _provider.shutdown()


def instrument_app(app) -> None:
    """FastAPI: one server span per request, the trace continued from the
    caller's traceparent when the cockpit or the CLI sends one."""
    from opentelemetry.instrumentation.fastapi import FastAPIInstrumentor

    FastAPIInstrumentor.instrument_app(app, excluded_urls="healthz,openapi.json")


def instrument_grpc() -> None:
    """The gRPC client to the core and the edge's own server. Both are
    process-wide instrumentations, installed once."""
    from opentelemetry.instrumentation.grpc import (
        GrpcAioInstrumentorClient,
        GrpcAioInstrumentorServer,
    )

    GrpcAioInstrumentorClient().instrument()
    GrpcAioInstrumentorServer().instrument()


def ids() -> tuple[str, str]:
    """The active span's ids for a log line, or empty strings."""
    ctx = trace.get_current_span().get_span_context()
    if not ctx.is_valid:
        return "", ""
    return f"{ctx.trace_id:032x}", f"{ctx.span_id:016x}"
