"""Analysis of OpenTelemetry-like JSON span exports (OTLP/JSON shape, subset).

Classifies spans as model / tool / retrieval / other using ``gen_ai.*`` and
common attribute conventions or span-name prefixes, then summarises latency
and errors per kind. Only span metadata is read; attribute values that look
like prompt/response content are ignored. This is an analysis helper, not an
observability backend.
"""

from __future__ import annotations

import json
from collections import defaultdict
from pathlib import Path

from ai_reliability.domain.base import DomainModel
from ai_reliability.errors import ConfigurationError
from ai_reliability.stats import percentile

_CONTENT_KEYS = ("prompt", "completion", "content", "input.value", "output.value", "message")


class Span(DomainModel):
    trace_id: str
    span_id: str
    name: str
    kind: str
    duration_ms: float
    error: bool
    attributes: dict[str, str]


class SpanKindSummary(DomainModel):
    kind: str
    count: int
    errors: int
    error_rate: float
    p50_ms: float | None
    p95_ms: float | None


class TraceAnalysis(DomainModel):
    traces: int
    spans: int
    by_kind: dict[str, SpanKindSummary]
    slowest_traces: tuple[tuple[str, float], ...]


def _attr_value(raw: dict[str, object]) -> str:
    value = raw.get("value", {})
    if isinstance(value, dict):
        for key in ("stringValue", "intValue", "doubleValue", "boolValue"):
            if key in value:
                return str(value[key])
    return ""


def classify_span(name: str, attributes: dict[str, str]) -> str:
    op = attributes.get("gen_ai.operation.name", "")
    lowered = name.lower()
    if (
        op in {"chat", "text_completion", "generate_content"}
        or "gen_ai.request.model" in attributes
    ):
        return "model"
    if (
        op == "execute_tool"
        or "tool.name" in attributes
        or lowered.startswith(("tool", "execute_tool"))
    ):
        return "tool"
    if "retrieval" in lowered or "retriever" in lowered or "db.vector" in " ".join(attributes):
        return "retrieval"
    if lowered.startswith(("llm", "chat", "completion")):
        return "model"
    return "other"


def parse_otlp_json(path: Path) -> list[Span]:
    """Parse ``{"resourceSpans": [{"scopeSpans": [{"spans": [...]}]}]}``."""
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        raise ConfigurationError(f"cannot read OTLP JSON {path}: {exc}") from exc
    spans: list[Span] = []
    for resource in data.get("resourceSpans", []):
        for scope in resource.get("scopeSpans", []):
            for raw in scope.get("spans", []):
                attributes = {
                    str(a.get("key")): _attr_value(a)
                    for a in raw.get("attributes", [])
                    if not any(k in str(a.get("key", "")) for k in _CONTENT_KEYS)
                }
                start, end = (
                    int(raw.get("startTimeUnixNano", 0)),
                    int(raw.get("endTimeUnixNano", 0)),
                )
                status = raw.get("status", {}) or {}
                spans.append(
                    Span(
                        trace_id=str(raw.get("traceId", "")),
                        span_id=str(raw.get("spanId", "")),
                        name=str(raw.get("name", "")),
                        kind=classify_span(str(raw.get("name", "")), attributes),
                        duration_ms=max(0, end - start) / 1e6,
                        error=status.get("code") in (2, "STATUS_CODE_ERROR"),
                        attributes=attributes,
                    )
                )
    return spans


def analyze_spans(spans: list[Span]) -> TraceAnalysis:
    by_kind: dict[str, list[Span]] = defaultdict(list)
    per_trace: dict[str, float] = defaultdict(float)
    for span in spans:
        by_kind[span.kind].append(span)
        per_trace[span.trace_id] = max(per_trace[span.trace_id], span.duration_ms)
    summaries = {
        kind: SpanKindSummary(
            kind=kind,
            count=len(group),
            errors=sum(s.error for s in group),
            error_rate=sum(s.error for s in group) / len(group),
            p50_ms=percentile([s.duration_ms for s in group], 50),
            p95_ms=percentile([s.duration_ms for s in group], 95),
        )
        for kind, group in sorted(by_kind.items())
    }
    slowest = tuple(sorted(per_trace.items(), key=lambda kv: -kv[1])[:5])
    return TraceAnalysis(
        traces=len(per_trace), spans=len(spans), by_kind=summaries, slowest_traces=slowest
    )
