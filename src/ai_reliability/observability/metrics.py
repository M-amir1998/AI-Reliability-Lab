"""Optional Prometheus and OpenTelemetry instrumentation of the evaluation runner itself.

These observe the *evaluation infrastructure* (tests completed, failures,
concurrency, endpoint errors, tokens, run duration) — not the model. Both
backends are optional extras (``ai-reliability[observability]``); importing
this module without them installed is safe and the factories raise a clear error.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from ai_reliability.domain.enums import Outcome
from ai_reliability.domain.result import CaseResult
from ai_reliability.errors import ConfigurationError


def _require(module: str) -> Any:
    try:
        return __import__(module, fromlist=["_"])
    except ImportError as exc:
        raise ConfigurationError(
            f"{module} is not installed; install with `pip install ai-reliability[observability]`"
        ) from exc


class PrometheusObserver:
    """Exposes runner metrics on an HTTP endpoint (or a private registry for tests)."""

    def __init__(self, *, system_id: str, suite_id: str, registry: Any | None = None) -> None:
        prom = _require("prometheus_client")
        self.registry = registry if registry is not None else prom.CollectorRegistry()
        labels = ("system", "suite")
        self._labels = {"system": system_id, "suite": suite_id}
        self.completed = prom.Counter(
            "aireliability_tests_completed_total",
            "Executions completed",
            labels,
            registry=self.registry,
        )
        self.failed = prom.Counter(
            "aireliability_tests_failed_total",
            "Executions not passing",
            (*labels, "outcome"),
            registry=self.registry,
        )
        self.endpoint_errors = prom.Counter(
            "aireliability_endpoint_errors_total",
            "System/transport errors",
            (*labels, "code"),
            registry=self.registry,
        )
        self.tokens = prom.Counter(
            "aireliability_tokens_total",
            "Reported tokens",
            (*labels, "direction"),
            registry=self.registry,
        )
        self.in_flight = prom.Gauge(
            "aireliability_concurrency_current",
            "Executions in flight",
            labels,
            registry=self.registry,
        )
        self.duration = prom.Gauge(
            "aireliability_run_duration_seconds",
            "Duration of the last run",
            labels,
            registry=self.registry,
        )
        self.latency = prom.Histogram(
            "aireliability_case_latency_ms",
            "Per-execution latency (ms)",
            labels,
            buckets=(50, 100, 250, 500, 1000, 2500, 5000, 10000, 30000),
            registry=self.registry,
        )
        self.judge_calls = prom.Counter(
            "aireliability_judge_calls_total", "LLM judge calls", labels, registry=self.registry
        )

    def serve(self, port: int, addr: str = "127.0.0.1") -> None:
        _require("prometheus_client").start_http_server(port, addr=addr, registry=self.registry)

    def on_run_started(self, run_id: str, total: int, skipped: int) -> None:
        return None

    def on_case_started(self, run_id: str, test_id: str, in_flight: int) -> None:
        self.in_flight.labels(**self._labels).set(in_flight)

    def on_case_finished(self, run_id: str, result: CaseResult, in_flight: int) -> None:
        self.completed.labels(**self._labels).inc()
        self.in_flight.labels(**self._labels).set(in_flight)
        if result.outcome is not Outcome.PASS:
            self.failed.labels(**self._labels, outcome=result.outcome.value).inc()
        if result.error_code is not None:
            self.endpoint_errors.labels(**self._labels, code=result.error_code.value).inc()
        if result.latency_ms is not None:
            self.latency.labels(**self._labels).observe(result.latency_ms)
        self.tokens.labels(**self._labels, direction="input").inc(result.input_tokens or 0)
        self.tokens.labels(**self._labels, direction="output").inc(result.output_tokens or 0)
        judged = sum(1 for e in result.evaluations if "judge_model" in e.details)
        if judged:
            self.judge_calls.labels(**self._labels).inc(judged)

    def on_run_finished(self, run_id: str, duration_s: float, interrupted: bool) -> None:
        self.duration.labels(**self._labels).set(duration_s)


class OTelObserver:
    """Emits one span per execution under a run span (requires an SDK tracer provider)."""

    def __init__(self, tracer: Any | None = None) -> None:
        trace = _require("opentelemetry.trace")
        self._tracer = tracer or trace.get_tracer("ai_reliability.runner")
        self._run_span: Any | None = None
        self.finished = 0

    def on_run_started(self, run_id: str, total: int, skipped: int) -> None:
        self._run_span = self._tracer.start_span(
            "evaluation_run", attributes={"run.id": run_id, "run.total": total}
        )

    def on_case_started(self, run_id: str, test_id: str, in_flight: int) -> None:
        return None

    def on_case_finished(self, run_id: str, result: CaseResult, in_flight: int) -> None:
        # Attributes are metadata only: never prompt or response text.
        span = self._tracer.start_span(
            "evaluate_case",
            attributes={
                "run.id": run_id,
                "test.id": result.test_id,
                "test.category": result.category,
                "eval.outcome": result.outcome.value,
                "eval.latency_ms": result.latency_ms or 0.0,
                "eval.attempts": result.attempts,
            },
        )
        span.end()
        self.finished += 1

    def on_run_finished(self, run_id: str, duration_s: float, interrupted: bool) -> None:
        if self._run_span is not None:
            self._run_span.set_attribute("run.interrupted", interrupted)
            self._run_span.end()


def summaries_exposition(results_dir: Path) -> str:
    """Prometheus text exposition of the latest stored run summaries (for static scraping)."""
    lines = [
        "# HELP aireliability_run_pass_rate Pass rate of a stored evaluation run",
        "# TYPE aireliability_run_pass_rate gauge",
    ]
    for summary_path in sorted(results_dir.glob("*/summary.json")):
        data = json.loads(summary_path.read_text(encoding="utf-8"))
        manifest = json.loads((summary_path.parent / "manifest.json").read_text(encoding="utf-8"))
        value = (data.get("metrics", {}).get("pass_rate") or {}).get("value")
        if value is None:
            continue
        system = str(manifest["system"]["system_id"]).replace('"', "")
        lines.append(
            f'aireliability_run_pass_rate{{run_id="{data["run_id"]}",system="{system}",suite="{manifest["suite_id"]}"}} {value}'
        )
    return "\n".join(lines) + "\n"
