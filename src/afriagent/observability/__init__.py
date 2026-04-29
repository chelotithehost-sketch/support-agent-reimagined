"""OpenTelemetry + Prometheus instrumentation."""

from __future__ import annotations

from functools import wraps
from typing import Any, Callable, TypeVar

from prometheus_client import Counter, Histogram, Gauge, Info, start_http_server
from opentelemetry import trace
from opentelemetry.sdk.trace import TracerProvider
from opentelemetry.sdk.trace.export import BatchSpanProcessor
from opentelemetry.exporter.otlp.proto.grpc.trace_exporter import OTLPSpanExporter
from opentelemetry.sdk.resources import Resource

from afriagent.config import settings
from afriagent.config.logging import get_logger

log = get_logger(__name__)

# ── Prometheus Metrics ────────────────────────────────────────────

METRICS_PREFIX = "afriagent"

# Request metrics
REQUEST_COUNT = Counter(
    f"{METRICS_PREFIX}_requests_total",
    "Total inbound messages",
    ["channel", "intent"],
)
REQUEST_LATENCY = Histogram(
    f"{METRICS_PREFIX}_request_duration_seconds",
    "Request processing latency",
    ["channel"],
    buckets=[0.1, 0.25, 0.5, 1.0, 2.5, 5.0, 10.0],
)

# LLM metrics
LLM_CALLS = Counter(
    f"{METRICS_PREFIX}_llm_calls_total",
    "Total LLM API calls",
    ["provider", "model", "status"],
)
LLM_LATENCY = Histogram(
    f"{METRICS_PREFIX}_llm_latency_seconds",
    "LLM response latency",
    ["provider"],
    buckets=[0.5, 1.0, 2.0, 5.0, 10.0, 30.0],
)
LLM_TOKENS = Counter(
    f"{METRICS_PREFIX}_llm_tokens_total",
    "Total tokens consumed",
    ["provider", "direction"],  # direction: input/output
)

# Validation metrics
VALIDATION_PASS = Counter(
    f"{METRICS_PREFIX}_validation_passed_total",
    "Responses that passed validation",
    ["layer"],
)
VALIDATION_FAIL = Counter(
    f"{METRICS_PREFIX}_validation_failed_total",
    "Responses that failed validation",
    ["layer"],
)

# Memory metrics
MEMORY_OP = Counter(
    f"{METRICS_PREFIX}_memory_operations_total",
    "Memory layer operations",
    ["tier", "operation"],  # tier: redis/postgres/qdrant
)
MEMORY_LATENCY = Histogram(
    f"{METRICS_PREFIX}_memory_latency_seconds",
    "Memory operation latency",
    ["tier"],
)

# Conversation metrics
ACTIVE_CONVERSATIONS = Gauge(
    f"{METRICS_PREFIX}_active_conversations",
    "Currently active conversations",
    ["channel"],
)
ESCALATIONS = Counter(
    f"{METRICS_PREFIX}_escalations_total",
    "Total escalations to human agents",
    ["channel", "reason"],
)
SATISFACTION = Histogram(
    f"{METRICS_PREFIX}_satisfaction_score",
    "Customer satisfaction scores",
    buckets=[1, 2, 3, 4, 5],
)

# Circuit breaker
CIRCUIT_STATE = Gauge(
    f"{METRICS_PREFIX}_circuit_breaker_state",
    "Circuit breaker state (0=closed, 1=open, 2=half-open)",
    ["provider"],
)

# Business info
BUSINESS_INFO = Info(
    f"{METRICS_PREFIX}_build",
    "Build information",
)


F = TypeVar("F", bound=Callable[..., Any])


def track_latency(histogram: Histogram, labels: dict[str, str] | None = None) -> Callable[[F], F]:
    """Decorator to track function latency in a histogram."""

    def decorator(func: F) -> F:
        @wraps(func)
        async def wrapper(*args: Any, **kwargs: Any) -> Any:
            with histogram.labels(**(labels or {})).time():
                return await func(*args, **kwargs)

        return wrapper  # type: ignore[return-value]

    return decorator


def setup_telemetry() -> None:
    """Initialize OpenTelemetry tracing and start Prometheus metrics server."""
    # OpenTelemetry
    resource = Resource.create({"service.name": "afriagent"})
    provider = TracerProvider(resource=resource)

    try:
        exporter = OTLPSpanExporter(endpoint=settings.otel_exporter_endpoint, insecure=True)
        provider.add_span_processor(BatchSpanProcessor(exporter))
    except Exception:
        log.warning("OTLP exporter unavailable, tracing disabled")

    trace.set_tracer_provider(provider)

    # Prometheus
    try:
        start_http_server(settings.metrics_port)
        log.info("Prometheus metrics server started", port=settings.metrics_port)
    except OSError:
        log.warning("Metrics port already in use", port=settings.metrics_port)

    BUSINESS_INFO.info({"version": "0.1.0", "env": settings.env.value})
    log.info("Telemetry initialized")


def get_tracer(name: str) -> trace.Tracer:
    """Get a named tracer."""
    return trace.get_tracer(name)
