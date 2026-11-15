"""Run-level reliability metrics. Every metric is a plain, documented ratio or statistic.

There is deliberately no single composite "reliability score": component
metrics are primary and gates are applied to them individually.
"""

from __future__ import annotations

from collections import Counter, defaultdict
from collections.abc import Iterable, Sequence

from ai_reliability.domain.enums import ErrorCode, Outcome, Severity
from ai_reliability.domain.result import CaseResult, EvaluationResult, MetricResult
from ai_reliability.domain.run import CategorySummary, RunSummary
from ai_reliability.stats import percentile, wilson_interval

EVALUATOR_GROUPS: dict[str, tuple[str, ...]] = {
    "structured_output_validity": ("json_valid", "json_schema"),
    "injection_resistance_rate": ("injection_resistance", "canary_leak"),
    "citation_validity": ("citation",),
    "tool_policy_compliance": ("tool_policy", "tool_trace"),
    "groundedness_rate": ("groundedness",),
}

METRIC_DESCRIPTIONS: dict[str, str] = {
    "pass_rate": "passed executions / all executions",
    "verified_accuracy": "passed / executions with a definitive verdict (excludes NEEDS_REVIEW, SKIPPED)",
    "test_pass_rate": "tests passing in every repetition / unique tests",
    "critical_failure_rate": "non-passing CRITICAL executions / CRITICAL executions",
    "critical_failure_count": "number of non-passing CRITICAL executions",
    "system_error_rate": "executions ending in a system/transport error / all executions",
    "timeout_rate": "executions ending in timeout / all executions",
    "needs_review_rate": "executions routed to human review / all executions",
    "retry_rate": "executions needing more than one attempt / all executions",
    "consistency_rate": "tests with identical outcomes across repetitions / unique tests",
    "structured_output_validity": "pass rate of json_valid + json_schema evaluations",
    "injection_resistance_rate": "pass rate of injection_resistance + canary_leak evaluations",
    "citation_validity": "pass rate of citation evaluations",
    "tool_policy_compliance": "pass rate of tool_policy + tool_trace evaluations",
    "groundedness_rate": "pass rate of groundedness evaluations",
}


def _flatten(evaluations: Iterable[EvaluationResult]) -> Iterable[EvaluationResult]:
    for e in evaluations:
        yield e
        yield from _flatten(e.components)


def _rate(name: str, successes: int, n: int, *, ci: bool = True) -> MetricResult:
    if n == 0:
        return MetricResult(
            name=name, value=None, n=0, description=METRIC_DESCRIPTIONS.get(name, "")
        )
    low, high = wilson_interval(successes, n) if ci else (None, None)
    return MetricResult(
        name=name,
        value=successes / n,
        n=n,
        ci_low=low,
        ci_high=high,
        unit="ratio",
        description=METRIC_DESCRIPTIONS.get(name, ""),
    )


def _stat(name: str, value: float | None, n: int, unit: str, description: str) -> MetricResult:
    return MetricResult(name=name, value=value, n=n, unit=unit, description=description)


def summarize(run_id: str, results: Sequence[CaseResult]) -> RunSummary:
    """Aggregate case results into run metrics, category summaries and failure counts."""
    n = len(results)
    passed = sum(r.outcome is Outcome.PASS for r in results)
    definitive = [r for r in results if r.outcome in {Outcome.PASS, Outcome.FAIL, Outcome.ERROR}]
    errors = [r for r in results if r.outcome is Outcome.ERROR]
    timeouts = sum(r.error_code is ErrorCode.TIMEOUT for r in errors)
    critical = [r for r in results if r.severity is Severity.CRITICAL]
    critical_failed = [r for r in critical if r.outcome is not Outcome.PASS]

    by_test: dict[str, list[CaseResult]] = defaultdict(list)
    for r in results:
        by_test[r.test_id].append(r)
    tests_all_pass = sum(all(x.outcome is Outcome.PASS for x in rs) for rs in by_test.values())
    consistent = sum(len({x.outcome for x in rs}) == 1 for rs in by_test.values())

    metrics: dict[str, MetricResult] = {
        "pass_rate": _rate("pass_rate", passed, n),
        "verified_accuracy": _rate(
            "verified_accuracy", sum(r.outcome is Outcome.PASS for r in definitive), len(definitive)
        ),
        "test_pass_rate": _rate("test_pass_rate", tests_all_pass, len(by_test)),
        "critical_failure_rate": _rate(
            "critical_failure_rate", len(critical_failed), len(critical)
        ),
        "critical_failure_count": _stat(
            "critical_failure_count",
            float(len(critical_failed)),
            len(critical),
            "count",
            METRIC_DESCRIPTIONS["critical_failure_count"],
        ),
        "system_error_rate": _rate("system_error_rate", len(errors) - timeouts, n),
        "timeout_rate": _rate("timeout_rate", timeouts, n),
        "needs_review_rate": _rate(
            "needs_review_rate", sum(r.outcome is Outcome.NEEDS_REVIEW for r in results), n
        ),
        "retry_rate": _rate("retry_rate", sum(r.attempts > 1 for r in results), n, ci=False),
    }
    if any(len(rs) > 1 for rs in by_test.values()):
        metrics["consistency_rate"] = _rate("consistency_rate", consistent, len(by_test))

    evaluations = [e for r in results for e in _flatten(r.evaluations)]
    for metric_name, evaluator_names in EVALUATOR_GROUPS.items():
        group = [
            e
            for e in evaluations
            if e.evaluator in evaluator_names and e.outcome is not Outcome.SKIPPED
        ]
        if group:
            metrics[metric_name] = _rate(
                metric_name, sum(e.outcome is Outcome.PASS for e in group), len(group)
            )

    latencies = [r.latency_ms for r in results if r.latency_ms is not None]
    for q in (50, 95, 99):
        metrics[f"p{q}_latency_ms"] = _stat(
            f"p{q}_latency_ms",
            percentile(latencies, q),
            len(latencies),
            "ms",
            f"{q}th percentile end-to-end latency (as reported or measured)",
        )
    metrics["mean_latency_ms"] = _stat(
        "mean_latency_ms",
        sum(latencies) / len(latencies) if latencies else None,
        len(latencies),
        "ms",
        "mean end-to-end latency",
    )
    ttfts = [r.ttft_ms for r in results if r.ttft_ms is not None]
    if ttfts:
        metrics["p50_ttft_ms"] = _stat(
            "p50_ttft_ms",
            percentile(ttfts, 50),
            len(ttfts),
            "ms",
            "median time to first token (if reported)",
        )
    out_tokens = [r.output_tokens for r in results if r.output_tokens is not None]
    in_tokens = [r.input_tokens for r in results if r.input_tokens is not None]
    if out_tokens:
        metrics["mean_output_tokens"] = _stat(
            "mean_output_tokens",
            sum(out_tokens) / len(out_tokens),
            len(out_tokens),
            "tokens",
            "mean output tokens (as reported by the system)",
        )
    if in_tokens:
        metrics["total_tokens"] = _stat(
            "total_tokens",
            float(sum(in_tokens) + sum(out_tokens)),
            len(in_tokens),
            "tokens",
            "total reported input + output tokens",
        )
    costs = [r.cost_usd for r in results if r.cost_usd is not None]
    if costs:
        metrics["total_cost_usd"] = _stat(
            "total_cost_usd",
            sum(costs),
            len(costs),
            "usd",
            "estimated cost from configured pricing",
        )
        metrics["mean_cost_usd"] = _stat(
            "mean_cost_usd",
            sum(costs) / len(costs),
            len(costs),
            "usd",
            "mean estimated cost per execution",
        )
    metrics["mean_response_chars"] = _stat(
        "mean_response_chars",
        sum(r.response_chars for r in results) / n if n else None,
        n,
        "chars",
        "mean response length in characters",
    )

    categories: dict[str, CategorySummary] = {}
    by_category: dict[str, list[CaseResult]] = defaultdict(list)
    for r in results:
        by_category[r.category].append(r)
    for category, rs in sorted(by_category.items()):
        cat_pass = sum(x.outcome is Outcome.PASS for x in rs)
        low, high = wilson_interval(cat_pass, len(rs))
        categories[category] = CategorySummary(
            category=category,
            total=len(rs),
            passed=cat_pass,
            failed=sum(x.outcome is Outcome.FAIL for x in rs),
            errors=sum(x.outcome is Outcome.ERROR for x in rs),
            pass_rate=cat_pass / len(rs),
            ci_low=low,
            ci_high=high,
        )

    failure_counts = Counter(
        r.failure_category.value
        for r in results
        if r.outcome is not Outcome.PASS and r.failure_category
    )
    return RunSummary(
        run_id=run_id,
        total_executions=n,
        unique_tests=len(by_test),
        metrics=metrics,
        categories=categories,
        failure_categories=dict(sorted(failure_counts.items())),
        critical_failures=tuple(sorted({r.test_id for r in critical_failed})),
    )
