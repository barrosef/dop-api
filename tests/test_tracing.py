"""The edge's tracing (ADR-0024 §4): the propagator is always on, the
backend is configuration, and a typo is a boot error."""

import pytest
from opentelemetry import trace

from app.platform import tracing
from app.settings import settings


def test_no_backend_still_installs_the_propagator(monkeypatch):
    monkeypatch.setattr(settings, "trace_backend", "")
    tracing.setup()
    from opentelemetry.propagate import get_global_textmap

    carrier = {}
    # A remote traceparent is extracted and re-injected unchanged: the
    # context crosses the process even when nothing is exported.
    remote = {"traceparent": "00-0af7651916cd43dd8448eb211c80319c-b7ad6b7169203331-01"}
    ctx = get_global_textmap().extract(remote)
    get_global_textmap().inject(carrier, context=ctx)
    assert carrier["traceparent"] == remote["traceparent"]


def test_an_unknown_backend_is_refused_at_boot(monkeypatch):
    monkeypatch.setattr(settings, "trace_backend", "datadog")
    with pytest.raises(RuntimeError):
        tracing.setup()


def test_ids_are_empty_outside_a_span():
    assert tracing.ids() == ("", "")


def test_ids_read_the_active_span(monkeypatch):
    monkeypatch.setattr(settings, "trace_backend", "")
    tracing.setup()
    tracer = trace.get_tracer("test")
    with tracer.start_as_current_span("t") as span:
        trace_id, span_id = tracing.ids()
        sc = span.get_span_context()
        # With no provider the span is non-recording and its ids are zero —
        # the function must not pretend otherwise.
        if sc.is_valid:
            assert trace_id == f"{sc.trace_id:032x}"
        else:
            assert trace_id == ""
